import gzip
import json
import logging
from copy import deepcopy
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.calls import AttemptCalls, Bounds, Completed, Stopped
from app.core.settings import Settings
from app.providers.web_research import (
    EvidenceFound,
    FindInPageAction,
    NoEvidence,
    OpenPageAction,
    ResearchUnavailable,
    SearchAction,
    WebLimits,
    WebResearchClient,
    WebResearchInputError,
    WebResearchPreflightError,
)


@pytest.fixture
def settings() -> Settings:
    return Settings.model_construct(
        mode="test",
        location_provider="geoapify",
        discovery_provider="geoapify",
        route_provider="geoapify",
        web_provider="openai",
        owner_model="gpt-4o",
        research_model="exact-synthetic-research-model",
        geographic_timeout_seconds=1.0,
        model_timeout_seconds=1.0,
        openai_api_key=SecretStr("synthetic-openai-key"),
    )


def attempt(settings: Settings, requests: int = 1) -> AttemptCalls:
    return AttemptCalls(settings, Bounds(max_requests=requests, total_seconds=3.0))


def payload() -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [
            {
                "id": "search-private",
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "type": "search",
                    "queries": ["private query 東京"],
                    "sources": [
                        {"type": "url", "url": "https://consulted.invalid/raw"}
                    ],
                },
            },
            {
                "id": "message-private",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "東京 evidence  ",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "title": "Exact private title  ",
                                "url": "https://cited.invalid/raw?a=1",
                                "start_index": 0,
                                "end_index": 2,
                            }
                        ],
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 30,
            "output_tokens": 20,
            "total_tokens": 50,
            "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 5},
        },
    }


def client_for(settings: Settings, body: dict[str, Any]) -> WebResearchClient:
    return WebResearchClient(
        attempt(settings),
        limits=WebLimits(max_output_tokens=64, max_tool_calls=4),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body)
        ),
    )


@pytest.mark.asyncio
async def test_preserves_real_annotations_sources_positions_usage_and_request_policy(
    settings: Settings,
) -> None:
    captured: list[httpx.Request] = []
    body = payload()
    body["output"].insert(
        0,
        {
            "type": "reasoning",
            "id": "private-reasoning",
            "summary": [],
            "encrypted_content": "private-encrypted",
            "status": "completed",
        },
    )
    body["output"][2]["content"].insert(
        0,
        {
            "type": "output_text",
            "text": "intro",
            "annotations": [],
        },
    )

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=body)

    calls = attempt(settings)
    client = WebResearchClient(
        calls,
        limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        test_transport=httpx.MockTransport(respond),
    )
    result = await client.search(
        "private user query 東京", input_sources=("geoapify_geography",)
    )
    assert isinstance(result, Completed)
    assert isinstance(result.value, EvidenceFound)
    assert [
        (block.output_index, block.content_index, block.text)
        for block in result.value.blocks
    ] == [
        (2, 0, "intro"),
        (2, 1, "東京 evidence  "),
    ]
    citation = result.value.blocks[1].citations[0]
    assert (citation.start_index, citation.end_index, citation.title, citation.url) == (
        0,
        2,
        "Exact private title  ",
        "https://cited.invalid/raw?a=1",
    )
    hosted = result.value.hosted_calls[0]
    assert (hosted.output_index, hosted.call_id, hosted.status) == (
        1,
        "search-private",
        "completed",
    )
    assert isinstance(hosted.action, SearchAction)
    assert hosted.action.queries == ("private query 東京",)
    assert hosted.action.consulted_urls == ("https://consulted.invalid/raw",)
    assert result.value.source_permission == "unchecked"
    observed = result.value.observation
    assert (
        observed.observed_tool_calls,
        observed.completed_tool_calls,
        observed.completed_search_actions,
        observed.outcome,
    ) == (1, 1, 1, "evidence")
    assert result.receipt.usage is not None
    assert result.receipt.usage.model_dump() == {
        "input_tokens": 30,
        "output_tokens": 20,
        "requests": 1,
    }
    assert result.receipt.cost is None
    assert (
        result.receipt.capability,
        result.receipt.use,
        result.receipt.model_role,
    ) == (
        "web_research",
        "model_inference",
        "research",
    )
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer synthetic-openai-key"
    sent = json.loads(request.content)
    assert sent["model"] == "exact-synthetic-research-model"
    assert sent["input"] == "private user query 東京"
    assert sent["tools"] == [
        {
            "type": "web_search",
            "external_web_access": True,
            "search_context_size": "low",
        }
    ]
    assert sent["include"] == ["web_search_call.action.sources"]
    assert sent["tool_choice"] == "required"
    assert (sent["max_output_tokens"], sent["max_tool_calls"]) == (64, 1)
    assert all(
        sent[key] is False
        for key in ("store", "background", "stream", "parallel_tool_calls")
    )
    assert all(0 < seconds <= 1 for seconds in request.extensions["timeout"].values())
    safe = (
        json.dumps(result.receipt.safe_record())
        + repr(result)
        + repr(result.value.blocks)
        + repr(hosted)
    )
    for private in (
        "private user",
        "東京",
        "cited.invalid",
        "private-reasoning",
        "search-private",
        "synthetic-openai-key",
    ):
        assert private not in safe
    stopped = await client.search("second", input_sources=())
    assert isinstance(stopped, Stopped)
    assert stopped.receipt.outcome == "request_limit"
    assert len(captured) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sources, expected", [(None, None), ([], ())])
async def test_distinguishes_absent_and_empty_sources(
    settings: Settings,
    sources: list[dict[str, str]] | None,
    expected: tuple[str, ...] | None,
) -> None:
    body = payload()
    if sources is None:
        del body["output"][0]["action"]["sources"]
    else:
        body["output"][0]["action"]["sources"] = sources
    result = await client_for(settings, body).search("query", input_sources=())
    assert isinstance(result, Completed) and isinstance(result.value, EvidenceFound)
    action = result.value.hosted_calls[0].action
    assert isinstance(action, SearchAction)
    assert action.consulted_urls == expected
    assert result.value.blocks[0].citations[0].url == "https://cited.invalid/raw?a=1"


@pytest.mark.asyncio
async def test_preserves_open_and_find_without_fabricating_searches(
    settings: Settings,
) -> None:
    body = payload()
    body["output"].extend(
        [
            {
                "id": "open-id",
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "open_page", "url": None},
            },
            {
                "id": "find-id",
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "type": "find_in_page",
                    "url": "https://page.invalid/",
                    "pattern": "private pattern",
                },
            },
        ]
    )
    result = await client_for(settings, body).search("query", input_sources=())
    assert isinstance(result, Completed) and isinstance(result.value, EvidenceFound)
    assert isinstance(result.value.hosted_calls[1].action, OpenPageAction)
    find = result.value.hosted_calls[2].action
    assert isinstance(find, FindInPageAction)
    assert (find.url, find.pattern) == ("https://page.invalid/", "private pattern")
    assert result.value.observation.completed_search_actions == 1
    body["output"] = body["output"][1:]
    rejected = await client_for(settings, body).search("query", input_sources=())
    assert isinstance(rejected, Stopped)
    assert rejected.receipt.diagnostic == "invalid_response"


@pytest.mark.asyncio
async def test_exact_sentinel_and_unknown_usage(settings: Settings) -> None:
    body = payload()
    body.pop("usage")
    body["output"][1]["content"] = [
        {"type": "output_text", "text": "NO_SUPPORTED_EVIDENCE", "annotations": []}
    ]
    result = await client_for(settings, body).search("query", input_sources=())
    assert isinstance(result, Completed) and isinstance(result.value, NoEvidence)
    assert result.value.blocks[0].text == "NO_SUPPORTED_EVIDENCE"
    assert result.value.observation.outcome == "no_evidence"
    assert result.receipt.usage is not None
    assert result.receipt.usage.model_dump() == {
        "input_tokens": None,
        "output_tokens": None,
        "requests": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, reason",
    [
        ("refusal", "refused"),
        ("incomplete", "incomplete"),
        ("failed", "provider_failed"),
        ("tool_failed", "provider_failed"),
        ("tool_incomplete", "incomplete"),
    ],
)
async def test_unavailable_drops_partial_evidence(
    settings: Settings, kind: str, reason: str
) -> None:
    body = payload()
    if kind == "refusal":
        body["output"][1]["content"].append(
            {"type": "refusal", "refusal": "private refusal"}
        )
    elif kind.startswith("tool_"):
        body["output"][0]["status"] = kind.removeprefix("tool_")
    else:
        body["status"] = kind
    result = await client_for(settings, body).search("query", input_sources=())
    assert isinstance(result, Completed) and isinstance(
        result.value, ResearchUnavailable
    )
    assert result.value.reason == reason
    assert result.value.observation.outcome == reason
    assert not hasattr(result.value, "blocks")
    assert result.value.hosted_calls[0].call_id == "search-private"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "no_search",
        "no_citations",
        "empty",
        "whitespace_sentinel",
        "mixed_sentinel",
        "bad_status",
        "running_tool",
        "unknown_output",
        "unknown_action",
        "unknown_annotation",
        "negative_range",
        "reversed_range",
        "long_range",
        "bool_range",
        "blank_annotated_text",
        "bad_url",
        "credential_url",
        "space_url",
        "bad_source",
        "bool_usage",
        "negative_usage",
        "incoherent_usage",
        "token_cap",
        "tool_cap",
        "bad_reasoning",
        "invalid_json",
    ],
)
async def test_invalid_provider_responses_have_fixed_diagnostics(
    settings: Settings, case: str
) -> None:
    body = deepcopy(payload())
    text = body["output"][1]["content"][0]
    citation = text["annotations"][0]
    if case == "no_search":
        body["output"].pop(0)
    elif case == "no_citations":
        text["annotations"] = []
    elif case == "empty":
        body["output"][1]["content"] = []
    elif case == "whitespace_sentinel":
        text.update(text=" NO_SUPPORTED_EVIDENCE", annotations=[])
    elif case == "mixed_sentinel":
        text["text"] = "NO_SUPPORTED_EVIDENCE cited"
    elif case == "bad_status":
        body["status"] = "queued"
    elif case == "running_tool":
        body["output"][0]["status"] = "searching"
    elif case == "unknown_output":
        body["output"].append({"type": "function_call"})
    elif case == "unknown_action":
        body["output"][0]["action"]["type"] = "invented"
    elif case == "unknown_annotation":
        citation["type"] = "file_citation"
    elif case == "negative_range":
        citation["start_index"] = -1
    elif case == "reversed_range":
        citation["end_index"] = 0
    elif case == "long_range":
        citation["end_index"] = 1000
    elif case == "bool_range":
        citation["start_index"] = False
    elif case == "blank_annotated_text":
        text["text"] = "  "
    elif case in {"bad_url", "credential_url", "space_url"}:
        citation["url"] = {
            "bad_url": "file:///tmp/private",
            "credential_url": "https://secret@host.invalid/",
            "space_url": "https://host.invalid/private path",
        }[case]
    elif case == "bad_source":
        body["output"][0]["action"]["sources"][0]["url"] = "javascript:private"
    elif case == "bool_usage":
        body["usage"]["input_tokens"] = True
    elif case == "negative_usage":
        body["usage"]["output_tokens"] = -1
    elif case == "incoherent_usage":
        body["usage"]["total_tokens"] = 999
    elif case == "token_cap":
        body["usage"].update(output_tokens=65, total_tokens=95)
    elif case == "tool_cap":
        body["output"] = [body["output"][0]] * 5 + body["output"][1:]
    elif case == "bad_reasoning":
        body["output"].append(
            {
                "type": "reasoning",
                "id": "r",
                "summary": [{"type": "invented", "text": "private"}],
            }
        )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"private-invalid-json"
            if case == "invalid_json"
            else json.dumps(body).encode(),
        )
    )
    result = await WebResearchClient(
        attempt(settings),
        limits=WebLimits(max_output_tokens=64, max_tool_calls=4),
        test_transport=transport,
    ).search("private query", input_sources=())
    assert isinstance(result, Stopped)
    assert (result.receipt.outcome, result.receipt.diagnostic) == (
        "failed",
        "invalid_response",
    )
    assert "private" not in json.dumps(result.receipt.safe_record())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 400, 429, 500])
async def test_http_errors_do_not_become_valid_provider_outcomes(
    settings: Settings, status: int
) -> None:
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://publisher.invalid/private"},
            text="private provider prose",
        )

    result = await WebResearchClient(
        attempt(settings),
        limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        test_transport=httpx.MockTransport(respond),
    ).search("query", input_sources=())
    assert isinstance(result, Stopped)
    assert (result.receipt.outcome, result.receipt.diagnostic) == (
        "failed",
        "unknown_failure",
    )
    assert len(captured) == 1
    assert "private" not in json.dumps(result.receipt.safe_record())


@pytest.mark.asyncio
async def test_decompressed_body_bound_and_permanent_content_filters(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    body = gzip.compress(b"x" * (1024 * 1024 + 1))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-encoding": "gzip"}, content=body
        )
    )
    result = await WebResearchClient(
        attempt(settings),
        limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        test_transport=transport,
    ).search("private query", input_sources=())
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "failed"
    with caplog.at_level(logging.DEBUG):
        for name in (
            "httpx",
            "httpcore.connection",
            "httpcore.http11",
            "httpcore.http2",
        ):
            logging.getLogger(name).error("private content after call")
        logging.getLogger("synthetic.control").error("control visible")
    assert "control visible" in caplog.text
    assert "private content" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["ordinary_google", "grounding_lite", "unknown_publisher"]
)
async def test_source_admission_blocks_transmission(
    settings: Settings, source: Any
) -> None:
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=payload())

    client = WebResearchClient(
        attempt(settings),
        limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        test_transport=httpx.MockTransport(respond),
    )
    result = await client.search("query", input_sources=(source,))
    assert isinstance(result, Stopped)
    assert (result.receipt.outcome, result.receipt.admitted) == (
        "source_blocked",
        False,
    )
    assert captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, key, transport, code",
    [
        ("diagnostic", True, False, "live_mode_required"),
        ("test", True, False, "test_transport_required"),
        ("live", True, True, "test_transport_forbidden"),
        ("test", False, True, "openai_key_missing"),
    ],
)
async def test_preflight_errors_are_fixed(
    settings: Settings, mode: Any, key: bool, transport: bool, code: str
) -> None:
    configured = settings.model_copy(
        update={
            "mode": mode,
            "openai_api_key": settings.openai_api_key if key else None,
        }
    )
    calls = attempt(configured)
    client = WebResearchClient(
        calls,
        limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload())
        )
        if transport
        else None,
    )
    with pytest.raises(WebResearchPreflightError) as caught:
        await client.search("query", input_sources=())
    assert str(caught.value) == code
    assert calls.receipts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query, sources",
    [
        ("", ()),
        ("  ", ()),
        ("x" * 2001, ()),
        (False, ()),
        ("query", []),
        ("query", ("private-source",)),
    ],
)
async def test_invalid_inputs_fail_before_admission(
    settings: Settings, query: Any, sources: Any
) -> None:
    calls = attempt(settings)
    client = WebResearchClient(
        calls, limits=WebLimits(max_output_tokens=64, max_tool_calls=1)
    )
    with pytest.raises(WebResearchInputError, match="^invalid web research request$"):
        await client.search(query, input_sources=sources)
    assert calls.receipts == ()


@pytest.mark.parametrize(
    "tokens, tools", [(15, 1), (4097, 1), (16, 0), (16, 5), (True, 1), (16, True)]
)
def test_limits_require_strict_bounded_integers(tokens: Any, tools: Any) -> None:
    with pytest.raises(ValidationError):
        WebLimits(max_output_tokens=tokens, max_tool_calls=tools)
