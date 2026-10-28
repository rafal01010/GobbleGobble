import httpx
import pytest
from pydantic import SecretStr

from app.core.calls import AttemptCalls, Bounds
from app.core.settings import Settings
from app.discovery import MealDiscovery, MealDiscoveryQuery
from app.providers.geoapify import GeoapifyClient, Point


def feature(place_id: str, category: str) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [121.0, 14.6]},
        "properties": {
            "place_id": place_id,
            "name": place_id,
            "categories": [category],
            "datasource": {"attribution": "Synthetic source credit  "},
        },
    }


def client(
    respond: httpx.MockTransport, requests: int = 4
) -> tuple[MealDiscovery, AttemptCalls]:
    settings = Settings.model_construct(
        mode="test",
        location_provider="geoapify",
        discovery_provider="geoapify",
        route_provider="geoapify",
        geographic_timeout_seconds=1.0,
        geoapify_api_key=SecretStr("synthetic-key"),
    )
    calls = AttemptCalls(settings, Bounds(max_requests=requests, total_seconds=3.0))
    return MealDiscovery(GeoapifyClient(calls, test_transport=respond)), calls


@pytest.mark.asyncio
async def test_varied_candidates_continue_past_duplicates_with_exact_attribution() -> (
    None
):
    seen: list[httpx.Request] = []
    batches = [
        [
            feature(" restaurant ", "catering.restaurant"),
            feature("cafe", "catering.cafe"),
        ],
        [feature("cafe", "catering.cafe"), feature("court", "catering.food_court")],
        [feature("takeaway", "catering.fast_food")],
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"type": "FeatureCollection", "features": batches[len(seen) - 1]}
        )

    discovery, calls = client(httpx.MockTransport(respond))
    result = await discovery.discover(
        MealDiscoveryQuery(Point(14.6, 121.0), radius_m=1200, page_size=2)
    )
    assert [lead.place_id for lead in result.candidates] == [
        " restaurant ",
        "cafe",
        "court",
        "takeaway",
    ]
    assert [lead.categories for lead in result.candidates] == [
        ("catering.restaurant",),
        ("catering.cafe",),
        ("catering.food_court",),
        ("catering.fast_food",),
    ]
    assert result.candidates[2].provenance.datasource == {
        "attribution": "Synthetic source credit  "
    }
    assert result.credits == result.candidates[2].provenance.credits
    assert result.stop_reason == "short_page" and result.next_offset is None
    assert [page.returned_count for page in result.pages] == [2, 2, 1]
    assert [int(request.url.params.get("offset", "0")) for request in seen] == [0, 2, 4]
    for request in seen:
        assert request.url.params["categories"] == (
            "catering.restaurant,catering.cafe,catering.fast_food,catering.food_court"
        )
        assert request.url.params["filter"] == "circle:121.0,14.6,1200"
        assert request.url.params["bias"] == "proximity:121.0,14.6"
    assert tuple(page.call for page in result.pages) == calls.receipts


@pytest.mark.asyncio
async def test_page_cap_exposes_a_resumable_offset() -> None:
    offsets: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset", "0")
        offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [feature(offset, "catering.cafe")],
            },
        )

    discovery, _ = client(httpx.MockTransport(respond))
    first = await discovery.discover(
        MealDiscoveryQuery(Point(0, 0), radius_m=1, page_size=1, max_pages=2)
    )
    assert first.stop_reason == "page_limit" and first.next_offset == 2
    assert [lead.place_id for lead in first.candidates] == ["0", "1"]
    resumed = await discovery.discover(
        MealDiscoveryQuery(
            Point(0, 0),
            radius_m=1,
            page_size=1,
            max_pages=1,
            start_offset=first.next_offset,
        )
    )
    assert offsets == ["0", "1", "2"]
    assert resumed.stop_reason == "page_limit" and resumed.next_offset == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("request_limit", [1, 3])
async def test_failure_keeps_candidates_and_distinguishes_unobserved_page(
    request_limit: int,
) -> None:
    transmissions = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal transmissions
        transmissions += 1
        if transmissions > 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [feature("kept", "catering.cafe")],
            },
        )

    discovery, calls = client(httpx.MockTransport(respond), requests=request_limit)
    result = await discovery.discover(
        MealDiscoveryQuery(Point(0, 0), radius_m=1, page_size=1)
    )
    assert [lead.place_id for lead in result.candidates] == ["kept"]
    assert result.stop_reason == "provider_stopped" and result.next_offset == 1
    assert result.pages[-1].returned_count is None
    assert result.pages[-1].call.outcome == (
        "request_limit" if request_limit == 1 else "failed"
    )
    assert transmissions == len(calls.receipts) == (1 if request_limit == 1 else 2)


@pytest.mark.asyncio
async def test_valid_empty_page_is_distinct_from_failure() -> None:
    discovery, _ = client(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"type": "FeatureCollection", "features": []}
            )
        )
    )
    result = await discovery.discover(MealDiscoveryQuery(Point(0, 0), radius_m=1))
    assert result.candidates == ()
    assert result.stop_reason == "short_page" and result.next_offset is None
    assert result.pages[0].returned_count == 0
    assert result.pages[0].call.outcome == "success"
