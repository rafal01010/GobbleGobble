import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.calls import AttemptCalls, Bounds
from app.core.settings import Settings
from app.providers.web_research import WebLimits, WebResearchInputError
from app.review_selection import ReviewSourceSelector
from app.schemas.search import Coordinates, Place, ResolvedLocation


def place():
    return Place(
        place_id="one",
        kind="branch",
        name="Example",
        location=ResolvedLocation(
            coordinates=Coordinates(latitude=1.0, longitude=2.0),
            timezone="UTC",
            formatted_address="1 Main St",
        ),
    )


def settings():
    return Settings.model_construct(
        mode="test",
        location_provider="geoapify",
        discovery_provider="geoapify",
        route_provider="geoapify",
        review_provider="google_reviews",
        review_timeout_seconds=1.0,
        web_provider="openai",
        owner_model="gpt-4o",
        research_model="gpt-4o-mini",
        geographic_timeout_seconds=1.0,
        model_timeout_seconds=1.0,
        openai_api_key=SecretStr("test-key"),
    )


def evidence(host="food.example"):
    url = f"https://{host}/branch"
    return {
        "status": "completed",
        "output": [
            {
                "id": "search",
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "sources": [{"type": "url", "url": url}]},
            },
            {
                "id": "message",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Soup is popular.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "title": "Guide",
                                "url": url,
                                "start_index": 0,
                                "end_index": 4,
                            }
                        ],
                    }
                ],
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
    }


@pytest.mark.asyncio
async def test_quota_falls_back_with_labeled_restricted_search():
    requests = []

    def google(request):
        requests.append("google")
        return httpx.Response(429)

    def web(request):
        requests.append("web")
        assert json.loads(request.content)["tools"][0]["filters"] == {
            "allowed_domains": ["food.example"]
        }
        return httpx.Response(200, json=evidence())

    calls = AttemptCalls(settings(), Bounds(max_requests=2, total_seconds=2.0))
    selector = ReviewSourceSelector(
        calls,
        web_limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        google_test_transport=httpx.MockTransport(google),
        web_test_transport=httpx.MockTransport(web),
    )
    result = await selector.collect(
        place(),
        independent_domains=("food.example",),
        input_sources=("geoapify_geography",),
    )
    assert result.primary.availability == "provider_failure"
    assert result.primary.reason == "quota"
    assert result.fallback_status == "evidence"
    assert result.fallback_source == "independent_web"
    assert result.fallback.source_permission == "unchecked"
    assert result.fallback.blocks[0].citations[0].url == "https://food.example/branch"
    assert requests == ["google", "web"]
    assert len(calls.receipts) == 2


@pytest.mark.asyncio
async def test_budget_and_disallowed_domain_do_not_dispatch_fallback():
    calls = AttemptCalls(settings(), Bounds(max_requests=1, total_seconds=2.0))
    selector = ReviewSourceSelector(
        calls,
        web_limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        google_test_transport=httpx.MockTransport(lambda request: httpx.Response(429)),
        web_test_transport=httpx.MockTransport(
            lambda request: pytest.fail("unexpected fallback")
        ),
    )
    with pytest.raises(WebResearchInputError):
        await selector.collect(
            place(), independent_domains=("maps.google.com",), input_sources=()
        )
    result = await selector.collect(
        place(), independent_domains=("food.example",), input_sources=()
    )
    assert result.fallback_status == "budget_exhausted"
    assert len(calls.receipts) == 1


@pytest.mark.asyncio
async def test_unpermitted_citation_is_not_returned():
    calls = AttemptCalls(settings(), Bounds(max_requests=2, total_seconds=2.0))
    selector = ReviewSourceSelector(
        calls,
        web_limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        google_test_transport=httpx.MockTransport(lambda request: httpx.Response(429)),
        web_test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=evidence("maps.google.com"))
        ),
    )
    result = await selector.collect(
        place(), independent_domains=("food.example",), input_sources=()
    )
    assert result.fallback_status == "restricted"
    assert result.fallback is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "availability",
    [
        "zero_reviews",
        "empty_sample",
        "omitted_sample",
        "no_match",
        "restricted",
        "missing_access",
        "timeout",
        "provider_failure",
        "budget_exhausted",
    ],
)
async def test_primary_reasons_are_preserved_without_permitted_alternatives(
    monkeypatch,
    availability,
):
    from app.review_research import CandidateReview, PrimaryReviewClient

    async def primary(self, target, *, limits=None):
        return CandidateReview(target.place_id, availability, "specific_reason")

    monkeypatch.setattr(PrimaryReviewClient, "collect", primary)
    calls = AttemptCalls(settings(), Bounds(max_requests=1, total_seconds=2.0))
    selector = ReviewSourceSelector(
        calls, web_limits=WebLimits(max_output_tokens=64, max_tool_calls=1)
    )
    result = await selector.collect(place(), independent_domains=(), input_sources=())
    assert result.primary.availability == availability
    assert result.primary.reason == "specific_reason"
    assert result.fallback_status == "no_permitted_domains"
    assert calls.receipts == ()


@pytest.mark.asyncio
async def test_primary_recovery_avoids_fallback(monkeypatch):
    from app.review_research import CandidateReview, PrimaryReviewClient

    async def primary(self, target, *, limits=None):
        return CandidateReview(target.place_id, "available", "sample_limit")

    monkeypatch.setattr(PrimaryReviewClient, "collect", primary)
    calls = AttemptCalls(settings(), Bounds(max_requests=1, total_seconds=2.0))
    selector = ReviewSourceSelector(
        calls,
        web_limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        web_test_transport=httpx.MockTransport(
            lambda request: pytest.fail("unexpected fallback")
        ),
    )
    result = await selector.collect(
        place(), independent_domains=("food.example",), input_sources=()
    )
    assert result.primary.availability == "available"
    assert result.fallback_status is None
    assert calls.receipts == ()


@pytest.mark.asyncio
async def test_missing_independent_evidence_is_not_negative_reception():
    empty = evidence()
    empty["output"][1]["content"][0] = {
        "type": "output_text",
        "text": "NO_SUPPORTED_EVIDENCE",
        "annotations": [],
    }
    calls = AttemptCalls(settings(), Bounds(max_requests=2, total_seconds=2.0))
    selector = ReviewSourceSelector(
        calls,
        web_limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        google_test_transport=httpx.MockTransport(lambda request: httpx.Response(429)),
        web_test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=empty)
        ),
    )
    result = await selector.collect(
        place(), independent_domains=("food.example",), input_sources=()
    )
    assert result.primary.reason == "quota"
    assert result.fallback_status == "no_evidence"
    assert result.fallback is None


@pytest.mark.asyncio
async def test_review_lineage_is_not_reingested_into_fallback():
    calls = AttemptCalls(settings(), Bounds(max_requests=2, total_seconds=2.0))
    selector = ReviewSourceSelector(
        calls,
        web_limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        google_test_transport=httpx.MockTransport(lambda request: httpx.Response(429)),
        web_test_transport=httpx.MockTransport(
            lambda request: pytest.fail("unexpected fallback")
        ),
    )
    result = await selector.collect(
        place(),
        independent_domains=("food.example",),
        input_sources=("public_google_reviews",),
    )
    assert result.primary.reason == "quota"
    assert result.fallback_status == "restricted"
    assert len(calls.receipts) == 1


@pytest.mark.asyncio
async def test_missing_identity_produces_explicit_fallback_gap():
    original = place()
    unnamed = original.model_copy(update={"name": None})
    calls = AttemptCalls(settings(), Bounds(max_requests=2, total_seconds=2.0))
    selector = ReviewSourceSelector(
        calls,
        web_limits=WebLimits(max_output_tokens=64, max_tool_calls=1),
        google_test_transport=httpx.MockTransport(
            lambda request: pytest.fail("unexpected google call")
        ),
        web_test_transport=httpx.MockTransport(
            lambda request: pytest.fail("unexpected fallback")
        ),
    )
    result = await selector.collect(
        unnamed, independent_domains=("food.example",), input_sources=()
    )
    assert result.primary.availability == "no_match"
    assert result.fallback_status == "insufficient_identity"
