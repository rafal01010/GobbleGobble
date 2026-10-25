import asyncio
import gzip
import json
from dataclasses import replace
from typing import Any

import httpx2
import pytest
from agents.tracing import (
    Span,
    Trace,
    TracingProcessor,
    custom_span,
    get_trace_provider,
    trace,
)
from pydantic import SecretStr

from app.agents.probe import (
    ProbeLimits,
    ProbePreflightError,
    ProbeStopped,
    ProbeUnavailable,
    SDKProbe,
    SuggestionReady,
)
from app.core.calls import AttemptCalls, Bounds
from app.core.settings import Settings
from app.providers.google_reviews import (
    GoogleReview,
    MatchedBranch,
    ReviewLimits,
    ReviewSample,
)

_QUOTE = "The chicken was fresh and the rice was delicious."
_SUGGESTION = {"food": "chicken", "passage_label": "p0", "quote": _QUOTE}


class _TraceRecorder(TracingProcessor):
    def __init__(self) -> None:
        self.events: list[str] = []

    def on_trace_start(self, trace: Trace) -> None:
        self.events.append("trace_start")

    def on_trace_end(self, trace: Trace) -> None:
        self.events.append("trace_end")

    def on_span_start(self, span: Span[Any]) -> None:
        self.events.append("span_start")

    def on_span_end(self, span: Span[Any]) -> None:
        self.events.append("span_end")

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


@pytest.fixture
def settings() -> Settings:
    return Settings.model_construct(
        mode="test",
        location_provider="geoapify",
        discovery_provider="geoapify",
        route_provider="geoapify",
        web_provider="openai",
        owner_model="gpt-4o",
        research_model="gpt-4o-mini",
        geographic_timeout_seconds=1.0,
        model_timeout_seconds=1.0,
        openai_api_key=SecretStr("synthetic-openai-key"),
    )


@pytest.fixture
def sample() -> ReviewSample:
    branch = MatchedBranch(
        feature_id="0x1:0x2",
        source_url="https://www.google.com/maps/place/private-branch",
        name="Private branch",
        full_address="Private address",
        website=None,
        rating=4.5,
        reported_total=10,
        evidence=("target_feature_id",),
    )
    review = GoogleReview(
        review_id="private-id",
        rating=5.0,
        text=_QUOTE,
        text_state="present",
        author_name="Private author",
        author_url="https://www.google.com/maps/contrib/private-author",
        source_url=branch.source_url,
        link_basis="branch",
        published=None,
    )
    return ReviewSample(
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


def response(output: list[dict[str, object]], model: str) -> dict[str, object]:
    return {
        "id": "private-response-id",
        "object": "response",
        "created_at": 1,
        "model": model,
        "status": "completed",
        "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


def message(suggestion: dict[str, str]) -> dict[str, object]:
    return {
        "id": "private-message-id",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [
            {
                "type": "output_text",
                "text": json.dumps(suggestion),
                "annotations": [],
            }
        ],
    }


def first_owner_response(arguments: str = "{}") -> dict[str, object]:
    return response(
        [
            {
                "type": "function_call",
                "id": "private-function-id",
                "call_id": "private-call-id",
                "name": "research_food_sample",
                "arguments": arguments,
                "status": "completed",
            }
        ],
        "gpt-4o",
    )


def calls_for(settings: Settings, requests: int = 3) -> AttemptCalls:
    return AttemptCalls(settings, Bounds(max_requests=requests, total_seconds=3.0))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "violation,expected_requests,expected_reason",
    [
        ("missing_tool", 1, "invalid_model_output"),
        ("wrong_arguments", 1, "sdk_failure"),
        ("repeated_tool", 3, "sdk_failure"),
        ("changed_owner_output", 3, "invalid_model_output"),
    ],
)
async def test_owner_protocol_violations_never_produce_suggestions(
    settings: Settings,
    sample: ReviewSample,
    violation: str,
    expected_requests: int,
    expected_reason: str,
) -> None:
    requests = 0

    def transport(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            if violation == "missing_tool":
                body = response([message(_SUGGESTION)], "gpt-4o")
            else:
                body = first_owner_response(
                    '{"unexpected":"private input"}'
                    if violation == "wrong_arguments"
                    else "{}"
                )
        elif requests == 2:
            body = response([message(_SUGGESTION)], "gpt-4o-mini")
        elif violation == "repeated_tool":
            body = first_owner_response()
        else:
            body = response([message(_SUGGESTION | {"food": "rice"})], "gpt-4o")
        return httpx2.Response(200, json=body)

    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(sample)
    assert isinstance(result, ProbeUnavailable)
    assert result.reason == expected_reason
    assert requests == expected_requests
    assert [item.outcome for item in result.receipts] == ["success"] * expected_requests


@pytest.mark.asyncio
async def test_shared_deadline_stops_sdk_research_without_owner_continuation(
    settings: Settings, sample: ReviewSample
) -> None:
    requests = 0

    async def transport(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx2.Response(200, json=first_owner_response())
        await asyncio.sleep(10)
        return httpx2.Response(
            200, json=response([message(_SUGGESTION)], "gpt-4o-mini")
        )

    calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=0.2))
    result = await SDKProbe(
        calls,
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(sample)
    assert isinstance(result, ProbeStopped)
    assert result.receipt.outcome == "attempt_timeout"
    assert result.receipt.model_role == "research"
    assert [(item.model_role, item.outcome) for item in result.receipts] == [
        ("owner", "success"),
        ("research", "attempt_timeout"),
    ]
    assert requests == 2


@pytest.mark.asyncio
async def test_sdk_cancellation_retains_admitted_research_receipt(
    settings: Settings, sample: ReviewSample
) -> None:
    requests = 0
    research_entered = asyncio.Event()

    async def transport(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx2.Response(200, json=first_owner_response())
        research_entered.set()
        await asyncio.Future()
        return httpx2.Response(
            200, json=response([message(_SUGGESTION)], "gpt-4o-mini")
        )

    calls = calls_for(settings)
    task = asyncio.create_task(
        SDKProbe(
            calls,
            limits=ProbeLimits(max_output_tokens=256),
            test_transport=httpx2.MockTransport(transport),
        ).run(sample)
    )
    await asyncio.wait_for(research_entered.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [(item.model_role, item.outcome) for item in calls.receipts] == [
        ("owner", "success"),
        ("research", "cancelled"),
    ]
    assert all(item.admitted for item in calls.receipts)
    assert requests == 2


@pytest.mark.asyncio
async def test_sdk_owner_calls_actual_research_agent_and_keeps_attribution_local(
    settings: Settings, sample: ReviewSample, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[bytes] = []
    traces = _TraceRecorder()
    monkeypatch.setattr(get_trace_provider(), "_multi_processor", traces)

    def transport(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.content)
        if len(sent) == 1:
            body = first_owner_response("{ }")
        else:
            body = response(
                [message(_SUGGESTION)],
                "gpt-4o-mini" if len(sent) == 2 else "gpt-4o",
            )
        return httpx2.Response(200, json=body)

    calls = calls_for(settings)
    result = await SDKProbe(
        calls,
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(sample)
    assert isinstance(result, SuggestionReady)
    assert result.suggestion.model_dump() == _SUGGESTION
    assert result.branch is sample.branch
    assert [item.model_role for item in result.receipts] == [
        "owner",
        "research",
        "owner",
    ]
    assert [item.outcome for item in result.receipts] == ["success"] * 3
    bodies = [json.loads(item) for item in sent]
    assert [item["model"] for item in bodies] == [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4o",
    ]
    assert bodies[0]["tool_choice"] == {
        "type": "function",
        "name": "research_food_sample",
    }
    for body in bodies:
        assert body["store"] is False
        assert body["background"] is False
        assert body.get("stream", False) is False
        if body["tools"]:
            assert body["parallel_tool_calls"] is False
        else:
            assert "parallel_tool_calls" not in body
    assert bodies[1]["tools"] == []
    wire = b" ".join(sent)
    for private in (b"Private branch", b"Private author", b"private-branch"):
        assert private not in wire
    assert _QUOTE.encode() not in repr(result).encode()
    assert b"Private branch" not in repr(result).encode()
    assert all(item.usage is not None for item in result.receipts)
    assert traces.events == []
    with trace("test tracing control"), custom_span("test tracing control"):
        pass
    assert traces.events == ["trace_start", "span_start", "span_end", "trace_end"]


@pytest.mark.asyncio
async def test_empty_minimized_sample_never_requests_model(
    settings: Settings, sample: ReviewSample
) -> None:
    def forbidden(_request: httpx2.Request) -> httpx2.Response:
        pytest.fail("An empty sample must not transmit.")

    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(forbidden),
    ).run(replace(sample, reviews=()))
    assert result == ProbeUnavailable("no_food_passages", ())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "The food had balanced flavors and excellent texture.",
        "The dish was delicious and beautifully prepared.",
        "The chicken was bland and dry.",
        "If you like bland food, the chicken was fresh and delicious.",
    ],
)
async def test_generic_negative_or_conditional_food_never_requests_model(
    settings: Settings, sample: ReviewSample, text: str
) -> None:
    sent: list[bytes] = []

    def forbidden(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.content)
        return httpx2.Response(200, json=first_owner_response())

    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(forbidden),
    ).run(replace(sample, reviews=(replace(sample.reviews[0], text=text),)))
    assert result == ProbeUnavailable("no_food_passages", ())
    assert sent == []


@pytest.mark.asyncio
async def test_request_limit_counts_individual_model_calls(
    settings: Settings, sample: ReviewSample
) -> None:
    count = 0

    def transport(_request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(200, json=first_owner_response())

    result = await SDKProbe(
        calls_for(settings, requests=1),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(sample)
    assert isinstance(result, ProbeStopped)
    assert result.receipt.outcome == "request_limit"
    assert result.receipt.model_role == "research"
    assert count == 1
    assert len(result.receipts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"quote": "Invented quote."},
        {"passage_label": "p2"},
        {"food": "fish"},
        {"food": "and"},
        {"food": "The"},
        {"food": "delicious"},
        {"food": "ice"},
        {"food": "The chicken"},
        {"food": "chicken was fresh"},
        {"food": "Chicken"},
    ],
)
async def test_research_cannot_return_unsupported_food_or_citations(
    settings: Settings, sample: ReviewSample, change: dict[str, str]
) -> None:
    count = 0

    def transport(_request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        body = (
            first_owner_response()
            if count == 1
            else response([message(_SUGGESTION | change)], "gpt-4o-mini")
        )
        return httpx2.Response(200, json=body)

    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(sample)
    assert isinstance(result, ProbeUnavailable)
    assert result.reason == "invalid_model_output"
    assert count == 2


@pytest.mark.asyncio
async def test_sdk_rejects_generic_food_even_when_it_appears_in_supporting_quote(
    settings: Settings, sample: ReviewSample
) -> None:
    quote = "The chicken was fresh and the food was delicious."
    suggestion = {"food": "food", "passage_label": "p0", "quote": quote}
    sent: list[bytes] = []

    def transport(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.content)
        body = (
            first_owner_response()
            if len(sent) == 1
            else response([message(suggestion)], "gpt-4o-mini")
        )
        return httpx2.Response(200, json=body)

    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(replace(sample, reviews=(replace(sample.reviews[0], text=quote),)))
    assert isinstance(result, ProbeUnavailable)
    assert result.reason == "invalid_model_output"
    assert [receipt.model_role for receipt in result.receipts] == ["owner", "research"]
    assert len(sent) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500])
async def test_sdk_and_runner_do_not_retry(
    settings: Settings, sample: ReviewSample, status: int
) -> None:
    count = 0

    def transport(_request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(
            status, json={"error": {"message": "private error body"}}
        )

    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(sample)
    assert isinstance(result, ProbeStopped)
    assert result.receipt.outcome == "failed"
    assert count == 1
    assert "private error body" not in repr(result)


@pytest.mark.asyncio
async def test_decoded_response_byte_cap_rejects_compressed_content(
    settings: Settings, sample: ReviewSample
) -> None:
    def transport(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=gzip.compress(b"x" * 1048577),
            headers={"content-encoding": "gzip"},
        )

    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(sample)
    assert isinstance(result, ProbeStopped)
    assert result.receipt.diagnostic == "response_limit"


@pytest.mark.asyncio
async def test_oversized_sdk_continuation_is_stopped_before_transmission(
    settings: Settings, sample: ReviewSample
) -> None:
    count = 0

    def transport(_request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        body = (
            first_owner_response("{" + " " * 66000 + "}")
            if count == 1
            else response([message(_SUGGESTION)], "gpt-4o-mini")
        )
        return httpx2.Response(200, json=body)

    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(transport),
    ).run(sample)
    assert isinstance(result, ProbeStopped)
    assert result.receipt.diagnostic == "response_limit"
    assert result.receipt.model_role == "owner"
    assert count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        b"not JSON",
        b'{"status":"failed","output":[]}',
        b'{"status":"incomplete","output":[]}',
        b'{"status":"completed","output":null}',
    ],
)
async def test_malformed_failed_and_incomplete_responses_never_produce_suggestions(
    settings: Settings, sample: ReviewSample, content: bytes
) -> None:
    result = await SDKProbe(
        calls_for(settings),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, content=content)
        ),
    ).run(sample)
    assert isinstance(result, ProbeStopped)
    assert result.receipt.outcome == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["diagnostic", "live"])
async def test_no_implicit_test_transport_or_live_fallback(
    settings: Settings, sample: ReviewSample, mode: str
) -> None:
    configured = settings.model_copy(update={"mode": mode})
    probe = SDKProbe(
        calls_for(configured),
        limits=ProbeLimits(max_output_tokens=256),
        test_transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, json={})
        ),
    )
    with pytest.raises(ProbePreflightError) as error:
        await probe.run(sample)
    assert error.value.code == (
        "live_mode_required" if mode == "diagnostic" else "test_transport_forbidden"
    )
