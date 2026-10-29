import json

import httpx2
import pytest
from openai import AsyncOpenAI
from test_research_agent import model_response, setup_values

from app.agents.research import invoke_review_tool, prepare_review_tool
from app.agents.research_budget import ResearchBudget
from app.core.calls import AttemptCalls, Bounds


@pytest.mark.asyncio
async def test_shared_turn_limit_preserves_prior_contribution():
    settings, request, sample = setup_values()
    seen = []

    def respond(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(
            200, json=model_response({"findings": [], "gaps": ["No menu"]})
        )

    budget = ResearchBudget(max_turns=1)
    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(
            settings, Bounds(max_requests=4, total_seconds=5.0), research_budget=budget
        )
        first = await invoke_review_tool(
            prepare_review_tool(calls, client, request, sample), "Describe food"
        )
        second = await invoke_review_tool(
            prepare_review_tool(calls, client, request, sample),
            "Describe service",
            prior=first.contributions,
        )
    assert first.status == "available"
    assert second.reason == "turn_limit"
    assert second.contributions == first.contributions
    assert len(seen) == 1
    assert budget.turns == 1 and budget.tools == 2
    assert budget.records[0].actual_input == 10
    assert budget.records[0].actual_output == 20
    assert budget.records[0].estimated_input >= len(
        json.dumps(seen[0], ensure_ascii=False).encode("utf-8")
    )
    assert calls.receipts[-1].diagnostic == "turn_limit"


@pytest.mark.asyncio
async def test_irreducible_request_stops_before_provider():
    settings, request, sample = setup_values()
    seen = []

    def respond(req):
        seen.append(req)
        return httpx2.Response(500)

    budget = ResearchBudget(context_limit=1024)
    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(
            settings, Bounds(max_requests=2, total_seconds=5.0), research_budget=budget
        )
        result = await invoke_review_tool(
            prepare_review_tool(calls, client, request, sample), "Describe food"
        )
    assert result.reason == "context_limit"
    assert seen == []
    assert calls.receipts == ()
    assert budget.turns == 0


@pytest.mark.asyncio
async def test_provider_context_error_charged_with_unknown_usage():
    settings, request, sample = setup_values()
    seen = []

    def respond(req):
        seen.append(req)
        return httpx2.Response(
            400,
            json={
                "error": {
                    "message": "too long",
                    "type": "invalid_request_error",
                    "code": "context_length_exceeded",
                }
            },
        )

    budget = ResearchBudget()
    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(
            settings, Bounds(max_requests=2, total_seconds=5.0), research_budget=budget
        )
        result = await invoke_review_tool(
            prepare_review_tool(calls, client, request, sample), "Describe food"
        )
    assert result.reason == "context_limit"
    assert len(seen) == 1
    assert budget.charged > 0
    assert budget.records[0].actual_input is None
    assert budget.records[0].actual_output is None
    assert budget.records[0].outcome == "context_limit"
    assert calls.receipts[0].diagnostic == "context_limit"


pytest_plugins = ["test_research_tools"]


@pytest.mark.asyncio
async def test_growing_tool_history_defers_complete_pair(setup):
    from dataclasses import replace

    from test_research_tools import tools

    settings, request, sample = setup_values()
    assert sample.sample is not None
    review = replace(
        sample.sample.reviews[0],
        review_id="secret",
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
    )
    sample = replace(sample, sample=replace(sample.sample, reviews=(review,)))
    seen = []
    budget = ResearchBudget()

    def respond(req):
        seen.append(json.loads(req.content))
        if len(seen) == 2:
            return httpx2.Response(
                200, json=model_response({"findings": [], "gaps": []})
            )
        budget.context_limit = len(req.content) + budget.headroom + 1024 + 300
        return httpx2.Response(
            200,
            json={
                "id": "r1",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o-mini",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "f1",
                        "call_id": "c1",
                        "name": "read_review_evidence",
                        "arguments": '{"item_id":"ev_google","offset":0}',
                        "status": "completed",
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            },
        )

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(
            settings, Bounds(max_requests=3, total_seconds=5.0), research_budget=budget
        )
        prepared = prepare_review_tool(
            calls, client, request, sample, research_tools=tools(setup)
        )
        result = await invoke_review_tool(prepared, "Inspect reviews")
    assert result.status == "available"
    assert len(seen) == 2
    assert budget.turns == 2
    assert budget.tools == 2
    assert budget.records[1].deferred_tool_results == 1
    assert "prior review lookup was deferred" in json.dumps(seen[1])
    assert "ev_google" in json.dumps(seen[1])
    assert all(
        item.get("type") not in {"function_call", "function_call_output"}
        for item in seen[1]["input"]
    )


@pytest.mark.asyncio
async def test_nested_tool_limit_stops_without_another_model_call(setup):
    from test_research_tools import tools

    settings, request, sample = setup_values()
    seen = []
    budget = ResearchBudget(max_tools=1)

    def respond(req):
        seen.append(req)
        return httpx2.Response(
            200,
            json={
                "id": "r1",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o-mini",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "f1",
                        "call_id": "c1",
                        "name": "read_review_evidence",
                        "arguments": '{"item_id":"ev_google","offset":0}',
                        "status": "completed",
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            },
        )

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(
            settings, Bounds(max_requests=3, total_seconds=5.0), research_budget=budget
        )
        prepared = prepare_review_tool(
            calls, client, request, sample, research_tools=tools(setup)
        )
        result = await invoke_review_tool(prepared, "Inspect reviews")
    assert result.reason == "tool_limit"
    assert len(seen) == 1
    assert budget.tools == 1
    assert budget.turns == 1


@pytest.mark.asyncio
async def test_oversized_sample_is_fitted_with_continuation():
    from dataclasses import replace

    settings, request, sample = setup_values()
    assert sample.sample is not None
    reviews = tuple(
        replace(
            sample.sample.reviews[0],
            review_id=f"review-{i}",
            text=f"Review {i}. " + "x" * 800,
        )
        for i in range(5)
    )
    sample = replace(sample, sample=replace(sample.sample, reviews=reviews))
    seen = []

    def respond(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(200, json=model_response({"findings": [], "gaps": []}))

    budget = ResearchBudget(context_limit=6000)
    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(
            settings, Bounds(max_requests=3, total_seconds=5.0), research_budget=budget
        )
        first = await invoke_review_tool(
            prepare_review_tool(calls, client, request, sample), "Describe food"
        )
        assert first.status == "available"
        cursor = first.contributions[0].next_index
        assert cursor is not None and 0 < cursor < 5
        second = await invoke_review_tool(
            prepare_review_tool(calls, client, request, sample, start_index=cursor),
            "Describe food",
            prior=first.contributions,
        )
    assert second.status == "available"
    first_request = json.dumps(seen[0])
    second_request = json.dumps(seen[1])
    assert "Review 0." in first_request
    assert f"Review {cursor}." not in first_request
    assert f"Review {cursor}." in second_request
    assert "deferred_reviews" in first_request
    assert len(second.contributions) == 2
    assert budget.tool_records[0].name == "specialist"
    assert budget.tool_records[0].outcome == "success"


@pytest.mark.asyncio
async def test_web_and_specialist_share_model_and_tool_allowances():
    import httpx
    from test_web_research import payload

    from app.core.calls import Completed
    from app.providers.web_research import WebLimits, WebResearchClient

    settings, request, sample = setup_values()
    budget = ResearchBudget(max_turns=1)
    calls = AttemptCalls(
        settings, Bounds(max_requests=3, total_seconds=5.0), research_budget=budget
    )
    web = WebResearchClient(
        calls,
        limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        test_transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=payload())
        ),
    )
    result = await web.search("Find a menu", input_sources=())
    assert isinstance(result, Completed)
    assert budget.records[0].actual_input == 30
    assert budget.records[0].actual_output == 20
    assert budget.tool_records[0].name == "web_search"
    assert budget.tool_records[0].outcome == "success"
    seen = []

    def respond(req):
        seen.append(req)
        return httpx2.Response(500)

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        specialist = await invoke_review_tool(
            prepare_review_tool(calls, client, request, sample), "Describe food"
        )
    assert specialist.reason == "turn_limit"
    assert len(seen) == 0
    assert budget.turns == 1
    assert [item.name for item in budget.tool_records] == ["web_search", "specialist"]


@pytest.mark.asyncio
async def test_web_provider_context_error_keeps_unknown_usage():
    import httpx

    from app.core.calls import Stopped
    from app.providers.web_research import WebLimits, WebResearchClient

    settings, _, _ = setup_values()
    budget = ResearchBudget()
    calls = AttemptCalls(
        settings, Bounds(max_requests=2, total_seconds=5.0), research_budget=budget
    )
    web = WebResearchClient(
        calls,
        limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        test_transport=httpx.MockTransport(
            lambda req: httpx.Response(
                400, json={"error": {"code": "context_length_exceeded"}}
            )
        ),
    )
    result = await web.search("Find a menu", input_sources=())
    assert isinstance(result, Stopped)
    assert result.receipt.diagnostic == "context_limit"
    assert budget.records[0].outcome == "context_limit"
    assert budget.records[0].actual_input is None
    assert budget.charged > 0
    assert budget.unknown_hosted_reservations == [1]
    assert budget.tool_records == []


@pytest.mark.asyncio
async def test_deferred_tool_read_can_support_a_review_proposal(setup):
    from dataclasses import replace

    from test_research_tools import tools

    settings, request, candidate = setup_values()
    assert candidate.sample is not None
    review = replace(
        candidate.sample.reviews[0],
        review_id="secret",
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
    )
    later = replace(review, review_id="later", text="Another review.")
    candidate = replace(
        candidate, sample=replace(candidate.sample, reviews=(review, later))
    )
    seen = []

    def respond(req):
        seen.append(json.loads(req.content))
        if len(seen) == 1:
            return httpx2.Response(
                200,
                json={
                    "id": "r1",
                    "object": "response",
                    "created_at": 1,
                    "model": "gpt-4o-mini",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "f1",
                            "call_id": "c1",
                            "name": "read_review_evidence",
                            "arguments": '{"item_id":"ev_google","offset":0}',
                            "status": "completed",
                        }
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "total_tokens": 30,
                    },
                },
            )
        return httpx2.Response(
            200,
            json=model_response(
                {
                    "findings": [
                        {
                            "review_label": "r0",
                            "topic": "service",
                            "polarity": "negative",
                            "wording": "quotation",
                            "statement": "service was slow",
                            "basis": None,
                        }
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
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        prepared = prepare_review_tool(
            calls,
            client,
            request,
            candidate,
            start_index=1,
            research_tools=tools(setup),
        )
        result = await invoke_review_tool(prepared, "Inspect the reviews")
    assert result.status == "available"
    assert result.contributions[0].proposals[0].review.review_id == "secret"
    assert result.contributions[0].proposals[0].quote_matches_source
    assert "review_label" in json.dumps(seen[1])
