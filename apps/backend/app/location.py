from __future__ import annotations

import re
from typing import Literal

from pydantic import ValidationError

from app.core.calls import Stopped
from app.providers.geoapify import (
    GeoapifyClient,
    GeoapifyPreflightError,
    LocationLead,
    Point,
)
from app.schemas.operations import (
    CurrentLocationInput,
    LocationChoice,
    LocationChoicesOutcome,
    LocationInput,
    LocationNoMatchOutcome,
    LocationUnavailableOutcome,
    PointLocationChoice,
    RefineLocationChoice,
    ResolvedLocationOutcome,
    ResolveLocationResponse,
)
from app.schemas.search import (
    Coordinates,
    ProviderReference,
    ResolvedLocation,
    SearchOrigin,
)

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_COORDINATES = re.compile(rf"\s*({_NUMBER})\s*,\s*({_NUMBER})\s*")
_POINT_TYPES = frozenset({"building", "amenity"})
_AREA_TYPES = frozenset(
    {"country", "state", "county", "city", "postcode", "district", "suburb", "street"}
)


def _label(lead: LocationLead) -> str:
    return (
        (lead.formatted_address or "").strip()
        or (lead.name or "").strip()
        or f"{lead.point.latitude}, {lead.point.longitude}"
    )


def _origin(
    lead: LocationLead,
    *,
    kind: Literal["current", "chosen"],
    coordinates: Coordinates | None = None,
) -> SearchOrigin | None:
    if lead.timezone is None:
        return None
    try:
        location = ResolvedLocation(
            coordinates=coordinates
            or Coordinates(
                latitude=lead.point.latitude, longitude=lead.point.longitude
            ),
            timezone=lead.timezone,
            name=(
                f"{coordinates.latitude}, {coordinates.longitude}"
                if coordinates is not None
                else _label(lead)
            ),
            formatted_address=lead.formatted_address if coordinates is None else None,
            provider_refs=(
                ProviderReference(provider="geoapify", provider_id=lead.place_id),
            )
            if coordinates is None
            else (),
        )
    except ValidationError:
        return None
    return SearchOrigin(kind=kind, location=location)


def _choice(lead: LocationLead) -> LocationChoice:
    if lead.result_type not in _POINT_TYPES:
        return RefineLocationChoice(
            label=_label(lead),
            reason="broad_area"
            if lead.result_type in _AREA_TYPES
            else "unconfirmed_point",
        )
    origin = _origin(lead, kind="chosen")
    if origin is None:
        return RefineLocationChoice(label=_label(lead), reason="timezone_unavailable")
    return PointLocationChoice(label=_label(lead), origin=origin)


class LocationResolver:
    def __init__(self, provider: GeoapifyClient) -> None:
        self._provider = provider

    async def resolve(self, location: LocationInput) -> ResolveLocationResponse:
        kind: Literal["current", "chosen"] = location.kind
        query: Coordinates | str = (
            location.coordinates
            if isinstance(location, CurrentLocationInput)
            else location.query
        )
        if isinstance(query, str) and (match := _COORDINATES.fullmatch(query)):
            try:
                query = Coordinates(latitude=float(match[1]), longitude=float(match[2]))
            except ValidationError:
                return ResolveLocationResponse(
                    outcome=LocationNoMatchOutcome(
                        message="Coordinates are outside valid bounds."
                    )
                )
        try:
            if isinstance(query, Coordinates):
                result = await self._provider.reverse_location(
                    Point(query.latitude, query.longitude)
                )
            else:
                result = await self._provider.resolve_location(query, limit=5)
        except GeoapifyPreflightError:
            return ResolveLocationResponse(
                outcome=LocationUnavailableOutcome(
                    message="Location lookup is not configured.", retryable=False
                )
            )
        if isinstance(result, Stopped):
            return ResolveLocationResponse(
                outcome=LocationUnavailableOutcome(
                    message="Location lookup is unavailable. Try again later.",
                    retryable=True,
                )
            )
        if not result.value:
            return ResolveLocationResponse(
                outcome=LocationNoMatchOutcome(
                    message="No location matched. Try an address, landmark, or coordinates."
                )
            )
        if isinstance(query, Coordinates):
            origin = _origin(result.value[0], kind=kind, coordinates=query)
            if origin is None:
                return ResolveLocationResponse(
                    outcome=LocationUnavailableOutcome(
                        message="The location's time zone could not be confirmed.",
                        retryable=False,
                    )
                )
            return ResolveLocationResponse(
                outcome=ResolvedLocationOutcome(origin=origin)
            )
        choices = tuple(_choice(lead) for lead in result.value)
        if len(choices) == 1 and isinstance(choices[0], PointLocationChoice):
            return ResolveLocationResponse(
                outcome=ResolvedLocationOutcome(origin=choices[0].origin)
            )
        return ResolveLocationResponse(
            outcome=LocationChoicesOutcome(
                message="Choose a specific point or narrow the location query.",
                choices=choices,
            )
        )
