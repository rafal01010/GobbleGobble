import json

import httpx2
import pytest
from openai import AsyncOpenAI
from test_owner_draft import context, draft
from test_research_agent import model_response, setup_values

from app.agents.research_budget import ResearchBudget
from app.core.calls import AttemptCalls, Bounds


def safe_draft(collection):
    value = draft(collection).model_dump()
    for item in (*value["primaries"], *value["runners_up"]):
        item["food_claim_ids"] = []
    return value


def synthetic_response(output):
    response = model_response(output)
    response["model"] = "gpt-4o"
    return response


def tool_call(name, arguments, *, call_id="call_1"):
    return {
        "id": "response_1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4o",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "id": "tool_1",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
                "status": "completed",
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


def harness(
    responses,
    setup,
    *,
    max_requests=8,
    budget=None,
    context_limit_after_first=None,
    evidence_variant=None,
    on_request=None,
):
    settings, _, _ = setup_values()
    raw = context().model_dump(mode="json")
    raw["search_id"] = setup[1].search_id
    ephemeral = setup[3].model_dump(mode="json")
    evidence = raw["snapshots"][0]["evidence"]
    evidence["sources"].append(
        next(s for s in ephemeral["sources"] if s["source_id"] == "src_google")
    )
    evidence["evidence"].append(
        next(e for e in ephemeral["evidence"] if e["evidence_id"] == "ev_google")
    )
    claim = next(c for c in evidence["food_claims"] if c["claim_id"] == "menu_a")
    claim["subjects"][0]["evidence_ids"] = ["ev_google"]
    if evidence_variant == "stale":
        review = next(
            e for e in evidence["evidence"] if e["evidence_id"] == "ev_google"
        )
        review["freshness"]["fresh_until"] = "2026-10-29T12:00:00Z"
    elif evidence_variant == "restricted":
        source = next(s for s in evidence["sources"] if s["source_id"] == "src_google")
        source["policy"]["model_input"]["decision"] = "prohibited"
    elif evidence_variant == "derived_denied":
        source = next(s for s in evidence["sources"] if s["source_id"] == "src_google")
        source["policy"]["derived_output"]["decision"] = "prohibited"
    elif evidence_variant == "sequence":
        import copy

        source = next(s for s in evidence["sources"] if s["source_id"] == "src_google")
        source_b = copy.deepcopy(source)
        source_b["source_id"] = "src_google_b"
        source_b["subject_place_ids"] = ["branch_b"]
        source_b["origin"]["branch"]["feature_id"] = "0x3:0x4"
        evidence["sources"].append(source_b)
        review = next(
            e for e in evidence["evidence"] if e["evidence_id"] == "ev_google"
        )
        later = copy.deepcopy(review)
        later["evidence_id"] = "ev_google_later"
        later["source_id"] = "src_google_b"
        later["subject_place_ids"] = ["branch_b"]
        later["statement"] = "The soup was cold."
        later["locator"]["branch"]["feature_id"] = "0x3:0x4"
        later["locator"]["review_id"] = "secret_b"
        later["locator"]["source_url"] = "https://google.com/SECRET_REVIEW_B"
        later["freshness"]["fresh_until"] = "2026-11-01T00:00:00Z"
        evidence["evidence"].append(later)
    from app.schemas.recommendations import RecommendationCollection

    collection = RecommendationCollection.model_validate_json(json.dumps(raw))
    captured = []
    pending = iter(responses)

    def reply(request):
        captured.append(json.loads(request.content))
        if on_request is not None:
            on_request(len(captured))
        if len(captured) == 1 and context_limit_after_first is not None:
            calls.research_budget.context_limit = context_limit_after_first
        value = next(pending)
        return (
            value
            if isinstance(value, httpx2.Response)
            else httpx2.Response(200, json=value)
        )

    client = AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(reply)),
        max_retries=0,
    )
    calls = AttemptCalls(
        settings,
        Bounds(max_requests=max_requests, total_seconds=10.0),
        research_budget=budget or ResearchBudget(),
    )
    return collection, draft(collection), calls, client, captured


pytest_plugins = ["test_research_tools"]


def prepared_owner(
    collection,
    calls,
    client,
    setup,
    *,
    oversized=False,
    fake_result=None,
    specialist=None,
    specialists=None,
    clock=None,
):
    from datetime import UTC, datetime

    from app.agents.owner import prepare_owner
    from app.db.research_snapshots import SnapshotCandidate, store_snapshot
    from app.db.session import transaction
    from app.providers.geoapify import GeoapifyClient
    from app.research_tools import ResearchTools

    conn, search, _, _ = setup
    snapshot = collection.snapshots[0]
    evidence = snapshot.evidence
    places = (*snapshot.discovery_leads, *snapshot.researched_candidates)
    with transaction(conn):
        store_snapshot(
            conn,
            search,
            snapshot.snapshot_id,
            candidates=tuple(
                SnapshotCandidate(place.place_id, place.kind) for place in places
            ),
            findings=(),
        )
    research_tools = ResearchTools(
        conn,
        search,
        snapshot.snapshot_id,
        place_ids=tuple(p.place_id for p in places),
        clock=clock or (lambda: datetime(2026, 10, 30, tzinfo=UTC)),
        max_content_bytes=1024,
        ephemeral=evidence,
        source_classes={
            "src_google": "public_google_reviews",
            "src_google_b": "public_google_reviews",
        },
    )
    if oversized:
        fake_result = {"status": "available", "statement": "oversized " * 4000}
    if fake_result is not None:
        research_tools.read_ephemeral_evidence = (
            fake_result if callable(fake_result) else lambda request: fake_result
        )
    return prepare_owner(
        calls,
        client,
        collection.request,
        snapshot,
        research_tools,
        GeoapifyClient(calls),
        specialists
        if specialists is not None
        else ({} if specialist is None else {"branch_a": specialist}),
        candidate_sources={place.place_id: "geoapify_geography" for place in places},
        max_output_tokens=1024,
    )


@pytest.mark.asyncio
async def test_closed_research_composes_with_original_constraints_and_no_tools(setup):
    from app.agents.owner_execution import execute_owner

    example = context()
    final = safe_draft(example)
    collection, _, calls, client, captured = harness([synthetic_response(final)], setup)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
        )
    assert result.status == "completed"
    assert result.research_closed is True
    assert result.recovery_attempted is False
    assert [x.place_id for x in result.collection.versions[0].primaries] == [
        "branch_a",
        "branch_b",
        "branch_c",
    ]
    assert result.collection.request == collection.request
    assert len(captured) == 1
    assert not captured[0].get("tools")
    model_input = json.dumps(captured[0]["input"])
    assert collection.request.origin.location.timezone in model_input
    assert str(collection.request.travel.minutes) in model_input
    assert "branch_h" in model_input


@pytest.mark.asyncio
async def test_invalid_final_gets_one_tools_free_recovery(setup):
    from app.agents.owner_execution import execute_owner

    example = context()
    bad = synthetic_response({"bad": True})
    good = synthetic_response(safe_draft(example))
    collection, _, calls, client, captured = harness([bad, good], setup)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
        )
    assert result.status == "completed"
    assert result.recovery_attempted is True
    assert result.collection.versions[0].primaries[0].food_claim_ids == ()
    assert len(captured) == 2
    assert all(not item.get("tools") for item in captured)
    assert len(calls.receipts) == 2


@pytest.mark.asyncio
async def test_repeated_invalid_final_cannot_publish_or_retry_again(setup):
    from app.agents.owner_execution import execute_owner

    collection, _, calls, client, captured = harness(
        [synthetic_response({"bad": True}), synthetic_response({"bad": True})], setup
    )
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
        )
    assert result.status == "technical_failure"
    assert result.collection is None
    assert result.recovery_attempted is True
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_valid_final_at_output_cap_is_accepted(setup):
    from app.agents.owner_execution import execute_owner

    example = context()
    full = synthetic_response(safe_draft(example))
    full["usage"]["output_tokens"] = 1024
    full["usage"]["total_tokens"] = 1034
    collection, _, calls, client, captured = harness([full], setup)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
        )
    assert result.status == "completed"
    assert result.recovery_attempted is False
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_research_closure_retains_source_evidence_and_late_alternative(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    output["runners_up"][-1] = {
        "place_id": "branch_h",
        "why_to_go": "A worthwhile alternative.",
        "food_claim_ids": [],
    }
    calls_to_model = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
        ),
        synthetic_response(output),
    ]
    collection, _, calls, client, captured = harness(calls_to_model, setup)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert result.research_closed is True
    assert (
        result.collection.versions[0].primaries[0].why_to_go
        == "Chicken was excellent, though service was slow."
    )
    assert result.collection.versions[0].runners_up[-1].place_id == "branch_h"
    assert result.collection.versions[0].runners_up[-1].food_claim_ids == ()
    assert len(captured) == 2
    assert not captured[-1].get("tools")
    evidence_and_context = json.dumps(captured[-1]["input"], ensure_ascii=False)
    assert "chicken was excellent" in evidence_and_context
    assert "service was slow" in evidence_and_context
    assert "branch_h" in evidence_and_context
    assert collection.request.origin.location.timezone in evidence_and_context
    assert str(collection.request.travel.minutes) in evidence_and_context
    assert sum(receipt.admitted for receipt in calls.receipts) == 2


@pytest.mark.asyncio
async def test_one_request_left_closes_research_and_composes(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    collection, _, calls, client, captured = harness(
        [synthetic_response(safe_draft(original))], setup, max_requests=1
    )
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=3,
        )
    assert result.status in {"completed", "partial"}
    assert result.research_closed is True
    assert not captured[0].get("tools")
    assert len(calls.receipts) == 1


@pytest.mark.asyncio
async def test_no_affordable_recovery_never_retries(setup):
    from app.agents.owner_execution import execute_owner

    collection, _, calls, client, captured = harness(
        [synthetic_response({"bad": True})], setup, max_requests=1
    )
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
        )
    assert result.status == "technical_failure"
    assert result.collection is None
    assert result.recovery_attempted is False
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_irreducible_context_never_calls_provider_or_invents_no_matches(setup):
    from app.agents.owner_execution import execute_owner

    collection, _, calls, client, captured = harness(
        [], setup, budget=ResearchBudget(context_limit=1024)
    )
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
        )
    assert result.status == "technical_failure"
    assert result.reason in {"context_limit", "context_or_reserve_unavailable"}
    assert result.collection is None
    assert captured == []


@pytest.mark.asyncio
async def test_provider_context_error_closes_research_and_composes(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    context_error = httpx2.Response(
        400,
        json={
            "error": {
                "message": "context too long",
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        },
    )
    collection, _, calls, client, captured = harness(
        [context_error, synthetic_response(safe_draft(original))], setup
    )
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=3,
        )
    assert result.status in {"completed", "partial"}
    assert result.research_closed is True
    assert result.collection.request == collection.request
    assert len(captured) == 2
    assert not captured[-1].get("tools")
    assert calls.receipts[0].diagnostic == "context_limit"
    assert calls.research_budget.records[0].outcome == "context_limit"
    assert calls.research_budget.records[0].actual_input is None
    assert calls.research_budget.charged > 0


@pytest.mark.asyncio
async def test_oversized_helper_becomes_gap_and_owner_still_composes(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    outputs = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
        ),
        synthetic_response(safe_draft(original)),
    ]
    collection, _, calls, client, captured = harness(outputs, setup)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup, oversized=True)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert result.collection.versions[0].primaries[0].food_claim_ids == ()
    assert result.retained[0]["result"] == {
        "status": "oversized",
        "reason": "tool_result_limit",
    }
    assert not captured[-1].get("tools")
    final_input = json.dumps(captured[-1]["input"])
    assert "tool_result_limit" in final_input
    assert "branch_h" in final_input
    assert "oversized " * 10 not in final_input


@pytest.mark.asyncio
async def test_context_pressure_reduces_review_body_but_keeps_decision_evidence(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    output["runners_up"][-1] = {
        "place_id": "branch_h",
        "why_to_go": "A worthwhile alternative.",
        "food_claim_ids": [],
    }
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
        ),
        synthetic_response(output),
    ]
    collection, _, calls, client, captured = harness(
        responses, setup, context_limit_after_first=10000
    )
    supplied = {
        "status": "available",
        "proposals": [
            {
                "review_id": "review-a",
                "statement": "chicken was excellent",
                "original_review": "long context " * 700,
            },
            {
                "review_id": "review-a",
                "statement": "service was slow",
                "original_review": "long context " * 700,
            },
        ],
    }
    async with client:
        prepared = prepared_owner(
            collection, calls, client, setup, fake_result=supplied
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert (
        result.collection.versions[0].primaries[0].why_to_go
        == "Chicken was excellent, though service was slow."
    )
    assert result.collection.versions[0].runners_up[-1].place_id == "branch_h"
    assert len(captured) == 2
    sent = json.dumps(captured[-1]["input"], ensure_ascii=False)
    assert "chicken was excellent" in sent and "service was slow" in sent
    assert "branch_h" in sent
    assert "deferred_review_context_ids" in sent and "review-a" in sent
    assert "review_context_omitted" in sent
    assert "long context " * 20 not in sent


@pytest.mark.asyncio
async def test_oversized_later_helper_preserves_earlier_usable_finding(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
            call_id="first",
        ),
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 1},
            call_id="second",
        ),
        synthetic_response(output),
    ]

    def evidence(request):
        if request.offset == 0:
            return {
                "status": "available",
                "place_id": "branch_a",
                "item_id": "ev_google",
                "statement": "The chicken was excellent, but service was slow.",
            }
        return {"status": "available", "statement": "oversized " * 4000}

    collection, _, calls, client, captured = harness(responses, setup)
    async with client:
        prepared = prepared_owner(
            collection, calls, client, setup, fake_result=evidence
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=2,
        )
    assert result.status in {"completed", "partial"}
    assert (
        result.retained[0]["result"]["statement"]
        == "The chicken was excellent, but service was slow."
    )
    assert result.retained[1]["result"] == {
        "status": "oversized",
        "reason": "tool_result_limit",
    }
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" in sent and "service was slow" in sent
    assert "tool_result_limit" in sent
    assert not captured[-1].get("tools")
    assert result.collection.versions[0].primaries[0].food_claim_ids == ()


@pytest.mark.asyncio
async def test_growing_context_does_not_spend_reserved_completion_capacity(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
        ),
        synthetic_response(output),
    ]
    budget = ResearchBudget(usage_limit=18000)
    collection, _, calls, client, captured = harness(responses, setup, budget=budget)
    supplied = {
        "status": "available",
        "proposals": [
            {
                "review_id": "review-a",
                "statement": "chicken was excellent",
                "original_review": "lengthy review " * 700,
            },
            {
                "review_id": "review-a",
                "statement": "service was slow",
                "original_review": "lengthy review " * 700,
            },
        ],
    }
    async with client:
        prepared = prepared_owner(
            collection, calls, client, setup, fake_result=supplied
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
            completion_usage=2048,
        )
    assert result.status in {"completed", "partial"}
    assert (
        result.collection.versions[0].primaries[0].why_to_go
        == "Chicken was excellent, though service was slow."
    )
    assert len(captured) == 2
    assert not captured[-1].get("tools")
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" in sent and "service was slow" in sent
    assert "review-a" in sent
    assert "lengthy review " * 20 not in sent
    assert budget.charged <= budget.usage_limit


@pytest.mark.asyncio
async def test_unaffordable_growth_defers_later_result_and_preserves_prior_evidence(
    setup,
):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
            call_id="first",
        ),
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google_0", "offset": 0},
            call_id="second",
        ),
        synthetic_response(output),
    ]

    def evidence(request):
        if request.item_id == "ev_google":
            return {
                "status": "available",
                "place_id": "branch_a",
                "source_id": "src_google",
                "evidence_id": "ev_google",
                "statement": "The chicken was excellent, but service was slow.",
            }
        return {
            "status": "available",
            "place_id": "branch_a",
            "source_id": "src_google",
            "evidence_id": "ev_google_0",
            "review_id": "review_0",
            "statement": "source detail " * 1200,
        }

    budget = ResearchBudget(usage_limit=30000)
    collection, _, calls, client, captured = harness(responses, setup, budget=budget)
    async with client:
        prepared = prepared_owner(
            collection, calls, client, setup, fake_result=evidence
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=4,
            completion_usage=2048,
        )
    assert result.status in {"completed", "partial"}
    assert result.research_closed is True
    assert len(captured) == 3
    assert not captured[-1].get("tools")
    assert (
        result.retained[0]["result"]["statement"]
        == "The chicken was excellent, but service was slow."
    )
    deferred = result.retained[1]["result"]
    assert deferred["status"] == "deferred"
    assert deferred["reason"] == "completion_reserve"
    assert {"branch_a", "src_google", "ev_google_0", "review_0"} <= set(
        deferred["references"]
    )
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" in sent and "service was slow" in sent
    assert "completion_reserve" in sent and "ev_google_0" in sent
    assert "source detail " * 20 not in sent
    assert result.collection.versions[0].primaries[0].food_claim_ids == ()
    assert budget.charged <= budget.usage_limit


@pytest.mark.asyncio
async def test_provider_final_context_error_recovers_once_after_reducing_source_context(
    setup,
):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    output["runners_up"][-1] = {
        "place_id": "branch_h",
        "why_to_go": "A worthwhile alternative.",
        "food_claim_ids": [],
    }
    context_error = httpx2.Response(
        400,
        json={
            "error": {
                "message": "context too long",
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        },
    )
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
        ),
        context_error,
        synthetic_response(output),
    ]
    supplied = {
        "status": "available",
        "proposals": [
            {
                "review_id": "review-a",
                "statement": "chicken was excellent",
                "original_review": "long source context " * 450,
            },
            {
                "review_id": "review-a",
                "statement": "service was slow",
                "original_review": "long source context " * 450,
            },
        ],
    }
    collection, _, calls, client, captured = harness(responses, setup)
    async with client:
        prepared = prepared_owner(
            collection, calls, client, setup, fake_result=supplied
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert result.recovery_attempted is True
    assert (
        result.collection.versions[0].primaries[0].why_to_go
        == "Chicken was excellent, though service was slow."
    )
    assert result.collection.versions[0].runners_up[-1].place_id == "branch_h"
    assert len(captured) == 3
    assert not captured[-2].get("tools") and not captured[-1].get("tools")
    before = json.dumps(captured[-2], ensure_ascii=False)
    after = json.dumps(captured[-1], ensure_ascii=False)
    assert len(after.encode()) < len(before.encode())
    assert "chicken was excellent" in after and "service was slow" in after
    assert "review-a" in after and "deferred_review_context_ids" in after
    assert "branch_h" in after
    assert collection.request.origin.location.timezone in after
    assert calls.receipts[-2].diagnostic == "context_limit"
    assert calls.research_budget.charged > 0


@pytest.mark.asyncio
async def test_irreducible_provider_final_context_error_does_not_retry_identical_request(
    setup,
):
    from app.agents.owner_execution import execute_owner

    context_error = httpx2.Response(
        400,
        json={
            "error": {
                "message": "context too long",
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        },
    )
    collection, _, calls, client, captured = harness([context_error], setup)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
        )
    assert result.status == "technical_failure"
    assert result.reason == "context_limit"
    assert result.collection is None
    assert result.recovery_attempted is False
    assert len(captured) == 1
    assert not captured[0].get("tools")


@pytest.mark.asyncio
async def test_tool_limit_closes_research_and_retains_prior_observation(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    for item in (*output["primaries"], *output["runners_up"]):
        item["food_claim_ids"] = []
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
            call_id="first",
        ),
        synthetic_response(output),
    ]
    budget = ResearchBudget(max_tools=1)
    collection, _, calls, client, captured = harness(responses, setup, budget=budget)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=5,
        )
    assert result.status in {"completed", "partial"}
    assert result.research_closed is True
    assert result.collection.request == collection.request
    assert (
        result.collection.versions[0].primaries[0].why_to_go
        == "Chicken was excellent, though service was slow."
    )
    assert len(captured) == 2
    assert not captured[-1].get("tools")
    final_input = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" in final_input and "service was slow" in final_input
    assert budget.tools == 1


@pytest.mark.asyncio
async def test_exposed_fresh_claim_survives_as_branch_scoped_recommendation(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["food_claim_ids"] = ["menu_a"]
    responses = [
        tool_call(
            "list_food_claims", {"place_id": "branch_a", "after": None, "limit": 10}
        ),
        synthetic_response(output),
    ]
    collection, _, calls, client, captured = harness(responses, setup)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert result.collection.versions[0].primaries[0].food_claim_ids == ("menu_a",)
    claim_context = json.dumps(captured[-1]["input"])
    assert (
        "menu_a" in claim_context
        and "ev_google" in claim_context
        and "branch_a" in claim_context
    )
    assert not captured[-1].get("tools")


@pytest.mark.asyncio
async def test_guessed_unseen_claim_is_rejected_before_single_recovery(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    guessed = safe_draft(original)
    guessed["primaries"][0]["food_claim_ids"] = ["menu_a"]
    collection, _, calls, client, captured = harness(
        [synthetic_response(guessed), synthetic_response(safe_draft(original))], setup
    )
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
        )
    assert result.status in {"completed", "partial"}
    assert result.recovery_attempted is True
    assert result.collection.versions[0].primaries[0].food_claim_ids == ()
    assert len(captured) == 2 and not captured[-1].get("tools")
    assert "unavailable_claim" in json.dumps(captured[-1]["input"])


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["stale", "restricted"])
async def test_stale_or_restricted_claim_is_not_exposed_or_accepted(setup, variant):
    from app.agents.owner_execution import execute_owner

    original = context()
    guessed = safe_draft(original)
    guessed["primaries"][0]["food_claim_ids"] = ["menu_a"]
    responses = [
        tool_call(
            "list_food_claims", {"place_id": "branch_a", "after": None, "limit": 10}
        ),
        synthetic_response(guessed),
        synthetic_response(safe_draft(original)),
    ]
    collection, _, calls, client, captured = harness(
        responses, setup, evidence_variant=variant
    )
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert result.recovery_attempted is True
    assert result.collection.versions[0].primaries[0].food_claim_ids == ()
    observation = result.retained[0]["result"]
    assert all(row["claim_id"] != "menu_a" for row in observation["claims"])
    assert observation["omissions"][variant] >= 1
    assert len(captured) == 3 and not captured[-1].get("tools")


@pytest.mark.asyncio
async def test_claim_exposed_for_other_branch_cannot_transfer(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    wrong = safe_draft(original)
    wrong["primaries"][1]["food_claim_ids"] = ["menu_a"]
    responses = [
        tool_call(
            "list_food_claims", {"place_id": "branch_a", "after": None, "limit": 10}
        ),
        synthetic_response(wrong),
        synthetic_response(safe_draft(original)),
    ]
    collection, _, calls, client, captured = harness(responses, setup)
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert result.recovery_attempted is True
    assert result.collection.versions[0].primaries[1].food_claim_ids == ()
    assert len(captured) == 3


@pytest.mark.asyncio
async def test_nested_specialist_context_failure_keeps_prior_finding_and_closes_tools(
    setup,
):
    from dataclasses import replace

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    context_error = httpx2.Response(
        400,
        json={
            "error": {
                "message": "context too long",
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        },
    )
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
            call_id="first",
        ),
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "What food is mentioned?"},
            call_id="second",
        ),
        context_error,
        synthetic_response(output),
    ]
    collection, _, calls, client, captured = harness(responses, setup)
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    candidate = replace(candidate, sample=replace(sample, reviews=(review,)))
    async with client:
        specialist = prepare_review_tool(
            calls, client, collection.request, candidate, max_output_tokens=1024
        )
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=5,
        )
    assert result.status in {"completed", "partial"}
    assert result.research_closed is True
    assert (
        result.collection.versions[0].primaries[0].why_to_go
        == "Chicken was excellent, though service was slow."
    )
    assert len(captured) == 4
    assert not captured[-1].get("tools")
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" in sent and "service was slow" in sent
    assert "context_limit" in sent
    assert any(
        r.model_role == "research" and r.diagnostic == "context_limit"
        for r in calls.receipts
    )


@pytest.mark.asyncio
async def test_claim_expiring_after_exposure_is_rejected_before_publish(setup):
    from datetime import UTC, datetime

    from app.agents.owner_execution import execute_owner

    original = context()
    proposed = safe_draft(original)
    proposed["primaries"][0]["food_claim_ids"] = ["menu_a"]
    now = {"value": datetime(2026, 10, 30, tzinfo=UTC)}

    def advance_after_exposure(count):
        if count == 2:
            now["value"] = datetime(2026, 12, 1, tzinfo=UTC)

    responses = [
        tool_call(
            "list_food_claims", {"place_id": "branch_a", "after": None, "limit": 10}
        ),
        synthetic_response(proposed),
        synthetic_response(safe_draft(original)),
    ]
    collection, _, calls, client, captured = harness(
        responses, setup, on_request=advance_after_exposure
    )
    async with client:
        prepared = prepared_owner(
            collection, calls, client, setup, clock=lambda: now["value"]
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert result.recovery_attempted is True
    assert result.collection.versions[0].primaries[0].food_claim_ids == ()
    assert "menu_a" in json.dumps(captured[1]["input"])
    assert "unavailable_claim" in json.dumps(captured[-1]["input"])
    assert "The synthetic menu lists noodles for branch a." not in json.dumps(
        captured[-1]["input"]
    )
    assert len(captured) == 3


@pytest.mark.asyncio
async def test_gap_too_large_for_remaining_reserve_keeps_prior_evidence(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
            call_id="first",
        ),
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google_0", "offset": 0},
            call_id="second",
        ),
        synthetic_response(output),
    ]

    def evidence(request):
        if request.item_id == "ev_google":
            return {
                "status": "available",
                "place_id": "branch_a",
                "source_id": "src_google",
                "evidence_id": "ev_google",
                "statement": "The chicken was excellent, but service was slow.",
            }
        budget.usage_limit = budget.charged + budget._completion_usage
        return {
            "status": "available",
            "place_id": "branch_a",
            "source_id": "src_google",
            "evidence_id": "ev_google_0",
            "review_id": "review_0",
            "statement": "source detail " * 1200,
        }

    budget = ResearchBudget(usage_limit=100000)

    collection, _, calls, client, captured = harness(responses, setup, budget=budget)
    async with client:
        prepared = prepared_owner(
            collection, calls, client, setup, fake_result=evidence
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=4,
            completion_usage=2048,
        )
    assert result.status in {"completed", "partial"}
    assert result.research_closed is True
    assert len(captured) == 3 and not captured[-1].get("tools")
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" in sent and "service was slow" in sent
    assert json.loads(captured[-1]["input"][0]["content"])["omitted_observations"] == 1
    assert "completion_reserve" not in sent
    assert len(result.retained) == 1
    assert (
        result.collection.versions[0].primaries[0].why_to_go
        == "Chicken was excellent, though service was slow."
    )
    assert budget.charged <= budget.usage_limit


@pytest.mark.asyncio
async def test_retained_review_expiring_before_composition_is_not_resent(setup):
    from datetime import UTC, datetime

    from app.agents.owner_execution import execute_owner

    original = context()
    calls_to_model = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
        ),
        synthetic_response(safe_draft(original)),
    ]
    collection, _, calls, client, captured = harness(calls_to_model, setup)
    count = {"value": 0}

    def expiring_clock():
        count["value"] += 1
        return (
            datetime(2026, 10, 30, tzinfo=UTC)
            if count["value"] == 1
            else datetime(2026, 12, 1, tzinfo=UTC)
        )

    async with client:
        prepared = prepared_owner(
            collection, calls, client, setup, clock=expiring_clock
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 2
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" not in sent
    assert "service was slow" not in sent
    assert "stale" in sent or "omitted_observations" in sent
    assert not captured[-1].get("tools")


@pytest.mark.asyncio
async def test_invalid_claim_notice_too_large_still_allows_tools_free_recovery(setup):
    from app.agents.owner_execution import execute_owner

    original = context()
    guessed = safe_draft(original)
    guessed["primaries"][0]["food_claim_ids"] = ["menu_a"]
    budget = ResearchBudget(usage_limit=100000)
    captured = []

    def limit_after_first_dispatch(count):
        if count == 1:
            budget.usage_limit = budget.charged + budget.estimate(captured[0]) + 1200

    collection, _, calls, client, captured = harness(
        [synthetic_response(guessed), synthetic_response(safe_draft(original))],
        setup,
        budget=budget,
        on_request=limit_after_first_dispatch,
    )
    async with client:
        prepared = prepared_owner(collection, calls, client, setup)
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=0,
            completion_usage=2048,
        )
    assert result.status in {"completed", "partial"}
    assert result.recovery_attempted is True
    assert result.collection.versions[0].primaries[0].food_claim_ids == ()
    assert len(captured) == 2 and not captured[-1].get("tools")
    sent = json.loads(captured[-1]["input"][0]["content"])
    assert sent["omitted_observations"] == 1
    assert "unavailable_claim" not in json.dumps(sent)
    assert budget.charged <= budget.usage_limit


@pytest.mark.asyncio
async def test_oversized_helper_keeps_small_proposals_and_omits_unrelated_payload(
    setup,
):
    from dataclasses import replace

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    output = safe_draft(original)
    output["primaries"][0]["why_to_go"] = (
        "Chicken was excellent, though service was slow."
    )
    responses = [
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "What did reviewers mention?"},
        ),
        synthetic_response(
            {
                "findings": [
                    {
                        "review_label": "r0",
                        "topic": "food",
                        "polarity": "positive",
                        "wording": "paraphrase",
                        "statement": "chicken was excellent",
                        "basis": None,
                    },
                    {
                        "review_label": "r0",
                        "topic": "service",
                        "polarity": "negative",
                        "wording": "paraphrase",
                        "statement": "service was slow",
                        "basis": None,
                    },
                ],
                "gaps": [],
            }
        ),
        synthetic_response(output),
    ]
    collection, _, calls, client, captured = harness(responses, setup)
    async with client:
        _, _, candidate = setup_values()
        sample = candidate.sample
        review = replace(
            sample.reviews[0],
            review_id="secret",
            link_basis="review",
            author_name=None,
            author_url=None,
            text="The chicken was excellent, but service was slow. 私はまた行きたい。",
            source_url="https://google.com/SECRET_REVIEW",
        )
        candidate = replace(candidate, sample=replace(sample, reviews=(review,)))
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        real_call = specialist.tool.on_invoke_tool

        async def oversized_result(context, arguments):
            result = json.loads(await real_call(context, arguments))
            result.update(
                {
                    "source_id": "src_google",
                    "evidence_id": "ev_google",
                    "unrelated": "noise " * 6000,
                }
            )
            return json.dumps(result)

        specialist.tool.on_invoke_tool = oversized_result
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" in sent and "service was slow" in sent
    assert "secret" in sent and "ev_google" in sent
    assert "omitted_fields" in sent and "unrelated" in sent
    assert "noise " * 30 not in sent
    assert (
        result.collection.versions[0].primaries[0].why_to_go
        == "Chicken was excellent, though service was slow."
    )


@pytest.mark.asyncio
async def test_specialist_review_expiring_before_composition_removes_proposal_and_text(
    setup,
):
    from dataclasses import replace
    from datetime import UTC, datetime

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    nested = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "positive",
                "wording": "paraphrase",
                "statement": "chicken was excellent",
                "basis": None,
            }
        ],
        "gaps": [],
    }
    responses = [
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "What did reviewers mention?"},
        ),
        synthetic_response(nested),
        synthetic_response(safe_draft(original)),
    ]
    now = {"value": datetime(2026, 10, 30, tzinfo=UTC)}

    def expire_after_nested_dispatch(count):
        if count == 2:
            now["value"] = datetime(2026, 12, 1, tzinfo=UTC)

    collection, _, calls, client, captured = harness(
        responses, setup, on_request=expire_after_nested_dispatch
    )
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    candidate = replace(candidate, sample=replace(sample, reviews=(review,)))
    async with client:
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        prepared = prepared_owner(
            collection,
            calls,
            client,
            setup,
            specialist=specialist,
            clock=lambda: now["value"],
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 3 and not captured[-1].get("tools")
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" not in sent and "service was slow" not in sent
    assert "stale" in sent or "omitted_observations" in sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["matched", "wrong_branch", "wrong_body", "wrong_url"]
)
async def test_specialist_result_requires_exact_bound_review_source_and_text(
    setup, variant
):
    from dataclasses import replace

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    output = safe_draft(original)
    nested_output = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "positive",
                "wording": "paraphrase",
                "statement": "Distinctive chicken finding.",
                "basis": None,
            }
        ],
        "gaps": [],
    }
    responses = [
        tool_call(
            "research_place", {"place_id": "branch_a", "question": "What is useful?"}
        ),
        synthetic_response(nested_output),
        synthetic_response(output),
    ]
    collection, _, calls, client, captured = harness(responses, setup)
    _, _, candidate = setup_values()
    sample = candidate.sample
    source_text = "The chicken was excellent, but service was slow. 私はまた行きたい。"
    source_url = "https://google.com/SECRET_REVIEW"
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text=source_text
        if variant != "wrong_body"
        else "A different review for a different branch.",
        source_url=source_url
        if variant != "wrong_url"
        else "https://google.com/wrong-review",
    )
    branch = (
        replace(sample.branch, feature_id="0x3:0x4")
        if variant == "wrong_branch"
        else sample.branch
    )
    candidate = replace(
        candidate, sample=replace(sample, branch=branch, reviews=(review,))
    )
    async with client:
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 3 and not captured[-1].get("tools")
    sent = json.dumps(captured[-1]["input"])
    if variant == "matched":
        assert "Distinctive chicken finding." in sent
        assert "chicken was excellent" in sent
    else:
        assert "Distinctive chicken finding." not in sent
        assert "A different review for a different branch." not in sent
        assert "omitted_observations" in sent or "unavailable" in sent


@pytest.mark.asyncio
async def test_unlinked_specialist_gap_is_not_replayed_without_proposals(setup):
    from dataclasses import replace

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    responses = [
        tool_call(
            "research_place", {"place_id": "branch_a", "question": "What is useful?"}
        ),
        synthetic_response(
            {"findings": [], "gaps": ["Unlinked source-derived private detail."]}
        ),
        synthetic_response(safe_draft(original)),
    ]
    collection, _, calls, client, captured = harness(responses, setup)
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    candidate = replace(
        candidate,
        sample=replace(
            sample,
            branch=replace(sample.branch, feature_id="0x3:0x4"),
            reviews=(review,),
        ),
    )
    async with client:
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 3
    sent = json.dumps(captured[-1]["input"])
    assert "Unlinked source-derived private detail." not in sent
    assert "omitted" in sent or "unavailable" in sent


@pytest.mark.asyncio
async def test_invalidated_later_review_keeps_earlier_independent_specialist_result(
    setup,
):
    from dataclasses import replace
    from datetime import UTC, datetime

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()

    def proposal(label, statement):
        return synthetic_response(
            {
                "findings": [
                    {
                        "review_label": label,
                        "topic": "food",
                        "polarity": "positive",
                        "wording": "paraphrase",
                        "statement": statement,
                        "basis": None,
                    }
                ],
                "gaps": [],
            }
        )

    responses = [
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "Look for chicken."},
            call_id="first",
        ),
        proposal("r0", "EARLIER_FRESH_FINDING"),
        tool_call(
            "research_place",
            {"place_id": "branch_b", "question": "Look for soup."},
            call_id="second",
        ),
        proposal("r0", "LATER_EXPIRED_FINDING"),
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "Follow up LATER_EXPIRED_FINDING"},
            call_id="third",
        ),
        proposal("r0", "SUBSEQUENT_DEPENDENT_FINDING"),
        synthetic_response(safe_draft(original)),
    ]
    now = {"value": datetime(2026, 10, 30, tzinfo=UTC)}

    def expire_during_third_helper(count):
        if count == 6:
            now["value"] = datetime(2026, 11, 2, tzinfo=UTC)

    collection, _, calls, client, captured = harness(
        responses,
        setup,
        evidence_variant="sequence",
        on_request=expire_during_third_helper,
    )
    _, _, candidate = setup_values()
    sample = candidate.sample
    first = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    second = replace(
        first,
        review_id="secret_b",
        text="The soup was cold.",
        source_url="https://google.com/SECRET_REVIEW_B",
    )
    candidate_a = replace(candidate, sample=replace(sample, reviews=(first,)))
    branch_b = replace(sample.branch, feature_id="0x3:0x4")
    candidate_b = replace(
        candidate,
        place_id="branch_b",
        sample=replace(sample, branch=branch_b, reviews=(second,)),
    )
    async with client:
        specialist_a = prepare_review_tool(
            calls, client, collection.request, candidate_a
        )
        specialist_b = prepare_review_tool(
            calls, client, collection.request, candidate_b
        )
        prepared = prepared_owner(
            collection,
            calls,
            client,
            setup,
            specialists={"branch_a": specialist_a, "branch_b": specialist_b},
            clock=lambda: now["value"],
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=3,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 7 and not captured[-1].get("tools")
    sent = json.dumps(captured[-1]["input"])
    assert "EARLIER_FRESH_FINDING" in sent
    assert "LATER_EXPIRED_FINDING" not in sent
    assert "SUBSEQUENT_DEPENDENT_FINDING" not in sent
    assert "Look for chicken." in sent
    assert "Follow up LATER_EXPIRED_FINDING" not in sent
    assert "omitted" in sent or "unavailable" in sent


@pytest.mark.asyncio
async def test_specialist_source_permission_revocation_prevents_later_replay(
    setup, monkeypatch
):
    from dataclasses import replace

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool
    from app.core import source_policy

    original = context()
    nested_output = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "positive",
                "wording": "paraphrase",
                "statement": "REVOKED_SOURCE_FINDING",
                "basis": None,
            }
        ],
        "gaps": [],
    }
    responses = [
        tool_call("research_place", {"place_id": "branch_a", "question": "What food?"}),
        synthetic_response(nested_output),
        synthetic_response(safe_draft(original)),
    ]

    def revoke_during_nested_response(count):
        if count == 2:
            monkeypatch.setattr(
                source_policy,
                "POLICIES",
                dict(source_policy.POLICIES)
                | {
                    "public_google_reviews": replace(
                        source_policy.POLICIES["public_google_reviews"], model=False
                    )
                },
            )

    collection, _, calls, client, captured = harness(
        responses, setup, on_request=revoke_during_nested_response
    )
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    candidate = replace(candidate, sample=replace(sample, reviews=(review,)))
    async with client:
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    if len(captured) == 2:
        assert result.status == "technical_failure"
        assert result.collection is None
    else:
        assert len(captured) == 3
        assert "REVOKED_SOURCE_FINDING" not in json.dumps(captured[-1]["input"])
        assert "chicken was excellent" not in json.dumps(captured[-1]["input"])


@pytest.mark.asyncio
async def test_fresh_specialist_gap_can_guide_next_research_turn(setup):
    from dataclasses import replace

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    nested = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "positive",
                "wording": "paraphrase",
                "statement": "Chicken is a useful lead.",
                "basis": None,
            }
        ],
        "gaps": [],
    }
    responses = [
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "What is missing?"},
            call_id="first",
        ),
        synthetic_response(
            {"findings": [], "gaps": ["Check chicken details before deciding."]}
        ),
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "Check chicken details."},
            call_id="second",
        ),
        synthetic_response(nested),
        synthetic_response(safe_draft(original)),
    ]
    collection, _, calls, client, captured = harness(responses, setup)
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    candidate = replace(candidate, sample=replace(sample, reviews=(review,)))
    async with client:
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=2,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 5
    owner_followup = json.dumps(captured[2]["input"])
    assert "Check chicken details before deciding." in owner_followup
    assert captured[2].get("tools")
    assert not captured[-1].get("tools")
    assert "Chicken is a useful lead." in json.dumps(captured[-1]["input"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [
        "fresh",
        "stale",
        "restricted",
        "wrong_branch",
        "wrong_body",
        "wrong_url",
        "derived_denied",
    ],
)
async def test_specialist_dispatch_requires_fresh_exact_source_binding(setup, variant):
    from dataclasses import replace

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    nested = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "positive",
                "wording": "paraphrase",
                "statement": "BOUND_SPECIALIST_FINDING",
                "basis": None,
            }
        ],
        "gaps": [],
    }
    responses = [
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "What food is useful?"},
        )
    ]
    if variant == "fresh":
        responses.append(synthetic_response(nested))
    responses.append(synthetic_response(safe_draft(original)))
    evidence_variant = (
        variant if variant in {"stale", "restricted", "derived_denied"} else None
    )
    collection, _, calls, client, captured = harness(
        responses, setup, evidence_variant=evidence_variant
    )
    _, _, candidate = setup_values()
    sample = candidate.sample
    text = "The chicken was excellent, but service was slow. 私はまた行きたい。"
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text=text if variant != "wrong_body" else "An entirely different review.",
        source_url="https://google.com/SECRET_REVIEW"
        if variant != "wrong_url"
        else "https://google.com/WRONG_REVIEW",
    )
    branch = (
        replace(sample.branch, feature_id="0x3:0x4")
        if variant == "wrong_branch"
        else sample.branch
    )
    candidate = replace(
        candidate, sample=replace(sample, branch=branch, reviews=(review,))
    )
    async with client:
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert not captured[-1].get("tools")
    if variant == "fresh":
        assert len(captured) == 3
        assert any(
            receipt.model_role == "research" and receipt.outcome == "success"
            for receipt in calls.receipts
        )
        assert "BOUND_SPECIALIST_FINDING" in json.dumps(captured[-1]["input"])
    else:
        assert len(captured) == 2
        assert all(receipt.model_role != "research" for receipt in calls.receipts)
        final_context = json.dumps(captured[-1]["input"])
        assert "BOUND_SPECIALIST_FINDING" not in final_context
        assert "chicken was excellent" not in final_context
        assert any(
            word in final_context
            for word in ("unavailable", "stale", "restricted", "unconfirmed")
        )


@pytest.mark.asyncio
async def test_nested_source_expiry_stops_second_specialist_model_turn(setup):
    from dataclasses import replace
    from datetime import UTC, datetime

    from app.agents.owner import prepare_owner
    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool
    from app.providers.geoapify import GeoapifyClient

    original = context()
    responses = [
        tool_call("research_place", {"place_id": "branch_a", "question": "What food?"}),
        tool_call(
            "list_review_evidence", {"after": None, "limit": 10}, call_id="child"
        ),
        synthetic_response(safe_draft(original)),
    ]
    now = {"value": datetime(2026, 10, 30, tzinfo=UTC)}

    def expire_after_first_nested_dispatch(count):
        if count == 2:
            now["value"] = datetime(2026, 12, 1, tzinfo=UTC)

    collection, _, calls, client, captured = harness(
        responses, setup, on_request=expire_after_first_nested_dispatch
    )
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    candidate = replace(candidate, sample=replace(sample, reviews=(review,)))
    async with client:
        base = prepared_owner(
            collection, calls, client, setup, clock=lambda: now["value"]
        )
        specialist = prepare_review_tool(
            calls,
            client,
            collection.request,
            candidate,
            research_tools=base.research_tools,
        )
        prepared = prepare_owner(
            calls,
            client,
            collection.request,
            collection.snapshots[0],
            base.research_tools,
            GeoapifyClient(calls),
            {"branch_a": specialist},
            candidate_sources={
                p.place_id: "geoapify_geography"
                for p in (
                    *collection.snapshots[0].discovery_leads,
                    *collection.snapshots[0].researched_candidates,
                )
            },
            max_output_tokens=1024,
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 3
    assert sum(r.model_role == "research" for r in calls.receipts) == 1
    assert not captured[-1].get("tools")
    sent = json.dumps(captured[-1]["input"])
    assert "stale" in sent
    assert "chicken was excellent" not in sent


@pytest.mark.asyncio
async def test_fitting_excludes_unbound_review_without_blocking_bound_sample(setup):
    from dataclasses import replace

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    nested = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "positive",
                "wording": "paraphrase",
                "statement": "FITTED_BOUND_FINDING",
                "basis": None,
            }
        ],
        "gaps": [],
    }
    responses = [
        tool_call("research_place", {"place_id": "branch_a", "question": "What food?"}),
        synthetic_response(nested),
        synthetic_response(safe_draft(original)),
    ]
    collection, _, calls, client, captured = harness(responses, setup)
    _, _, candidate = setup_values()
    sample = candidate.sample
    first = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    excluded = replace(
        first,
        review_id="not_in_snapshot",
        text="UNBOUND_BODY " * 700,
        source_url="https://google.com/UNKNOWN",
    )
    candidate = replace(candidate, sample=replace(sample, reviews=(first, excluded)))
    async with client:
        specialist = prepare_review_tool(
            calls, client, collection.request, candidate, max_input_bytes=4096
        )
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=1,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 3
    assert "UNBOUND_BODY" not in json.dumps(captured[1]["input"])
    assert "FITTED_BOUND_FINDING" in json.dumps(captured[-1]["input"])
    assert (
        sum(
            r.model_role == "research" and r.outcome == "success"
            for r in calls.receipts
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["fresh", "stale", "wrong_branch"])
async def test_direct_owner_agent_enforces_specialist_preflight(setup, variant):
    from dataclasses import replace

    from agents import RunConfig, Runner

    from app.agents.research import prepare_review_tool

    original = context()
    nested = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "positive",
                "wording": "paraphrase",
                "statement": "DIRECT_FRESH_FINDING",
                "basis": None,
            }
        ],
        "gaps": [],
    }
    responses = [
        tool_call("research_place", {"place_id": "branch_a", "question": "What food?"})
    ]
    if variant == "fresh":
        responses.append(synthetic_response(nested))
    responses.append(synthetic_response(safe_draft(original)))
    collection, _, calls, client, captured = harness(
        responses, setup, evidence_variant="stale" if variant == "stale" else None
    )
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    branch = (
        replace(sample.branch, feature_id="0x3:0x4")
        if variant == "wrong_branch"
        else sample.branch
    )
    candidate = replace(
        candidate, sample=replace(sample, branch=branch, reviews=(review,))
    )
    async with client:
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        prepared = prepared_owner(
            collection, calls, client, setup, specialist=specialist
        )
        result = await Runner.run(
            prepared.agent,
            prepared.input_json,
            run_config=RunConfig(
                tracing_disabled=True, trace_include_sensitive_data=False
            ),
            max_turns=3,
        )
    assert result.final_output.primaries[0].place_id == "branch_a"
    if variant == "fresh":
        assert len(captured) == 3
        assert sum(r.model_role == "research" for r in calls.receipts) == 1
    else:
        assert len(captured) == 2
        assert all(r.model_role != "research" for r in calls.receipts)
        assert "DIRECT_FRESH_FINDING" not in json.dumps(captured[-1]["input"])
        assert "stale" in json.dumps(captured[-1]["input"]) or "source" in json.dumps(
            captured[-1]["input"]
        )


@pytest.mark.asyncio
async def test_source_expiring_as_owner_requests_tool_blocks_stale_question_dispatch(
    setup,
):
    from dataclasses import replace
    from datetime import UTC, datetime

    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool

    original = context()
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
            call_id="first",
        ),
        tool_call(
            "research_place",
            {
                "place_id": "branch_a",
                "question": "Investigate the chicken was excellent finding.",
            },
            call_id="second",
        ),
        synthetic_response(safe_draft(original)),
    ]
    now = {"value": datetime(2026, 10, 30, tzinfo=UTC)}

    def expire_during_owner_response(count):
        if count == 2:
            now["value"] = datetime(2026, 12, 1, tzinfo=UTC)

    collection, _, calls, client, captured = harness(
        responses, setup, on_request=expire_during_owner_response
    )
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    candidate = replace(candidate, sample=replace(sample, reviews=(review,)))
    async with client:
        specialist = prepare_review_tool(calls, client, collection.request, candidate)
        prepared = prepared_owner(
            collection,
            calls,
            client,
            setup,
            specialist=specialist,
            clock=lambda: now["value"],
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=3,
        )
    assert result.status in {"completed", "partial"}
    assert result.research_closed is True
    assert len(captured) == 3 and not captured[-1].get("tools")
    assert all(receipt.model_role != "research" for receipt in calls.receipts)
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" not in sent
    assert "service was slow" not in sent
    assert "stale" in sent or "source_changed" in sent or "omitted_observations" in sent


@pytest.mark.asyncio
async def test_prior_owner_evidence_expiring_during_nested_research_stops_next_nested_turn(
    setup,
):
    from dataclasses import replace
    from datetime import UTC, datetime

    from app.agents.owner import prepare_owner
    from app.agents.owner_execution import execute_owner
    from app.agents.research import prepare_review_tool
    from app.providers.geoapify import GeoapifyClient

    original = context()
    responses = [
        tool_call(
            "read_evidence",
            {"place_id": "branch_a", "item_id": "ev_google", "offset": 0},
            call_id="first",
        ),
        tool_call(
            "read_evidence",
            {"place_id": "branch_b", "item_id": "ev_google_later", "offset": 0},
            call_id="second",
        ),
        tool_call(
            "research_place",
            {"place_id": "branch_a", "question": "Compare the soup and chicken."},
            call_id="third",
        ),
        tool_call(
            "list_review_evidence", {"after": None, "limit": 10}, call_id="nested"
        ),
        synthetic_response(safe_draft(original)),
    ]
    now = {"value": datetime(2026, 10, 30, tzinfo=UTC)}

    def expire_prior_b_during_nested(count):
        if count == 4:
            now["value"] = datetime(2026, 11, 2, tzinfo=UTC)

    collection, _, calls, client, captured = harness(
        responses,
        setup,
        evidence_variant="sequence",
        on_request=expire_prior_b_during_nested,
    )
    _, _, candidate = setup_values()
    sample = candidate.sample
    review = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    candidate = replace(candidate, sample=replace(sample, reviews=(review,)))
    async with client:
        base = prepared_owner(
            collection, calls, client, setup, clock=lambda: now["value"]
        )
        specialist = prepare_review_tool(
            calls,
            client,
            collection.request,
            candidate,
            research_tools=base.research_tools,
        )
        snapshot = collection.snapshots[0]
        sources = {
            p.place_id: "geoapify_geography"
            for p in (*snapshot.discovery_leads, *snapshot.researched_candidates)
        }
        prepared = prepare_owner(
            calls,
            client,
            collection.request,
            snapshot,
            base.research_tools,
            GeoapifyClient(calls),
            {"branch_a": specialist},
            candidate_sources=sources,
            max_output_tokens=1024,
        )
        result = await execute_owner(
            prepared,
            calls,
            search_id=collection.search_id,
            version_id="ver_owner",
            research_turns=3,
        )
    assert result.status in {"completed", "partial"}
    assert len(captured) == 5 and not captured[-1].get("tools")
    assert sum(r.model_role == "research" for r in calls.receipts) == 1
    sent = json.dumps(captured[-1]["input"])
    assert "chicken was excellent" in sent and "service was slow" in sent
    assert "The soup was cold." not in sent
    assert "Compare the soup and chicken." not in sent
    assert "stale" in sent or "omitted_observations" in sent
