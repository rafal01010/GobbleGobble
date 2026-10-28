from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.core.calls import AttemptCalls, Bounds
from app.core.settings import Settings
from app.identity import IdentityLead, reconcile_outlets
from app.journey import JourneyEstimate, JourneyQualifier, JourneyUnavailable
from app.providers.geoapify import GeoapifyClient, MealPlaceLead, Point, Provenance
from app.schemas.search import (
    Coordinates,
    DrivingConstraint,
    ResolvedLocation,
    SearchOrigin,
    SearchRequest,
    TravelConstraint,
    UnknownJourney,
    WalkingConstraint,
)


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
        geoapify_api_key=SecretStr("synthetic-journey-key"),
    )


def search(travel: TravelConstraint) -> SearchRequest:
    return SearchRequest(
        origin=SearchOrigin(
            kind="chosen",
            location=ResolvedLocation(
                coordinates=Coordinates(latitude=14.58, longitude=120.98),
                timezone="Asia/Manila",
            ),
        ),
        travel=travel,
        eat_now_at=datetime(2026, 10, 28, 4, tzinfo=UTC),
    )


_DESTINATION = MealPlaceLead(
    "synthetic-branch", Point(14.59, 120.99), "Synthetic stall", None, (), Provenance()
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("travel", "seconds", "within_limit"),
    [
        (WalkingConstraint(), 899.5, True),
        (WalkingConstraint(), 900.0, True),
        (WalkingConstraint(), 900.1, False),
        (WalkingConstraint(minutes=5), 301.0, False),
        (DrivingConstraint(), 1800.0, True),
        (DrivingConstraint(), 1800.1, False),
        (WalkingConstraint(), 0.0, True),
    ],
)
async def test_actual_client_estimate_and_selected_limit(
    travel: TravelConstraint, seconds: float, within_limit: bool
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "time": seconds,
                        "distance": 987.25,
                        "distance_units": "meters",
                        "datasource": {"attribution": "Synthetic route credit"},
                    }
                ]
            },
        )

    calls = AttemptCalls(settings(), Bounds(max_requests=1, total_seconds=3.0))
    provider = GeoapifyClient(calls, test_transport=httpx.MockTransport(respond))
    outlet = reconcile_outlets((IdentityLead("stall", _DESTINATION, "stall"),)).outlets[
        0
    ]
    before = datetime.now(UTC)
    result = await JourneyQualifier(provider).assess(
        search(travel), _DESTINATION, identity_id=outlet.identity_id
    )
    after = datetime.now(UTC)
    assert isinstance(result, JourneyEstimate)
    assert result.identity_id == "identity_1"
    assert result.route.destination is outlet.leads[0].lead
    assert result.route.origin == Point(14.58, 120.98)
    assert before <= result.receipt.started_at <= result.observed_at <= after
    assert result.observed_at.tzinfo is UTC
    assert result.constraint is travel
    assert result.within_limit is within_limit
    assert result.journey.mode == travel.mode
    assert result.journey.duration_seconds == seconds
    assert result.journey.distance_m == 987.25
    assert result.journey.basis == "no_live_traffic"
    assert result.journey.qualification is not None
    if travel.mode == "drive":
        assert "excludes live traffic" in result.journey.qualification
    assert result.route.provenance.datasource == {
        "attribution": "Synthetic route credit"
    }
    assert result.route.provenance.credits[0] == (
        "Geoapify",
        "https://www.geoapify.com/",
    )
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/routing"
    assert requests[0].url.params["mode"] == travel.mode
    assert requests[0].url.params["waypoints"] == "14.58,120.98|14.59,120.99"
    assert requests[0].url.params.get("traffic") == (
        "free_flow" if travel.mode == "drive" else None
    )
    assert calls.receipts == (result.receipt,)
    safe = repr(result) + str(result.receipt.safe_record())
    assert "Synthetic route credit" not in safe
    assert "synthetic-branch" not in safe
    assert "synthetic-journey-key" not in safe


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload", "reason", "outcome", "diagnostic"),
    [
        (200, {"results": []}, "provider_returned_no_routes", "success", "none"),
        (503, {"message": "private"}, "provider_stopped", "failed", "unknown_failure"),
        (200, {"results": [{}]}, "provider_stopped", "failed", "invalid_response"),
    ],
)
async def test_no_route_and_failures_remain_unknown(
    status_code: int,
    payload: dict[str, object],
    reason: str,
    outcome: str,
    diagnostic: str,
) -> None:
    calls = AttemptCalls(settings(), Bounds(max_requests=1, total_seconds=3.0))
    provider = GeoapifyClient(
        calls,
        test_transport=httpx.MockTransport(
            lambda _: httpx.Response(status_code, json=payload)
        ),
    )
    before = datetime.now(UTC)
    result = await JourneyQualifier(provider).assess(
        search(DrivingConstraint()), _DESTINATION, identity_id="stall-2"
    )
    assert isinstance(result, JourneyUnavailable)
    assert result.reason == reason
    assert result.identity_id == "stall-2" and result.destination is _DESTINATION
    assert result.constraint.mode == "drive" and result.constraint.minutes == 30
    assert before <= result.observed_at <= datetime.now(UTC)
    assert isinstance(result.journey, UnknownJourney)
    assert not hasattr(result.journey, "duration_seconds")
    assert not hasattr(result, "within_limit")
    assert result.receipt is not None
    assert (result.receipt.outcome, result.receipt.diagnostic) == (outcome, diagnostic)
    assert (result.route is not None) == (reason == "provider_returned_no_routes")
    assert len(result.credits) == 2


@pytest.mark.asyncio
async def test_exhausted_shared_allowance_does_not_send_or_invent_time() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    calls = AttemptCalls(settings(), Bounds(max_requests=1, total_seconds=3.0))
    qualifier = JourneyQualifier(
        GeoapifyClient(calls, test_transport=httpx.MockTransport(respond))
    )
    request = search(WalkingConstraint())
    await qualifier.assess(request, _DESTINATION, identity_id="first")
    result = await qualifier.assess(request, _DESTINATION, identity_id="second")
    assert isinstance(result, JourneyUnavailable)
    assert result.receipt is not None and result.receipt.outcome == "request_limit"
    assert not result.receipt.admitted
    assert len(requests) == 1
    assert result.journey.model_dump() == {
        "status": "unknown",
        "qualification": "The route could not be checked.",
    }


@pytest.mark.asyncio
async def test_unconfigured_route_has_an_explicit_unobserved_provider_gap() -> None:
    calls = AttemptCalls(
        settings().model_copy(update={"geoapify_api_key": None}),
        Bounds(max_requests=1, total_seconds=3.0),
    )
    provider = GeoapifyClient(
        calls,
        test_transport=httpx.MockTransport(
            lambda _: pytest.fail("Unconfigured routing must not send")
        ),
    )
    result = await JourneyQualifier(provider).assess(
        search(WalkingConstraint()), _DESTINATION, identity_id="stall-1"
    )
    assert isinstance(result, JourneyUnavailable)
    assert result.reason == "not_configured" and result.receipt is None
    assert result.route is None and calls.receipts == ()
    assert result.journey.status == "unknown"


@pytest.mark.asyncio
async def test_blank_identity_is_rejected_before_route_admission() -> None:
    calls = AttemptCalls(settings(), Bounds(max_requests=1, total_seconds=3.0))
    qualifier = JourneyQualifier(
        GeoapifyClient(
            calls,
            test_transport=httpx.MockTransport(
                lambda _: pytest.fail("Invalid identity must not send")
            ),
        )
    )
    with pytest.raises(ValueError, match="invalid journey identity"):
        await qualifier.assess(
            search(WalkingConstraint()), _DESTINATION, identity_id="  "
        )
    assert calls.receipts == ()
