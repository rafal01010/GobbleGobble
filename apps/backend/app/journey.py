from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar, Literal

from app.core.calls import Receipt, Stopped
from app.providers.geoapify import (
    PROVIDER_CREDITS,
    GeoapifyClient,
    GeoapifyPreflightError,
    Point,
    RouteAvailable,
    RouteEndpoint,
    RouteUnavailable,
)
from app.schemas.search import (
    EstimatedJourney,
    SearchRequest,
    TravelConstraint,
    UnknownJourney,
)


@dataclass(frozen=True)
class JourneyEstimate:
    identity_id: str = field(repr=False)
    constraint: TravelConstraint
    observed_at: datetime
    route: RouteAvailable = field(repr=False)
    journey: EstimatedJourney = field(repr=False)
    receipt: Receipt

    @property
    def within_limit(self) -> bool:
        return self.journey.duration_seconds <= self.constraint.minutes * 60


@dataclass(frozen=True)
class JourneyUnavailable:
    identity_id: str = field(repr=False)
    origin: Point = field(repr=False)
    destination: RouteEndpoint = field(repr=False)
    constraint: TravelConstraint
    observed_at: datetime
    reason: Literal["provider_returned_no_routes", "provider_stopped", "not_configured"]
    journey: UnknownJourney = field(repr=False)
    receipt: Receipt | None
    route: RouteUnavailable | None = field(default=None, repr=False)
    credits: ClassVar[tuple[tuple[str, str], ...]] = PROVIDER_CREDITS


class JourneyQualifier:
    def __init__(self, provider: GeoapifyClient) -> None:
        self._provider = provider

    async def assess(
        self,
        request: SearchRequest,
        destination: RouteEndpoint,
        *,
        identity_id: str,
    ) -> JourneyEstimate | JourneyUnavailable:
        if not isinstance(identity_id, str) or not identity_id.strip():
            raise ValueError("invalid journey identity")
        coordinates = request.origin.location.coordinates
        origin = Point(coordinates.latitude, coordinates.longitude)
        try:
            result = await self._provider.route(
                origin, destination, mode=request.travel.mode
            )
        except GeoapifyPreflightError:
            return JourneyUnavailable(
                identity_id,
                origin,
                destination,
                request.travel,
                datetime.now(UTC),
                "not_configured",
                UnknownJourney(qualification="Routing is not configured."),
                None,
            )
        observed_at = datetime.now(UTC)
        if isinstance(result, Stopped):
            return JourneyUnavailable(
                identity_id,
                origin,
                destination,
                request.travel,
                observed_at,
                "provider_stopped",
                UnknownJourney(qualification="The route could not be checked."),
                result.receipt,
            )
        route = result.value
        if isinstance(route, RouteUnavailable):
            return JourneyUnavailable(
                identity_id,
                origin,
                destination,
                request.travel,
                observed_at,
                "provider_returned_no_routes",
                UnknownJourney(qualification="The provider returned no route."),
                result.receipt,
                route,
            )
        return JourneyEstimate(
            identity_id,
            request.travel,
            observed_at,
            route,
            EstimatedJourney(
                mode=route.mode,
                duration_seconds=route.duration_seconds,
                distance_m=route.distance_m,
                basis=route.estimate_basis,
                qualification=(
                    "Provider route estimate. Driving excludes live traffic, parking "
                    "and time to reach the vehicle. Arrival time is not guaranteed."
                    if route.mode == "drive"
                    else "Provider walking route estimate. Walking pace, access "
                    "and conditions can change actual travel time."
                ),
            ),
            result.receipt,
        )
