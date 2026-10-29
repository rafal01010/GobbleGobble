import json
from pathlib import Path

import httpx2
import pytest
from agents.agent_output import AgentOutputSchema
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.agents.research import ResearchOutput, invoke_review_tool, prepare_review_tool
from app.core.calls import AttemptCalls, Bounds
from app.core.settings import Settings
from app.providers.google_reviews import (
    GoogleReview,
    MatchedBranch,
    ReviewLimits,
    ReviewSample,
)
from app.review_research import CandidateReview
from app.schemas.search import SearchRequest

pytest_plugins = ["test_research_tools"]


def setup_values():
    settings = Settings.model_construct(
        mode="test",
        location_provider="geoapify",
        discovery_provider="geoapify",
        route_provider="geoapify",
        web_provider="openai",
        owner_model="gpt-4o",
        research_model="gpt-4o-mini",
        geographic_timeout_seconds=2.0,
        model_timeout_seconds=2.0,
        openai_api_key=SecretStr("synthetic-openai-key"),
    )
    request = SearchRequest.model_validate_json(
        Path(
            "../../docs/examples/search-contract/request-chosen-adjustable-walking.json"
        ).read_text()
    )
    branch = MatchedBranch(
        feature_id="0x1:0x2",
        source_url="https://maps.google.com/a",
        name="Branch",
        full_address="Address",
        website=None,
        rating=4.0,
        reported_total=50,
        evidence=("target_feature_id",),
    )
    review = GoogleReview(
        review_id="review-a",
        rating=3.0,
        text="私は鶏肉が好き。The chicken was excellent, but the service was slow.",
        text_state="present",
        author_name="A",
        author_url=None,
        source_url="https://maps.google.com/a",
        link_basis="branch",
        published=None,
    )
    sample = ReviewSample(
        branch=branch,
        status="sample",
        termination="sample_limit",
        sort="relevant",
        limits=ReviewLimits(),
        reviews=(review,),
        receipts=(),
        pages_received=1,
        response_bytes=100,
        duplicate_count=0,
        invalid_row_count=0,
        received_row_count=1,
    )
    return (
        settings,
        request,
        CandidateReview("branch_a", "available", "sample_limit", sample),
    )


def model_response(output):
    return {
        "id": "r1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4o-mini",
        "status": "completed",
        "output": [
            {
                "id": "m1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(output),
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


@pytest.mark.asyncio
async def test_registered_specialist_receives_original_sample_and_returns_supported_links():
    settings, request, sample = setup_values()
    seen = []
    findings = [
        {
            "review_label": "r0",
            "topic": "food",
            "polarity": "positive",
            "wording": "paraphrase",
            "statement": "The reviewer liked the chicken.",
            "basis": None,
        },
        {
            "review_label": "r0",
            "topic": "service",
            "polarity": "negative",
            "wording": "quotation",
            "statement": "the service was slow",
            "basis": None,
        },
    ]

    def respond(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(
            200, json=model_response({"findings": findings, "gaps": []})
        )

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        prepared = prepare_review_tool(calls, client, request, sample)
        result = await invoke_review_tool(prepared, "What do diners say?")
    assert result.status == "available"
    assert [p.finding.statement for p in result.contributions[0].proposals] == [
        "The reviewer liked the chicken.",
        "the service was slow",
    ]
    assert result.contributions[0].proposals[1].review.review_id == "review-a"
    assert "私は鶏肉が好き" in json.dumps(seen, ensure_ascii=False)
    assert "service was slow" in json.dumps(seen)
    assert calls.receipts[0].input_sources == (
        "geoapify_geography",
        "public_google_reviews",
    )
    assert (
        seen[0]["text"]["format"]["schema"]
        == AgentOutputSchema(ResearchOutput).json_schema()
    )
    assert (
        prepared.tool.params_json_schema["properties"]["question"]["type"] == "string"
    )


@pytest.mark.asyncio
async def test_failed_following_helper_preserves_previous_contribution():
    settings, request, sample = setup_values()
    valid = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "positive",
                "wording": "quotation",
                "statement": "chicken was excellent",
                "basis": None,
            }
        ],
        "gaps": [],
    }
    invalid = {"findings": [{**valid["findings"][0], "review_label": "r9"}], "gaps": []}
    outputs = iter((valid, invalid))

    def respond(req):
        return httpx2.Response(200, json=model_response(next(outputs)))

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        prepared = prepare_review_tool(calls, client, request, sample)
        first = await invoke_review_tool(prepared, "Describe food")
        second = await invoke_review_tool(
            prepared, "Describe service", prior=first.contributions
        )
    assert first.status == "available"
    assert second.status == "incomplete"
    assert second.contributions == first.contributions


def test_nested_decimal_restriction_check():
    from decimal import Decimal

    from pydantic import BaseModel, Field

    from app.agents.research import schema_restrictions

    class Money(BaseModel):
        amount: Decimal = Field(ge=0)

    class Price(BaseModel):
        price: Money

    schema = AgentOutputSchema(Price).json_schema()
    assert schema_restrictions(schema) == ("minimum",)
    assert schema_restrictions(AgentOutputSchema(ResearchOutput).json_schema()) == ()


@pytest.mark.asyncio
async def test_oversized_review_is_reported_and_later_review_remains_available():
    from dataclasses import replace

    settings, request, candidate = setup_values()
    assert candidate.sample is not None
    first = replace(candidate.sample.reviews[0], text="x" * 10000)
    second = replace(
        candidate.sample.reviews[0], review_id="review-b", text="A good lunch."
    )
    candidate = replace(
        candidate, sample=replace(candidate.sample, reviews=(first, second))
    )
    seen = []

    def respond(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(
            200, json=model_response({"findings": [], "gaps": ["Menu not known"]})
        )

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=5.0))
        prepared = prepare_review_tool(
            calls, client, request, candidate, max_input_bytes=4096
        )
        result = await invoke_review_tool(prepared, "What food is mentioned?")
    assert result.status == "available"
    assert result.contributions[0].omitted_reviews == 1
    assert result.contributions[0].reacquisition_review_ids == ("review-a",)
    from app.agents.research import read_review_chunk

    chunk = read_review_chunk(
        candidate, result.contributions[0].reacquisition_review_ids[0]
    )
    assert chunk["status"] == "available"
    assert chunk["text"] == "x" * len(chunk["text"])
    assert chunk["next_offset"] is not None
    assert "A good lunch." in json.dumps(seen)
    assert "x" * 10000 not in json.dumps(seen)


@pytest.mark.asyncio
async def test_scoped_tools_are_registered_and_called(setup):
    from dataclasses import replace

    from test_research_tools import tools

    settings, request, candidate = setup_values()
    assert candidate.sample is not None
    review = replace(
        candidate.sample.reviews[0],
        review_id="secret",
        text="The chicken was excellent, but service was slow. 私はまた行きたい。",
    )
    candidate = replace(candidate, sample=replace(candidate.sample, reviews=(review,)))
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
        return httpx2.Response(200, json=model_response({"findings": [], "gaps": []}))

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        prepared = prepare_review_tool(
            calls, client, request, candidate, research_tools=tools(setup)
        )
        outcome = await invoke_review_tool(prepared, "Inspect the sample")
    assert outcome.status == "available"
    assert len(calls.receipts) == 2
    assert "read_review_evidence" in [tool["name"] for tool in seen[0]["tools"]]
    assert "The chicken was excellent" in json.dumps(seen[1], ensure_ascii=False)


def test_schema_check_rejects_nested_patterns():
    from pydantic import BaseModel, Field

    from app.agents.research import schema_restrictions

    class Nested(BaseModel):
        code: str = Field(pattern="^src_")

    class Container(BaseModel):
        nested: Nested

    assert schema_restrictions(AgentOutputSchema(Container).json_schema()) == (
        "pattern",
    )


@pytest.mark.asyncio
async def test_unverified_claim_and_quote_keep_original_review_context():
    from agents.tool_context import ToolContext

    settings, request, candidate = setup_values()
    output = {
        "findings": [
            {
                "review_label": "r0",
                "topic": "food",
                "polarity": "negative",
                "wording": "paraphrase",
                "statement": "The chicken poisoned the diner.",
                "basis": None,
            },
            {
                "review_label": "r0",
                "topic": "service",
                "polarity": "positive",
                "wording": "quotation",
                "statement": "the service was slow",
                "basis": None,
            },
        ],
        "gaps": [],
    }

    def respond(req):
        return httpx2.Response(200, json=model_response(output))

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=5.0))
        prepared = prepare_review_tool(calls, client, request, candidate)
        args = '{"question":"Interpret the review"}'
        context = ToolContext(
            context=None,
            tool_name=prepared.tool.name,
            tool_call_id="call",
            tool_arguments=args,
        )
        returned = json.loads(await prepared.tool.on_invoke_tool(context, args))
    assert "findings" not in returned
    assert [p["support"] for p in returned["proposals"]] == ["unverified", "unverified"]
    assert returned["proposals"][1]["quote_matches_source"] is True
    assert "but the service was slow" in returned["proposals"][1]["original_review"]
    from app.agents.research import assess_review_proposal

    proposal = prepared.contributions[0].proposals[0]
    decision = assess_review_proposal(
        proposal,
        support="unsupported",
        assessor="reviewer",
        rationale="The source describes a positive experience.",
    )
    assert decision.support == "unsupported"


@pytest.mark.asyncio
async def test_oversized_output_keeps_previous_sibling():
    settings, request, candidate = setup_values()
    valid = {
        "review_label": "r0",
        "topic": "food",
        "polarity": "positive",
        "wording": "paraphrase",
        "statement": "The chicken was good.",
        "basis": None,
    }
    responses = iter(
        (
            {"findings": [valid], "gaps": []},
            {"findings": [{**valid, "statement": "x" * 1900}] * 20, "gaps": []},
        )
    )

    def respond(req):
        return httpx2.Response(200, json=model_response(next(responses)))

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        prepared = prepare_review_tool(calls, client, request, candidate)
        first = await invoke_review_tool(prepared, "Food")
        second = await invoke_review_tool(prepared, "Food", prior=first.contributions)
    assert first.status == "available"
    assert second.status == "incomplete"
    assert second.contributions == first.contributions
    assert len(prepared.contributions) == 1


def test_oversized_review_can_be_read_in_bounded_chunks():
    from dataclasses import replace

    from app.agents.research import read_review_chunk

    _, _, candidate = setup_values()
    assert candidate.sample is not None
    original = "日本語のレビュー。" * 500
    review = replace(candidate.sample.reviews[0], text=original)
    candidate = replace(candidate, sample=replace(candidate.sample, reviews=(review,)))
    offset = 0
    pieces = []
    while True:
        chunk = read_review_chunk(
            candidate, review.review_id, offset=offset, max_bytes=512
        )
        assert chunk["status"] == "available"
        assert len(json.dumps(chunk, ensure_ascii=False).encode()) <= 512
        pieces.append(chunk["text"])
        next_offset = chunk["next_offset"]
        if next_offset is None:
            break
        offset = next_offset
    assert "".join(pieces) == original
    assert read_review_chunk(candidate, "wrong")["status"] == "unavailable"
