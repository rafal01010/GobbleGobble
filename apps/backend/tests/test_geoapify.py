import asyncio
import gzip
import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.core.calls import AttemptCalls, Bounds, Completed, Stopped
from app.core.settings import Settings
from app.providers.geoapify import (
    GeoapifyClient,
    GeoapifyInputError,
    GeoapifyPreflightError,
    Point,
)

_KEY = "synthetic-geographic-key"
_TEXT = "private location & + / 東京?apiKey=untrusted"
_EXPECTED_CREDITS = (
    ("Geoapify", "https://www.geoapify.com/"),
    ("OpenStreetMap contributors", "https://www.openstreetmap.org/copyright"),
)
_SOURCE_LOGGERS = ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2")


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
        geoapify_api_key=SecretStr(_KEY),
    )


def attempt(settings: Settings, requests: int = 2) -> AttemptCalls:
    return AttemptCalls(settings, Bounds(max_requests=requests, total_seconds=3.0))


def location(place_id: str = " id with spaces ") -> dict[str, object]:
    return {
        "place_id": place_id,
        "lat": 35.6909,
        "lon": 139.7003,
        "name": "private name",
        "formatted": "private address",
        "datasource": {
            "sourcename": "openstreetmap",
            "attribution": "Exact © credit with spacing  ",
            "license": "ODbL",
            "url": "https://source.invalid/item?private=1",
            "new_source_field": {"values": [None, True, 7, 2.25, "raw"]},
        },
    }


def feature() -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [139.7003, 35.6909]},
        "properties": location("place-id")
        | {"categories": ["catering.restaurant", "catering"]},
    }


def places(items: list[dict[str, object]]) -> dict[str, object]:
    return {"type": "FeatureCollection", "features": items}


@pytest.mark.asyncio
async def test_requests_preserve_ambiguity_ids_attribution_and_derived_lineage(
    settings: Settings,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = (
            {"results": [location(), location("second-id")]}
            if request.url.path == "/v1/geocode/search"
            else places([feature()])
        )
        return httpx.Response(200, json=payload)

    calls = attempt(settings)
    client = GeoapifyClient(calls, test_transport=httpx.MockTransport(respond))
    resolved = await client.resolve_location(_TEXT, limit=2)
    assert calls.settings is settings
    assert isinstance(resolved, Completed)
    assert [item.place_id for item in resolved.value] == [
        " id with spaces ",
        "second-id",
    ]
    chosen = resolved.value[0]
    assert chosen.point == Point(latitude=35.6909, longitude=139.7003)
    assert chosen.name == "private name"
    assert chosen.formatted_address == "private address"
    assert chosen.provenance.datasource == location()["datasource"]
    copied = chosen.provenance.datasource
    assert copied is not None
    copied["attribution"] = "changed"
    assert chosen.provenance.datasource == location()["datasource"]
    assert chosen.provenance.provider == "geoapify"
    assert chosen.provenance.credits == _EXPECTED_CREDITS
    discovered = await client.nearby_places(chosen, radius_m=800, limit=3)
    assert isinstance(discovered, Completed)
    assert discovered.value[0].place_id == "place-id"
    assert discovered.value[0].point == chosen.point
    assert discovered.value[0].categories == ("catering.restaurant", "catering")
    assert discovered.value[0].provenance.datasource == location()["datasource"]
    assert resolved.receipt.input_sources == ()
    assert discovered.receipt.input_sources == ("geoapify_geography",)
    assert len(requests) == 2
    first, second = requests
    assert first.method == second.method == "GET"
    assert first.url.host == second.url.host == "api.geoapify.com"
    assert dict(first.url.params) == {
        "text": _TEXT,
        "format": "json",
        "limit": "2",
        "apiKey": _KEY,
    }
    assert "東京" not in str(first.url)
    assert dict(second.url.params) == {
        "categories": (
            "catering.restaurant,catering.cafe,catering.fast_food,catering.food_court"
        ),
        "filter": "circle:139.7003,35.6909,800",
        "bias": "proximity:139.7003,35.6909",
        "limit": "3",
        "apiKey": _KEY,
    }
    for request in requests:
        timeout = request.extensions["timeout"]
        assert all(0 < value <= 1.0 for value in timeout.values())
    rejected = await client.resolve_location("another location")
    assert isinstance(rejected, Stopped)
    assert rejected.receipt.outcome == "request_limit"
    assert not rejected.receipt.admitted
    assert len(requests) == 2
    assert len(calls.receipts) == 2


@pytest.mark.asyncio
async def test_empty_batches_and_independent_point_keep_credits(
    settings: Settings,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": []}
            if request.url.path == "/v1/geocode/search"
            else places([]),
        )

    client = GeoapifyClient(
        attempt(settings), test_transport=httpx.MockTransport(respond)
    )
    resolved = await client.resolve_location("empty")
    discovered = await client.nearby_places(Point(0, 0), radius_m=1)
    assert isinstance(resolved, Completed) and resolved.value == ()
    assert isinstance(discovered, Completed) and discovered.value == ()
    assert discovered.receipt.input_sources == ()
    assert client.credits == (
        ("Geoapify", "https://www.geoapify.com/"),
        ("OpenStreetMap contributors", "https://www.openstreetmap.org/copyright"),
    )
    assert resolved.receipt.usage is None and resolved.receipt.cost is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": None},
        {"results": [location(), location() | {"place_id": " "}]},
        {"results": [location() | {"place_id": 123}]},
        {"results": [location() | {"lat": True}]},
        {"results": [location() | {"lon": 181}]},
        {"results": [location() | {"name": 7}]},
        {"results": [location() | {"datasource": {"attribution": {"bad": 1}}}]},
        {"results": [location() | {"datasource": []}]},
        {"results": [location(), location(), location()]},
    ],
)
async def test_malformed_location_rejects_entire_batch(
    settings: Settings, payload: dict[str, object]
) -> None:
    client = GeoapifyClient(
        attempt(settings),
        test_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    result = await client.resolve_location(_TEXT, limit=2)
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "failed"
    assert result.receipt.diagnostic == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "FeatureCollection", "features": {}},
        places([feature() | {"type": "wrong"}]),
        places([feature() | {"geometry": None}]),
        places([feature() | {"geometry": {"type": "Point", "coordinates": [0]}}]),
        places([feature() | {"properties": {"place_id": ""}}]),
        places([feature() | {"properties": location() | {"categories": [1]}}]),
        places([feature(), feature()]),
    ],
)
async def test_malformed_discovery_rejects_entire_batch(
    settings: Settings, payload: dict[str, object]
) -> None:
    client = GeoapifyClient(
        attempt(settings),
        test_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    result = await client.nearby_places(Point(0, 0), radius_m=1, limit=1)
    assert isinstance(result, Stopped)
    assert result.receipt.diagnostic == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 307, 400, 429, 500, 503])
async def test_http_failures_never_redirect_or_retry(
    settings: Settings, status: int
) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            status, headers={"location": "https://unsafe.invalid/"}, text=_TEXT
        )

    calls = attempt(settings, requests=1)
    client = GeoapifyClient(calls, test_transport=httpx.MockTransport(respond))
    result = await client.resolve_location(_TEXT)
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "failed"
    assert result.receipt.diagnostic == "unknown_failure"
    assert len(seen) == 1 and len(calls.receipts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("compressed", [False, True])
async def test_decoded_response_size_is_bounded(
    settings: Settings, compressed: bool
) -> None:
    body = b" " * (1024 * 1024 + 1)
    response = httpx.Response(
        200,
        content=gzip.compress(body) if compressed else body,
        headers={"content-encoding": "gzip"} if compressed else {},
    )
    client = GeoapifyClient(
        attempt(settings),
        test_transport=httpx.MockTransport(lambda _: response),
    )
    result = await client.resolve_location(_TEXT)
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "failed"
    assert result.receipt.diagnostic == "unknown_failure"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b"{private-invalid-json", b'{"results": [{"lat": NaN}]}']
)
async def test_invalid_json_has_safe_failure(settings: Settings, body: bytes) -> None:
    client = GeoapifyClient(
        attempt(settings),
        test_transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    )
    result = await client.resolve_location(_TEXT)
    assert isinstance(result, Stopped)
    assert result.receipt.diagnostic == "invalid_response"
    assert body.decode() not in repr(result)


@pytest.mark.asyncio
async def test_httpx_and_source_transport_logs_are_denied_at_debug(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def respond(request: httpx.Request) -> httpx.Response:
        for name in _SOURCE_LOGGERS:
            logging.getLogger(name).debug("%s %s %s", request.url, _TEXT, _KEY)
        raise httpx.ConnectError(f"{_KEY} {_TEXT}", request=request)

    client = GeoapifyClient(
        attempt(settings), test_transport=httpx.MockTransport(respond)
    )
    result = await client.resolve_location(_TEXT)
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "failed"
    safe = json.dumps(result.receipt.safe_record()) + repr(result) + caplog.text
    assert _KEY not in safe and _TEXT not in safe
    assert not any(record.name in _SOURCE_LOGGERS for record in caplog.records)


@pytest.mark.asyncio
async def test_success_values_and_httpx_info_never_enter_diagnostics(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = GeoapifyClient(
        attempt(settings),
        test_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"results": [location()]})
        ),
    )
    result = await client.resolve_location(_TEXT)
    assert isinstance(result, Completed)
    lead = result.value[0]
    safe = (
        json.dumps(result.receipt.safe_record())
        + repr(result)
        + repr(lead)
        + repr(lead.point)
        + repr(lead.provenance)
        + caplog.text
    )
    for private in (_KEY, _TEXT, "private name", "private address", "139.7003"):
        assert private not in safe


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,transport,code",
    [
        ({"mode": "diagnostic"}, True, "live_mode_required"),
        ({"mode": "test"}, False, "test_transport_required"),
        ({"mode": "live"}, True, "test_transport_forbidden"),
        ({"location_provider": "google_geocoding"}, True, "unsupported_provider"),
        ({"geoapify_api_key": None}, True, "geoapify_key_missing"),
        ({"geoapify_api_key": SecretStr("  ")}, True, "geoapify_key_missing"),
    ],
)
async def test_preflight_blocks_before_admission(
    settings: Settings, changes: dict[str, object], transport: bool, code: str
) -> None:
    def forbidden(_: httpx.Request) -> httpx.Response:
        pytest.fail("blocked configuration transmitted a request")

    calls = attempt(settings.model_copy(update=changes))
    client = GeoapifyClient(
        calls, test_transport=httpx.MockTransport(forbidden) if transport else None
    )
    with pytest.raises(GeoapifyPreflightError) as raised:
        await client.resolve_location(_TEXT)
    assert raised.value.code == code
    assert str(raised.value) == code and raised.value.__cause__ is None
    assert calls.receipts == ()


@pytest.mark.asyncio
async def test_discovery_selection_is_checked_independently(settings: Settings) -> None:
    calls = attempt(settings.model_copy(update={"discovery_provider": "google_places"}))
    client = GeoapifyClient(
        calls,
        test_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"results": []})
        ),
    )
    assert isinstance(await client.resolve_location("ok"), Completed)
    with pytest.raises(GeoapifyPreflightError, match="^unsupported_provider$"):
        await client.nearby_places(Point(0, 0), radius_m=1)
    assert len(calls.receipts) == 1


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (True, 0),
        (0, False),
        (91, 0),
        (0, -181),
        (float("nan"), 0),
        (0, float("inf")),
        (10**1000, 0),
    ],
)
def test_points_reject_invalid_coordinates_without_values(
    latitude: float, longitude: float
) -> None:
    with pytest.raises(GeoapifyInputError, match="^invalid geographic request$"):
        Point(latitude, longitude)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, -1, 11, 1.5])
async def test_location_limit_is_a_bounded_integer(
    settings: Settings, limit: int
) -> None:
    calls = attempt(settings)
    with pytest.raises(GeoapifyInputError, match="^invalid geographic request$"):
        await GeoapifyClient(calls).resolve_location(_TEXT, limit=limit)
    assert calls.receipts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", " \n", "p" * 501])
async def test_query_bounds_are_safe(settings: Settings, text: str) -> None:
    with pytest.raises(GeoapifyInputError, match="^invalid geographic request$"):
        await GeoapifyClient(attempt(settings)).resolve_location(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "radius,limit", [(True, 1), (0, 1), (5001, 1), (1.5, 1), (1, True), (1, 0), (1, 21)]
)
async def test_discovery_bounds_are_safe(
    settings: Settings, radius: int, limit: int
) -> None:
    with pytest.raises(GeoapifyInputError, match="^invalid geographic request$"):
        await GeoapifyClient(attempt(settings)).nearby_places(
            Point(0, 0), radius_m=radius, limit=limit
        )


@pytest.mark.asyncio
async def test_cancellation_propagates_and_consumes_shared_allowance(
    settings: Settings,
) -> None:
    entered = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Future()
        return httpx.Response(200, json={"results": []})

    calls = attempt(settings, requests=1)
    client = GeoapifyClient(calls, test_transport=httpx.MockTransport(respond))
    task = asyncio.create_task(client.resolve_location(_TEXT))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls.receipts) == 1 and calls.receipts[0].outcome == "cancelled"
    result = await client.nearby_places(Point(0, 0), radius_m=1)
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "request_limit"


@pytest.mark.asyncio
async def test_missing_datasource_stays_absent(settings: Settings) -> None:
    item = location()
    del item["datasource"]
    client = GeoapifyClient(
        attempt(settings),
        test_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"results": [item]})
        ),
    )
    result = await client.resolve_location("ok")
    assert isinstance(result, Completed)
    assert result.value[0].provenance.datasource is None
    assert result.value[0].provenance.credits == _EXPECTED_CREDITS
