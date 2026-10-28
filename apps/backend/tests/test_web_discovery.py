import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.calls import AttemptCalls, Bounds, Completed, Stopped
from app.core.settings import Settings
from app.providers.web_research import (
    NoEvidence,
    ResearchUnavailable,
    WebLimits,
    WebResearchClient,
)
from app.web_discovery import WebDiscoveryBatch, WebDiscoveryQuery, WebMealDiscovery


def response(text: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [
            {
                "id": "synthetic-search",
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "query": "synthetic local meals"},
            },
            {
                "id": "synthetic-answer",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": text, "annotations": citations}
                ],
            },
        ],
    }


def citation(text: str, phrase: str, url: str) -> dict[str, Any]:
    start = text.index(phrase)
    return {
        "type": "url_citation",
        "title": "Synthetic source title",
        "url": url,
        "start_index": start,
        "end_index": start + len(phrase),
    }


def discovery(
    body: dict[str, Any],
    *,
    requests: int = 1,
) -> tuple[WebMealDiscovery, AttemptCalls, list[httpx.Request]]:
    settings = Settings.model_construct(
        mode="test",
        web_provider="openai",
        research_model="gpt-4o",
        model_timeout_seconds=1.0,
        openai_api_key=SecretStr("synthetic-openai-key"),
    )
    calls = AttemptCalls(settings, Bounds(max_requests=requests, total_seconds=3.0))
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=body)

    provider = WebResearchClient(
        calls,
        limits=WebLimits(max_output_tokens=512, max_tool_calls=2),
        test_transport=httpx.MockTransport(respond),
    )
    return WebMealDiscovery(provider), calls, captured


@pytest.mark.asyncio
async def test_local_language_leads_keep_exact_annotations_without_identity() -> None:
    text = (
        "LEAD 架空食堂 (Kakuu Shokudo), synthetic station east exit, rice meals.\n"
        "LEAD 架空屋台, branch/address unknown, serves noodles."
    )
    sources = [
        citation(text, "架空食堂", "https://outlet.invalid/menu"),
        citation(text, "架空屋台", "https://reporting.invalid/stalls"),
    ]
    service, calls, captured = discovery(response(text, sources))
    result = await service.discover(
        WebDiscoveryQuery(locality="Synthetic Kokura station"),
        input_sources=("geoapify_geography",),
    )
    assert isinstance(result, Completed)
    assert isinstance(result.value, WebDiscoveryBatch)
    first, second = result.value.leads
    assert first.excerpt == (
        "架空食堂 (Kakuu Shokudo), synthetic station east exit, rice meals."
    )
    assert second.excerpt == "架空屋台, branch/address unknown, serves noodles."
    assert first.status == second.status == "lead"
    assert first.source_permission == result.value.source_permission == "unchecked"
    assert (first.output_index, first.content_index, first.start_index) == (1, 0, 5)
    assert first.citations[0].url == "https://outlet.invalid/menu"
    assert second.citations[0].url == "https://reporting.invalid/stalls"
    assert first.citations[0].start_index == 5
    assert second.citations[0].start_index == text.index("架空屋台")
    assert text[second.start_index : second.end_index] == second.excerpt
    assert result.value.unmapped_lines == result.value.capped_leads == 0
    request = json.loads(captured[0].content)
    assert 'Locality as JSON data: "Synthetic Kokura station"' in request["input"]
    assert "relevant local languages/scripts" in request["input"]
    assert "stalls, food courts, cafes and takeaway" in request["input"]
    assert "branch identity" in request["input"]
    assert request["max_tool_calls"] == 2
    assert request["store"] is False
    assert result.receipt.input_sources == ("geoapify_geography",)
    assert len(calls.receipts) == len(captured) == 1
    safe = json.dumps(result.receipt.safe_record())
    assert "架空" not in safe and "outlet.invalid" not in safe
    assert "架空" not in repr(result.value) and "outlet.invalid" not in repr(first)


@pytest.mark.asyncio
async def test_uncited_forged_urls_and_other_line_citations_are_not_leads() -> None:
    text = (
        "Overview with a citation.\n"
        "LEAD Invented Outlet https://fabricated.invalid/no-annotation\n"
        "LEAD Cross-line Outlet\n"
        "LEAD Cited Outlet, location unknown, meal clue."
    )
    sources = [
        citation(text, "Overview", "https://overview.invalid"),
        citation(text, "Cited Outlet", "https://source.invalid/actual"),
    ]
    cross = citation(text, "Cross-line Outlet\nLEAD", "https://cross.invalid")
    service, _, _ = discovery(response(text, sources + [cross]))
    result = await service.discover(
        WebDiscoveryQuery(locality="Anywhere"), input_sources=()
    )
    assert isinstance(result, Completed) and isinstance(result.value, WebDiscoveryBatch)
    assert [lead.excerpt for lead in result.value.leads] == [
        "Cited Outlet, location unknown, meal clue."
    ]
    assert [c.url for c in result.value.leads[0].citations] == [
        "https://source.invalid/actual"
    ]
    assert result.value.unmapped_lines == 3


@pytest.mark.asyncio
async def test_source_exclusion_and_lead_cap_are_explicit() -> None:
    text = "LEAD Excluded\nLEAD First\nLEAD Second"
    sources = [
        citation(text, "Excluded", "https://en.wikivoyage.org/wiki/Synthetic"),
        citation(text, "First", "https://first.invalid"),
        citation(text, "Second", "https://second.invalid"),
    ]
    service, _, _ = discovery(response(text, sources))
    result = await service.discover(
        WebDiscoveryQuery(locality="Anywhere", max_leads=1), input_sources=()
    )
    assert isinstance(result, Completed) and isinstance(result.value, WebDiscoveryBatch)
    assert [lead.excerpt for lead in result.value.leads] == ["First"]
    assert result.value.excluded_source_lines == 1
    assert result.value.capped_leads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["no_evidence", "incomplete", "unmapped"])
async def test_empty_mapping_is_distinct_from_absence_and_unavailability(
    kind: str,
) -> None:
    if kind == "no_evidence":
        body = response("NO_SUPPORTED_EVIDENCE", [])
    elif kind == "incomplete":
        body = {"status": "incomplete", "output": []}
    else:
        text = "Unsupported output format with a citation."
        body = response(text, [citation(text, "citation", "https://source.invalid")])
    service, _, _ = discovery(body)
    result = await service.discover(
        WebDiscoveryQuery(locality="Anywhere"), input_sources=()
    )
    assert isinstance(result, Completed)
    if kind == "no_evidence":
        assert isinstance(result.value, NoEvidence)
    elif kind == "incomplete":
        assert isinstance(result.value, ResearchUnavailable)
        assert result.value.reason == "incomplete"
    else:
        assert isinstance(result.value, WebDiscoveryBatch)
        assert result.value.leads == () and result.value.unmapped_lines == 1
        assert result.value.observation.outcome == "evidence"


@pytest.mark.asyncio
async def test_shared_allowance_and_source_policy_stop_without_transmission() -> None:
    service, calls, captured = discovery(response("NO_SUPPORTED_EVIDENCE", []))
    query = WebDiscoveryQuery(locality="Anywhere")
    blocked = await service.discover(query, input_sources=("unknown_publisher",))
    assert isinstance(blocked, Stopped) and blocked.receipt.outcome == "source_blocked"
    assert captured == []
    completed = await service.discover(query, input_sources=())
    assert isinstance(completed, Completed)
    stopped = await service.discover(query, input_sources=())
    assert isinstance(stopped, Stopped) and stopped.receipt.outcome == "request_limit"
    assert len(captured) == len(calls.receipts) == 1
