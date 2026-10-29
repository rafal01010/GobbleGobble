import json
from dataclasses import replace

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError
from test_evidence_storage import fixture_collection
from test_research_agent import model_response, setup_values
from test_research_tools import tools

from app.agents.research import invoke_review_tool, prepare_review_tool
from app.agents.research_budget import ResearchBudget
from app.core.calls import AttemptCalls, Bounds
from app.db.evidence import lookup_evidence, store_evidence
from app.db.ownership import create_search, issue_session
from app.db.research_snapshots import (
    SnapshotCandidate,
    SnapshotGap,
    store_snapshot,
    view_snapshot,
)
from app.db.session import transaction
from app.research_tools import TextInput
from app.schemas.evidence import EvidenceCollection

pytest_plugins = ["test_research_tools"]


def finding(label, topic, polarity, text):
    return {
        "review_label": label,
        "topic": topic,
        "polarity": polarity,
        "wording": "quotation",
        "statement": text,
        "basis": None,
    }


def tool_response(call_id, evidence_id):
    return {
        "id": "r1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4o-mini",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "id": "f" + call_id,
                "call_id": call_id,
                "name": "read_review_evidence",
                "arguments": json.dumps({"item_id": evidence_id, "offset": 0}),
                "status": "completed",
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["truncated_json", "incomplete"])
async def test_failed_sdk_response_preserves_completed_sibling(failure):
    settings, request, candidate = setup_values()
    good = model_response(
        {
            "findings": [finding("r0", "service", "negative", "the service was slow")],
            "gaps": [],
        }
    )
    bad = model_response({"findings": [], "gaps": []})
    if failure == "truncated_json":
        bad["output"][0]["content"][0]["text"] = '{"findings":['
    else:
        bad["status"] = "incomplete"
        bad["incomplete_details"] = {"reason": "max_output_tokens"}
    responses = iter([good, bad])
    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=next(responses))
            )
        ),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        prepared = prepare_review_tool(calls, client, request, candidate)
        first = await invoke_review_tool(prepared, "Assess review")
        second = await invoke_review_tool(
            prepared, "Assess review again", prior=first.contributions
        )
    assert first.status == "available"
    assert (
        first.contributions[0].proposals[0].finding.statement == "the service was slow"
    )
    assert second.status == "incomplete"
    assert second.reason is not None
    assert second.contributions == first.contributions
    assert len(calls.receipts) == 2


@pytest.mark.asyncio
async def test_multiple_sdk_tool_pairs_keep_review_lookup_and_sibling(setup):
    settings, request, candidate = setup_values()
    assert candidate.sample is not None
    review = replace(
        candidate.sample.reviews[0],
        review_id="secret",
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
    )
    later = replace(review, review_id="review_0")
    candidate = replace(
        candidate, sample=replace(candidate.sample, reviews=(review, later))
    )
    budget = ResearchBudget()
    seen = []

    def respond(req):
        seen.append(json.loads(req.content))
        if len(seen) == 1:
            return httpx2.Response(
                200,
                json=model_response(
                    {
                        "findings": [
                            finding("r0", "food", "positive", "chicken was excellent")
                        ],
                        "gaps": [],
                    }
                ),
            )
        if len(seen) == 2:
            return httpx2.Response(200, json=tool_response("call1", "ev_google"))
        if len(seen) == 3:
            budget.context_limit = len(req.content) + budget.headroom + 1024 + 100
            return httpx2.Response(200, json=tool_response("call2", "ev_google_0"))
        return httpx2.Response(
            200,
            json=model_response(
                {
                    "findings": [
                        finding("r0", "service", "negative", "service was slow")
                    ],
                    "gaps": [],
                }
            ),
        )

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(
            settings, Bounds(max_requests=5, total_seconds=5.0), research_budget=budget
        )
        prepared = prepare_review_tool(
            calls, client, request, candidate, research_tools=tools(setup)
        )
        first = await invoke_review_tool(prepared, "Positive food")
        second = await invoke_review_tool(
            prepared, "Check both reviews", prior=first.contributions
        )
    assert first.status == "available"
    assert second.status == "available"
    assert (
        second.contributions[0].proposals[0].finding.statement
        == "chicken was excellent"
    )
    assert second.contributions[1].proposals[0].finding.statement == "service was slow"
    assert budget.records[-1].deferred_tool_results >= 1
    history = seen[-1]["input"]
    calls_by_id = {
        item["call_id"] for item in history if item.get("type") == "function_call"
    }
    outputs_by_id = {
        item["call_id"]
        for item in history
        if item.get("type") == "function_call_output"
    }
    assert calls_by_id == outputs_by_id
    assert "ev_google" in json.dumps(history)
    retrieved = tools(setup).read_ephemeral_evidence(
        TextInput(place_id="branch_a", item_id="ev_google", offset=0)
    )
    assert (
        "chicken was excellent" in retrieved["statement"]
        and "service was slow" in retrieved["statement"]
    )


def test_branch_menu_price_complaint_and_shared_source_lookup(setup):
    conn, _, _, _ = setup
    raw = fixture_collection()
    ambiguous = dict(raw["sources"][0])
    ambiguous["source_id"] = "src_ambiguous"
    raw["sources"].append(ambiguous)
    raw["evidence"][1]["statement"] = (
        "Branch B serves soup and a diner said it arrived cold"
    )
    price = dict(raw["evidence"][1])
    price["evidence_id"] = "ev_price_b"
    price["statement"] = "Branch B soup costs PHP 180"
    raw["evidence"].append(price)
    raw["food_claims"].append(
        {
            "claim_id": "price_b",
            "status": "sourced",
            "topic": "price",
            "statement": "Soup costs PHP 180",
            "subjects": [{"place_id": "branch_b", "evidence_ids": ["ev_price_b"]}],
        }
    )
    raw["review_assessments"] = [
        {
            "assessment_id": "b",
            "place_id": "branch_b",
            "google": {"status": "no_match", "reason": "not matched"},
            "fallback": {
                "kind": "food_evidence",
                "source_id": "src_shared",
                "reason": "no_match",
                "evidence_ids": ["ev_b"],
                "findings": [
                    {
                        "topic": "complaint",
                        "statement": "B's soup was cold",
                        "subjects": [
                            {"place_id": "branch_b", "evidence_ids": ["ev_b"]}
                        ],
                    }
                ],
            },
        }
    ]
    for location in ("menu", "price", "complaint"):
        invalid = json.loads(json.dumps(raw))
        if location == "menu":
            invalid["food_claims"][0]["subjects"][0]["evidence_ids"] = ["ev_b"]
        elif location == "price":
            invalid["food_claims"][-1]["subjects"][0]["evidence_ids"] = ["ev_a"]
        else:
            invalid["review_assessments"][0]["fallback"]["findings"][0]["subjects"][0][
                "evidence_ids"
            ] = ["ev_a"]
        with pytest.raises(ValidationError):
            EvidenceCollection.model_validate_json(json.dumps(invalid))
    collection = EvidenceCollection.model_validate_json(json.dumps(raw))
    with transaction(conn):
        principal = issue_session(conn).principal
        search = create_search(conn, principal)
        store_evidence(
            conn,
            search,
            collection,
            source_classes={
                "src_shared": "permitted_independent",
                "src_ambiguous": "permitted_independent",
                "src_google": "public_google_reviews",
            },
        )
        store_snapshot(
            conn,
            search,
            "ambiguous",
            candidates=(
                SnapshotCandidate("branch_a", "branch"),
                SnapshotCandidate("branch_b", "branch"),
            ),
            findings=(),
            gaps=(
                SnapshotGap("branch_a", "omitted_material"),
                SnapshotGap("branch_b", "omitted_material"),
            ),
        )
    a = lookup_evidence(conn, search, "branch_a")
    b = lookup_evidence(conn, search, "branch_b")
    assert [(x.statement, x.evidence_ids) for x in a.findings] == [
        ("Noodles", ("ev_a",)),
        ("Tea", ("ev_shared",)),
    ]
    assert [(x.statement, x.evidence_ids) for x in b.findings] == [
        ("B's soup was cold", ("ev_b",)),
        ("Soup", ("ev_b",)),
        ("Tea", ("ev_shared",)),
        ("Soup costs PHP 180", ("ev_price_b",)),
    ]
    assert any(x.source_id == "src_shared" for x in a.sources) and any(
        x.source_id == "src_shared" for x in b.sources
    )
    assert any(x.source_id == "src_ambiguous" for x in a.sources)
    assert any(x.source_id == "src_ambiguous" for x in b.sources)
    assert [
        (e.evidence_id, e.statement) for e in a.evidence if e.evidence_id == "ev_a"
    ] == [("ev_a", "A serves noodles")]
    assert [
        (e.evidence_id, e.statement)
        for e in b.evidence
        if e.evidence_id in {"ev_b", "ev_price_b"}
    ] == [
        ("ev_b", "Branch B serves soup and a diner said it arrived cold"),
        ("ev_price_b", "Branch B soup costs PHP 180"),
    ]
    assert view_snapshot(conn, search, "ambiguous", limit=10).gaps == (
        SnapshotGap("branch_a", "candidate_reacquisition_required"),
        SnapshotGap("branch_a", "omitted_material"),
        SnapshotGap("branch_b", "candidate_reacquisition_required"),
        SnapshotGap("branch_b", "omitted_material"),
    )
    assert {e.evidence_id for e in a.evidence} == {"ev_a", "ev_shared"}
    assert {e.evidence_id for e in b.evidence} == {"ev_b", "ev_shared", "ev_price_b"}
    assert tools(setup).read_ephemeral_evidence(
        TextInput(place_id="branch_b", item_id="ev_google")
    ) == {"status": "unavailable"}


@pytest.mark.asyncio
async def test_two_branch_sdk_results_keep_review_identity():
    settings, request, candidate_a = setup_values()
    assert candidate_a.sample is not None
    review_b = replace(
        candidate_a.sample.reviews[0],
        review_id="review-b",
        text="The soup was cold.",
        source_url="https://maps.google.com/b",
    )
    branch_b = replace(
        candidate_a.sample.branch,
        feature_id="0x3:0x4",
        source_url="https://maps.google.com/b",
        name="Branch B",
    )
    candidate_b = replace(
        candidate_a,
        place_id="branch_b",
        sample=replace(candidate_a.sample, branch=branch_b, reviews=(review_b,)),
    )
    responses = iter(
        [
            model_response(
                {
                    "findings": [
                        finding("r0", "food", "positive", "chicken was excellent")
                    ],
                    "gaps": [],
                }
            ),
            model_response(
                {
                    "findings": [
                        finding("r0", "complaint", "negative", "soup was cold")
                    ],
                    "gaps": [],
                }
            ),
            model_response(
                {
                    "findings": [
                        finding("r1", "complaint", "negative", "soup was cold")
                    ],
                    "gaps": [],
                }
            ),
        ]
    )
    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda req: httpx2.Response(200, json=next(responses))
            )
        ),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=4, total_seconds=5.0))
        a = await invoke_review_tool(
            prepare_review_tool(calls, client, request, candidate_a), "Branch A"
        )
        prepared_b = prepare_review_tool(calls, client, request, candidate_b)
        b = await invoke_review_tool(prepared_b, "Branch B", prior=a.contributions)
        swapped = await invoke_review_tool(
            prepared_b, "Branch B", prior=b.contributions
        )
    assert b.status == "available"
    assert [
        (
            x.place_id,
            x.source_url,
            x.proposals[0].review.review_id,
            x.proposals[0].finding.statement,
        )
        for x in b.contributions
    ] == [
        ("branch_a", "https://maps.google.com/a", "review-a", "chicken was excellent"),
        ("branch_b", "https://maps.google.com/b", "review-b", "soup was cold"),
    ]
    assert swapped.status == "incomplete" and swapped.contributions == b.contributions
