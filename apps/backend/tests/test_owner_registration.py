import json
from dataclasses import replace
from datetime import timedelta

import httpx2
import pytest
from agents import RunConfig, Runner
from agents.agent_output import AgentOutputSchema
from agents.tool_context import ToolContext
from openai import AsyncOpenAI
from test_research_agent import model_response, setup_values

from app.agents.owner import prepare_owner
from app.agents.owner_draft import OwnerDraft
from app.agents.research import prepare_review_tool
from app.core.calls import AttemptCalls, Bounds
from app.providers.geoapify import GeoapifyClient
from app.schemas.recommendations import ResearchSnapshot
from app.schemas.search import Place

pytest_plugins = ["test_research_tools"]


def setup_snapshot(setup):
    _, _, _, evidence = setup
    _, request, _ = setup_values()
    location = request.origin.location
    return ResearchSnapshot(
        snapshot_id="snap_owner",
        researched_candidates=(
            Place(place_id="branch_a", kind="branch", name="A Cafe", location=location),
            Place(
                place_id="branch_b",
                kind="branch",
                name="Restricted source name",
                location=location,
            ),
        ),
        evidence=evidence,
    )


def snapshot_tools(setup):
    from app.db.research_snapshots import SnapshotCandidate, store_snapshot
    from app.db.session import transaction
    from app.research_tools import ResearchTools

    connection, search, _, collection = setup
    with transaction(connection):
        store_snapshot(
            connection,
            search,
            "snap_owner",
            candidates=(
                SnapshotCandidate("branch_a", "branch"),
                SnapshotCandidate("branch_b", "branch"),
            ),
            findings=(),
        )
    return ResearchTools(
        connection,
        search,
        "snap_owner",
        place_ids=("branch_a", "branch_b"),
        max_content_bytes=4096,
        ephemeral=collection,
        source_classes={"src_google": "public_google_reviews"},
    )


def call_context(tool, arguments):
    return ToolContext(
        context=None,
        tool_name=tool.name,
        tool_call_id="test-call",
        tool_arguments=arguments,
    )


def tool_call(name, arguments):
    return {
        "id": "r1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4o",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps(arguments),
                "call_id": "call1",
                "status": "completed",
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
    }


@pytest.mark.asyncio
async def test_owner_calls_real_bound_research_agent_and_returns_draft(setup):
    settings, request, review = setup_values()
    sample = review.sample
    matching = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    review = replace(review, sample=replace(sample, reviews=(matching,)))
    snapshot = setup_snapshot(setup)
    research_tools = snapshot_tools(setup)
    responses = iter(
        [
            tool_call(
                "research_place",
                {"place_id": "branch_a", "question": "What food stood out?"},
            ),
            model_response(
                {
                    "findings": [
                        {
                            "review_label": "r0",
                            "topic": "food",
                            "polarity": "positive",
                            "wording": "paraphrase",
                            "statement": "The chicken was excellent.",
                            "basis": None,
                        }
                    ],
                    "gaps": [],
                }
            ),
            model_response(
                {
                    "primaries": [
                        {
                            "place_id": "branch_a",
                            "why_to_go": "Diners praised the chicken.",
                            "food_claim_ids": [],
                        }
                    ],
                    "runners_up": [],
                    "shortfall_reason": "One suitable option was researched.",
                }
            ),
        ]
    )
    seen = []

    def respond(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(200, json=next(responses))

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=6, total_seconds=5.0))
        review_agent = prepare_review_tool(calls, client, request, review)
        owner = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            research_tools,
            GeoapifyClient(calls),
            {"branch_a": review_agent},
            candidate_sources={
                "branch_a": "geoapify_geography",
                "branch_b": "unknown_publisher",
            },
        )
        result = await Runner.run(
            owner.agent,
            owner.input_json,
            run_config=RunConfig(
                tracing_disabled=True, trace_include_sensitive_data=False
            ),
            max_turns=3,
        )
    assert isinstance(result.final_output, OwnerDraft)
    assert result.final_output.primaries[0].why_to_go == "Diners praised the chicken."
    assert result.final_output.primaries[0].place_id == "branch_a"
    assert len(review_agent.contributions) == 1
    assert review_agent.contributions[0].place_id == "branch_a"
    assert review_agent.contributions[0].proposals[0].review.review_id == "secret"
    assert "Restricted source name" not in owner.input_json
    assert "SECRET AUTHOR" not in owner.input_json
    assert (
        seen[0]["text"]["format"]["schema"]
        == AgentOutputSchema(OwnerDraft).json_schema()
    )
    assert [receipt.model_role for receipt in calls.receipts] == [
        "owner",
        "research",
        "owner",
    ]
    assert [record.name for record in calls.research_budget.tool_records] == [
        "specialist",
        "research_place_reviews",
    ]
    assert "owner_output" in owner.schema_hashes


@pytest.mark.asyncio
async def test_owner_rejects_mismatched_scope_and_shared_attempt(setup):
    settings, request, review = setup_values()
    snapshot = setup_snapshot(setup)
    research_tools = snapshot_tools(setup)
    async with AsyncOpenAI(api_key="synthetic-openai-key", max_retries=0) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        other = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        agent = prepare_review_tool(calls, client, request, review)
        with pytest.raises(ValueError, match="another place"):
            prepare_owner(
                calls,
                client,
                request,
                snapshot,
                research_tools,
                GeoapifyClient(calls),
                {"branch_b": agent},
                candidate_sources={},
            )
        with pytest.raises(ValueError, match="another attempt"):
            prepare_owner(
                calls,
                client,
                request,
                snapshot,
                research_tools,
                GeoapifyClient(other),
                {"branch_a": agent},
                candidate_sources={},
            )
        other_agent = prepare_review_tool(other, client, request, review)
        with pytest.raises(ValueError, match="another attempt"):
            prepare_owner(
                calls,
                client,
                request,
                snapshot,
                research_tools,
                GeoapifyClient(calls),
                {"branch_a": other_agent},
                candidate_sources={},
            )
        changed_request = request.model_copy(
            update={"eat_now_at": request.eat_now_at + timedelta(seconds=1)}
        )
        with pytest.raises(ValueError, match="another request"):
            prepare_owner(
                calls,
                client,
                changed_request,
                snapshot,
                research_tools,
                GeoapifyClient(calls),
                {"branch_a": agent},
                candidate_sources={},
            )

        scoped = replace(agent, search_id="another-search", snapshot_id="snap_owner")
        with pytest.raises(ValueError, match="another search or snapshot"):
            prepare_owner(
                calls,
                client,
                request,
                snapshot,
                research_tools,
                GeoapifyClient(calls),
                {"branch_a": scoped},
                candidate_sources={},
            )
        scoped = replace(
            agent, search_id=research_tools.search_id, snapshot_id="snap_other"
        )
        with pytest.raises(ValueError, match="another search or snapshot"):
            prepare_owner(
                calls,
                client,
                request,
                snapshot,
                research_tools,
                GeoapifyClient(calls),
                {"branch_a": scoped},
                candidate_sources={},
            )
        with pytest.raises(ValueError, match="another snapshot"):
            prepare_owner(
                calls,
                client,
                request,
                snapshot.model_copy(update={"snapshot_id": "snap_other"}),
                research_tools,
                GeoapifyClient(calls),
                {},
                candidate_sources={},
            )


@pytest.mark.asyncio
async def test_owner_research_tool_rejects_unknown_place_without_spending_model_request(
    setup,
):
    settings, request, review = setup_values()
    snapshot = setup_snapshot(setup)
    research_tools = snapshot_tools(setup)
    async with AsyncOpenAI(api_key="synthetic-openai-key", max_retries=0) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        agent = prepare_review_tool(calls, client, request, review)
        owner = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            research_tools,
            GeoapifyClient(calls),
            {"branch_a": agent},
            candidate_sources={},
        )
        tool = next(item for item in owner.agent.tools if item.name == "research_place")
        args = json.dumps({"place_id": "branch_b", "question": "Food?"})
        result = json.loads(await tool.on_invoke_tool(call_context(tool, args), args))
    assert result == {"status": "unavailable"}
    assert calls.receipts == ()
    assert calls.research_budget.tool_records[0].name == "research_place_reviews"


@pytest.mark.asyncio
async def test_owner_food_claims_enforce_subject_policy_and_freshness(setup):
    from datetime import UTC, datetime

    from app.research_tools import ResearchTools

    settings, request, _ = setup_values()
    snapshot = setup_snapshot(setup)
    default_tools = snapshot_tools(setup)
    connection, search, _, evidence = setup

    def make_tools(source, clock):
        return ResearchTools(
            connection,
            search,
            "snap_owner",
            place_ids=("branch_a", "branch_b"),
            max_content_bytes=4096,
            ephemeral=evidence,
            source_classes={"src_google": source},
            clock=clock,
        )

    async def read_claims(owner, place):
        tool = next(
            item for item in owner.agent.tools if item.name == "list_food_claims"
        )
        args = json.dumps({"place_id": place, "after": None, "limit": 10})
        return json.loads(await tool.on_invoke_tool(call_context(tool, args), args))

    async with AsyncOpenAI(api_key="synthetic-openai-key", max_retries=0) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        current = make_tools(
            "public_google_reviews", lambda: datetime(2026, 10, 30, tzinfo=UTC)
        )
        owner = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            current,
            GeoapifyClient(calls),
            {},
            candidate_sources={},
        )
        from app.agents.owner import eligible_claim_ids

        before_tools = calls.research_budget.tools
        assert eligible_claim_ids(owner, "branch_a") == {"restricted"}
        assert calls.research_budget.tools == before_tools
        a = await read_claims(owner, "branch_a")
        b = await read_claims(owner, "branch_b")
        assert [
            (claim["claim_id"], claim["evidence_ids"]) for claim in a["claims"]
        ] == [("restricted", ["ev_google"])]
        assert a["restricted_count"] == 2
        assert b["claims"] == []
        assert b["restricted_count"] == 2
        restricted = make_tools(
            "ordinary_google", lambda: datetime(2026, 10, 30, tzinfo=UTC)
        )
        blocked = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            restricted,
            GeoapifyClient(calls),
            {},
            candidate_sources={},
        )
        assert (await read_claims(blocked, "branch_a"))["claims"] == []
        stale = make_tools(
            "public_google_reviews", lambda: datetime(2026, 12, 30, tzinfo=UTC)
        )
        expired = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            stale,
            GeoapifyClient(calls),
            {},
            candidate_sources={},
        )
        assert eligible_claim_ids(expired, "branch_a") == set()
        expired_result = await read_claims(expired, "branch_a")
        assert expired_result["claims"] == []
        assert expired_result["omissions"]["stale"] == 1
        mismatched = snapshot.model_copy(
            update={"evidence": evidence.model_copy(update={"food_claims": ()})}
        )
        wrong_snapshot = prepare_owner(
            calls,
            client,
            request,
            mismatched,
            default_tools,
            GeoapifyClient(calls),
            {},
            candidate_sources={},
        )
        assert await read_claims(wrong_snapshot, "branch_a") == {
            "status": "unavailable"
        }


@pytest.mark.asyncio
async def test_owner_rejects_distinct_lookup_instance_even_for_same_search_and_snapshot(
    setup,
):
    from app.research_tools import ResearchTools

    settings, request, review = setup_values()
    snapshot = setup_snapshot(setup)
    owner_tools = snapshot_tools(setup)
    connection, search, _, collection = setup
    different_tools = ResearchTools(
        connection,
        search,
        "snap_owner",
        place_ids=("branch_a", "branch_b"),
        max_content_bytes=4096,
        ephemeral=collection,
        source_classes={"src_google": "public_google_reviews"},
    )
    assert owner_tools is not different_tools
    assert owner_tools.search_id == different_tools.search_id
    assert owner_tools.snapshot_id == different_tools.snapshot_id
    async with AsyncOpenAI(api_key="synthetic-openai-key", max_retries=0) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        specialist = prepare_review_tool(
            calls, client, request, review, research_tools=different_tools
        )
        with pytest.raises(ValueError, match="another source scope"):
            prepare_owner(
                calls,
                client,
                request,
                snapshot,
                owner_tools,
                GeoapifyClient(calls),
                {"branch_a": specialist},
                candidate_sources={},
            )
        no_nested_lookup = prepare_review_tool(calls, client, request, review)
        prepared = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            owner_tools,
            GeoapifyClient(calls),
            {"branch_a": no_nested_lookup},
            candidate_sources={},
        )
    assert prepared.research["branch_a"] is no_nested_lookup


@pytest.mark.asyncio
async def test_owner_admits_only_bound_leads_and_uses_expanded_snapshot(setup):
    from app.agents.owner import CandidateAdmission
    from app.agents.owner_draft import validate_owner_draft
    from app.core.calls import Completed
    from app.db.session import transaction
    from app.providers.geoapify import MealPlaceLead, Point, Provenance
    from app.research_tools import ResearchTools

    settings, request, _ = setup_values()
    snapshot = setup_snapshot(setup)
    original_tools = snapshot_tools(setup)
    connection, search, _, _ = setup
    old_handle = original_tools._handle("branch_a", "finding_a")
    new_place = Place(
        place_id="branch_c",
        kind="branch",
        name="New Cafe",
        location=request.origin.location,
    )
    raw = snapshot.model_dump(mode="json")
    raw["researched_candidates"].append(new_place.model_dump(mode="json"))
    raw["evidence"]["place_ids"].append("branch_c")
    expanded = ResearchSnapshot.model_validate_json(json.dumps(raw))
    with transaction(connection):
        connection.execute(
            "INSERT INTO research_candidates (search_id,snapshot_id,place_id,kind,identity_source) VALUES (?,?,?,?,?)",
            (
                search.search_id,
                snapshot.snapshot_id,
                "branch_c",
                "branch",
                "reacquisition_required",
            ),
        )
    expanded_tools = ResearchTools(
        connection,
        search,
        snapshot.snapshot_id,
        place_ids=("branch_a", "branch_b", "branch_c"),
        max_content_bytes=4096,
        ephemeral=expanded.evidence,
        source_classes={"src_google": "public_google_reviews"},
    )
    lead = MealPlaceLead("lead_c", Point(1, 1), "New Cafe", "Address", (), Provenance())
    calls = AttemptCalls(settings, Bounds(max_requests=4, total_seconds=10))
    geo = GeoapifyClient(calls)

    async def nearby(*args, **kwargs):
        return Completed((lead,), None)

    async def admit(item):
        assert item == lead
        return CandidateAdmission(
            "branch_c",
            expanded,
            expanded_tools,
            "geoapify_geography",
            None,
            "zero_reviews",
        )

    geo.nearby_places = nearby
    async with AsyncOpenAI(api_key="test") as client:
        owner = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            original_tools,
            geo,
            {},
            candidate_sources={"branch_a": "geoapify_geography"},
            admit_candidate=admit,
        )
        tool = next(t for t in owner.agent.tools if t.name == "search_nearby")
        args = json.dumps({"radius_m": 100, "limit": 1})
        result = json.loads(await tool.on_invoke_tool(call_context(tool, args), args))
        assert result == {
            "status": "available",
            "leads": [{"place_id": "branch_c", "name": "New Cafe"}],
        }
        assert owner.snapshot == expanded
        rejected = json.loads(await tool.on_invoke_tool(call_context(tool, args), args))
        assert rejected == {"status": "unavailable", "reason": "tool_failed"}
        assert owner.snapshot == expanded
        assert owner.research_tools._handles[old_handle] == ("branch_a", "finding_a")
        research = next(t for t in owner.agent.tools if t.name == "research_place")
        args = json.dumps({"place_id": "branch_c", "question": "What do people say?"})
        assert json.loads(
            await research.on_invoke_tool(call_context(research, args), args)
        ) == {"status": "unavailable", "reason": "zero_reviews"}
        draft = OwnerDraft.model_validate(
            {
                "primaries": [
                    {
                        "place_id": "branch_c",
                        "why_to_go": "A new option.",
                        "food_claim_ids": [],
                    }
                ],
                "runners_up": [],
                "shortfall_reason": "One option.",
            }
        )
        result = validate_owner_draft(
            draft,
            search_id=search.search_id,
            request=request,
            snapshot=owner.snapshot,
            version_id="ver_test",
        )
        assert result.versions[0].primaries[0].place_id == "branch_c"


@pytest.mark.asyncio
async def test_route_tool_retains_request_scoped_observation(setup, monkeypatch):
    from test_foundation import receipt as receipt_fixture

    from app.core.calls import Completed
    from app.providers.geoapify import Point, Provenance, RouteAvailable

    receipt = receipt_fixture.__wrapped__()

    settings, request, _ = setup_values()
    snapshot = setup_snapshot(setup)
    destination = snapshot.researched_candidates[0].location.coordinates
    origin = request.origin.location.coordinates
    route = RouteAvailable(
        Point(origin.latitude, origin.longitude),
        Point(destination.latitude, destination.longitude),
        request.travel.mode,
        300.0,
        250.0,
        Provenance("{}"),
    )

    async def get_route(self, *args, **kwargs):
        return Completed(route, receipt.model_copy(update={"capability": "route"}))

    monkeypatch.setattr(GeoapifyClient, "route", get_route)
    async with AsyncOpenAI(api_key="synthetic-openai-key", max_retries=0) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        owner = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            snapshot_tools(setup),
            GeoapifyClient(calls),
            {},
            candidate_sources={"branch_a": "geoapify_geography"},
        )
        tool = next(t for t in owner.agent.tools if t.name == "route_to_place")
        args = json.dumps({"place_id": "branch_a"})
        result = json.loads(await tool.on_invoke_tool(call_context(tool, args), args))
    assert result["duration_seconds"] == 300.0
    assert owner.scope.routes["branch_a"].route == route
    assert owner.scope.routes["branch_a"].constraint == request.travel


@pytest.mark.asyncio
async def test_owner_assesses_findings_with_same_attempt_and_bound_review(setup):
    settings, request, review = setup_values()
    sample = review.sample
    matching = replace(
        sample.reviews[0],
        review_id="secret",
        link_basis="review",
        author_name=None,
        author_url=None,
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
        source_url="https://google.com/SECRET_REVIEW",
    )
    review = replace(review, sample=replace(sample, reviews=(matching,)))
    snapshot = setup_snapshot(setup)
    research_tools = snapshot_tools(setup)
    responses = iter(
        [
            model_response(
                {
                    "findings": [
                        {
                            "review_label": "r0",
                            "topic": "service",
                            "polarity": "negative",
                            "wording": "paraphrase",
                            "statement": "Service was slow.",
                            "basis": None,
                        }
                    ],
                    "gaps": [],
                }
            ),
            model_response(
                {
                    "decisions": [
                        {
                            "proposal_id": "p0",
                            "review_label": "r0",
                            "verdict": "supported",
                            "rationale": "The review says so.",
                        }
                    ]
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
        specialist = prepare_review_tool(calls, client, request, review)
        owner = prepare_owner(
            calls,
            client,
            request,
            snapshot,
            research_tools,
            GeoapifyClient(calls),
            {"branch_a": specialist},
            candidate_sources={"branch_a": "geoapify_geography"},
            assess_review_findings=True,
        )
        tool = next(item for item in owner.agent.tools if item.name == "research_place")
        args = json.dumps({"place_id": "branch_a", "question": "What did diners say?"})
        output = json.loads(await tool.on_invoke_tool(call_context(tool, args), args))
    assert output["proposals"][0]["support"] == "unverified"
    assert len(owner.scope.assessments) == 1
    assert owner.scope.assessments[0][2].decisions[0].verdict == "supported"
    assert len(calls.receipts) == 2
