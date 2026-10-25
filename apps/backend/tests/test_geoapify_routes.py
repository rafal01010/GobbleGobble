import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.calls import AttemptCalls, Bounds, Completed, Stopped
from app.core.settings import Settings
from app.providers.geoapify import (
    GeoapifyClient,
    GeoapifyInputError,
    GeoapifyPreflightError,
    LocationLead,
    MealPlaceLead,
    Point,
    Provenance,
    RouteAvailable,
    RouteEndpoint,
    RouteMode,
    RouteUnavailable,
)

_KEY = "synthetic-route-key"
_ORIGIN, _DESTINATION = Point(35.6909, 139.7003), Point(35.6812, 139.7671)
_RECORD = {"time": 123.456, "distance": 890.125, "distance_units": "meters"}


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


def attempt(settings: Settings) -> AttemptCalls:
    return AttemptCalls(settings, Bounds(max_requests=2, total_seconds=3.0))


def client(calls: AttemptCalls, payload: object) -> GeoapifyClient:
    return GeoapifyClient(
        calls,
        test_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps(payload))
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["walk", "drive"])
async def test_route_wire_metrics_provenance_and_safe_accounting(
    settings: Settings,
    mode: RouteMode,
) -> None:
    requests: list[httpx.Request] = []
    datasource = {"attribution": "Exact private credit  ", "extra": {"raw": [None, 7]}}

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"results": [_RECORD | {"datasource": datasource}]}
        )

    calls = attempt(settings)
    result = await GeoapifyClient(
        calls, test_transport=httpx.MockTransport(respond)
    ).route(_ORIGIN, _DESTINATION, mode=mode)
    assert isinstance(result, Completed) and isinstance(result.value, RouteAvailable)
    route = result.value
    assert (route.duration_seconds, route.distance_m, route.mode) == (
        123.456,
        890.125,
        mode,
    )
    assert route.origin is _ORIGIN and route.destination is _DESTINATION
    assert route.estimate_basis == "no_live_traffic"
    assert route.provenance.datasource == datasource
    assert route.provenance.credits == (
        ("Geoapify", "https://www.geoapify.com/"),
        ("OpenStreetMap contributors", "https://www.openstreetmap.org/copyright"),
    )
    assert len(requests) == 1 and calls.receipts == (result.receipt,)
    request = requests[0]
    assert request.method == "GET" and request.url.host == "api.geoapify.com"
    assert request.url.path == "/v1/routing"
    params = {
        "waypoints": "35.6909,139.7003|35.6812,139.7671",
        "mode": mode,
        "format": "json",
        "units": "metric",
        "apiKey": _KEY,
    }
    if mode == "drive":
        params["traffic"] = "free_flow"
    assert dict(request.url.params) == params
    receipt = result.receipt
    assert (
        receipt.provider,
        receipt.capability,
        receipt.use,
        receipt.input_sources,
    ) == ("geoapify", "route", "api_diagnostic", ())
    assert receipt.outcome == "success" and receipt.admitted
    assert receipt.usage is None and receipt.cost is None
    safe = json.dumps(receipt.safe_record()) + repr(result) + repr(route)
    for private in (
        _KEY,
        "35.6909",
        "139.7671",
        "123.456",
        "890.125",
        "private credit",
    ):
        assert private not in safe


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [False, True])
async def test_empty_routes_and_provider_returned_zero_metrics(
    settings: Settings, available: bool
) -> None:
    record = {"time": 0, "distance": 0, "distance_units": "meters"}
    result = await client(
        attempt(settings), {"results": [record] if available else []}
    ).route(_ORIGIN, _DESTINATION, mode="walk")
    assert isinstance(result, Completed)
    route = result.value
    assert route.origin is _ORIGIN and route.destination is _DESTINATION
    assert route.provenance.datasource is None and route.mode == "walk"
    if available:
        assert isinstance(route, RouteAvailable)
        assert (route.duration_seconds, route.distance_m) == (0, 0)
    else:
        assert isinstance(route, RouteUnavailable)
        assert route.reason == "provider_returned_no_routes"
        assert not hasattr(route, "duration_seconds") and not hasattr(
            route, "distance_m"
        )
    assert result.receipt.outcome == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": None},
        {"results": {}},
        {"results": [{}]},
        {"results": [_RECORD, _RECORD]},
        *(
            {"results": [_RECORD | {field: value}]}
            for field in ("time", "distance")
            for value in (-1, True, "1", None, float("nan"), float("inf"))
        ),
        {"results": [{"time": 1, "distance": 2}]},
        *(
            {"results": [_RECORD | {"distance_units": units}]}
            for units in ("Meters", "kilometers", "miles", "feet", "", None)
        ),
        {"results": [_RECORD | {"datasource": []}]},
    ],
)
async def test_invalid_metrics_units_shape_or_count_reject_answer(
    settings: Settings, payload: object
) -> None:
    result = await client(attempt(settings), payload).route(
        _ORIGIN, _DESTINATION, mode="drive"
    )
    assert isinstance(result, Stopped)
    assert (result.receipt.outcome, result.receipt.diagnostic) == (
        "failed",
        "invalid_response",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 404, 429, 500])
async def test_error_prose_does_not_become_unavailability(
    settings: Settings, status: int
) -> None:
    message = "No route found between private endpoints"
    result = await GeoapifyClient(
        attempt(settings),
        test_transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status, json={"statusCode": status, "message": message}
            )
        ),
    ).route(_ORIGIN, _DESTINATION, mode="walk")
    assert isinstance(result, Stopped)
    assert (result.receipt.outcome, result.receipt.diagnostic) == (
        "failed",
        "unknown_failure",
    )
    assert message not in repr(result) + json.dumps(result.receipt.safe_record())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin,destination,sources",
    [
        (_ORIGIN, _DESTINATION, ()),
        (
            LocationLead("private-id", _ORIGIN, None, None, Provenance()),
            _DESTINATION,
            ("geoapify_geography",),
        ),
        (
            _ORIGIN,
            LocationLead("private-id", _DESTINATION, None, None, Provenance()),
            ("geoapify_geography",),
        ),
        (
            MealPlaceLead("private-id", _ORIGIN, None, None, (), Provenance()),
            _DESTINATION,
            ("geoapify_geography",),
        ),
        (
            _ORIGIN,
            MealPlaceLead("private-id", _DESTINATION, None, None, (), Provenance()),
            ("geoapify_geography",),
        ),
        (
            LocationLead("private-id", _ORIGIN, None, None, Provenance()),
            MealPlaceLead("private-id", _DESTINATION, None, None, (), Provenance()),
            ("geoapify_geography",),
        ),
    ],
)
async def test_selected_leads_from_either_endpoint_retain_lineage(
    settings: Settings,
    origin: RouteEndpoint,
    destination: RouteEndpoint,
    sources: tuple[str, ...],
) -> None:
    result = await client(attempt(settings), {"results": [_RECORD]}).route(
        origin, destination, mode="walk"
    )
    assert isinstance(result, Completed) and isinstance(result.value, RouteAvailable)
    assert result.value.origin is origin and result.value.destination is destination
    assert (
        result.value.duration_seconds == 123.456
        and result.receipt.input_sources == sources
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,transport,code",
    [
        ({"mode": "diagnostic"}, True, "live_mode_required"),
        ({"mode": "test"}, False, "test_transport_required"),
        ({"mode": "live"}, True, "test_transport_forbidden"),
        ({"route_provider": "google_routes"}, True, "unsupported_provider"),
        ({"route_provider": "google_grounding_lite"}, True, "unsupported_provider"),
        ({"geoapify_api_key": None}, True, "geoapify_key_missing"),
    ],
)
async def test_route_preflight_is_before_admission(
    settings: Settings,
    changes: dict[str, object],
    transport: bool,
    code: str,
) -> None:
    calls = attempt(settings.model_copy(update=changes))
    subject = GeoapifyClient(
        calls,
        test_transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request"))
        if transport
        else None,
    )
    with pytest.raises(GeoapifyPreflightError) as raised:
        await subject.route(_ORIGIN, _DESTINATION, mode="walk")
    assert str(raised.value) == code and raised.value.code == code
    assert raised.value.__cause__ is None and calls.receipts == ()


@pytest.mark.asyncio
async def test_route_selection_is_independent(settings: Settings) -> None:
    calls = attempt(
        settings.model_copy(
            update={
                "location_provider": "google_geocoding",
                "discovery_provider": "google_places",
            }
        )
    )
    result = await client(calls, {"results": []}).route(
        _ORIGIN, _DESTINATION, mode="walk"
    )
    assert isinstance(result, Completed) and isinstance(result.value, RouteUnavailable)
    assert result.receipt.provider == "geoapify"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["walking", "car", "WALK", "", None, 1])
async def test_invalid_mode_is_local(settings: Settings, mode: RouteMode) -> None:
    calls = attempt(settings)
    with pytest.raises(GeoapifyInputError, match="^invalid geographic request$"):
        await GeoapifyClient(calls).route(_ORIGIN, _DESTINATION, mode=mode)
    assert calls.receipts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", [None, (35.6, 139.7), {"lat": 35.6}, "private place"]
)
@pytest.mark.parametrize("origin_invalid", [False, True])
async def test_invalid_endpoint_is_local(
    settings: Settings, endpoint: RouteEndpoint, origin_invalid: bool
) -> None:
    calls = attempt(settings)
    with pytest.raises(GeoapifyInputError, match="^invalid geographic request$"):
        await GeoapifyClient(calls).route(
            endpoint if origin_invalid else _ORIGIN,
            _DESTINATION if origin_invalid else endpoint,
            mode="walk",
        )
    assert calls.receipts == ()


@pytest.mark.asyncio
async def test_location_and_routes_share_request_allowance(settings: Settings) -> None:
    calls = attempt(settings)
    subject = client(calls, {"results": []})
    location = await subject.resolve_location("synthetic location")
    driving = await subject.route(_ORIGIN, _DESTINATION, mode="drive")
    walking = await subject.route(_ORIGIN, _DESTINATION, mode="walk")
    assert isinstance(location, Completed) and location.value == ()
    assert isinstance(driving, Completed) and isinstance(
        driving.value, RouteUnavailable
    )
    assert isinstance(walking, Stopped) and walking.receipt.outcome == "request_limit"
    assert not walking.receipt.admitted and len(calls.receipts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt_bound", [False, True])
async def test_route_deadlines(settings: Settings, attempt_bound: bool) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        await asyncio.Future()
        return httpx.Response(200, json={"results": []})

    calls = AttemptCalls(
        settings
        if attempt_bound
        else settings.model_copy(update={"geographic_timeout_seconds": 0.01}),
        Bounds(max_requests=1, total_seconds=0.01 if attempt_bound else 3.0),
    )
    result = await GeoapifyClient(
        calls, test_transport=httpx.MockTransport(respond)
    ).route(_ORIGIN, _DESTINATION, mode="walk")
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == (
        "attempt_timeout" if attempt_bound else "call_timeout"
    )
    assert result.receipt.admitted and calls.receipts == (result.receipt,)
