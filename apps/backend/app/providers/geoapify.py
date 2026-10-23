from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, ClassVar, Literal

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    ValidationInfo,
    model_validator,
)

from app.core.calls import AttemptCalls, CallSpec, Completed, Parsed, Stopped

_BODY_LIMIT = 1024 * 1024
_CATEGORIES = "catering.restaurant,catering.cafe,catering.fast_food,catering.food_court"
HTTP_LOGGERS = ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2")
PROVIDER_CREDITS = (
    ("Geoapify", "https://www.geoapify.com/"),
    ("OpenStreetMap contributors", "https://www.openstreetmap.org/copyright"),
)


class _DenyContent(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


_DENY_CONTENT = _DenyContent()


def _install_permanent_process_http_log_filters() -> None:
    for name in HTTP_LOGGERS:
        logger = logging.getLogger(name)
        if _DENY_CONTENT not in logger.filters:
            logger.addFilter(_DENY_CONTENT)


class GeoapifyInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid geographic request")


PreflightCode = Literal[
    "live_mode_required",
    "test_transport_required",
    "test_transport_forbidden",
    "unsupported_provider",
    "geoapify_key_missing",
]


class GeoapifyPreflightError(Exception):
    def __init__(self, code: PreflightCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Point:
    latitude: float = field(repr=False)
    longitude: float = field(repr=False)

    def __post_init__(self) -> None:
        for value, bound in ((self.latitude, 90), (self.longitude, 180)):
            if type(value) not in (int, float) or not -bound <= value <= bound:
                raise GeoapifyInputError()


@dataclass(frozen=True)
class Provenance:
    """Preserve exact datasource keys and values; each access returns a copy."""

    provider: ClassVar[Literal["geoapify"]] = "geoapify"
    credits: ClassVar[tuple[tuple[str, str], ...]] = PROVIDER_CREDITS
    _datasource_json: str | None = field(default=None, repr=False)

    @property
    def datasource(self) -> dict[str, JsonValue] | None:
        if self._datasource_json is None:
            return None
        return _Datasource.model_validate_json(self._datasource_json).model_dump(
            exclude_unset=True
        )


@dataclass(frozen=True)
class LocationLead:
    place_id: str = field(repr=False)
    point: Point = field(repr=False)
    name: str | None = field(repr=False)
    formatted_address: str | None = field(repr=False)
    provenance: Provenance = field(repr=False)


@dataclass(frozen=True)
class MealPlaceLead:
    place_id: str = field(repr=False)
    point: Point = field(repr=False)
    name: str | None = field(repr=False)
    formatted_address: str | None = field(repr=False)
    categories: tuple[str, ...] = field(repr=False)
    provenance: Provenance = field(repr=False)


type RouteEndpoint = Point | LocationLead | MealPlaceLead
type RouteMode = Literal["walk", "drive"]


@dataclass(frozen=True)
class RouteAvailable:
    origin: RouteEndpoint = field(repr=False)
    destination: RouteEndpoint = field(repr=False)
    mode: RouteMode
    duration_seconds: float = field(repr=False)
    distance_m: float = field(repr=False)
    provenance: Provenance = field(repr=False)
    estimate_basis: ClassVar[Literal["no_live_traffic"]] = "no_live_traffic"


@dataclass(frozen=True)
class RouteUnavailable:
    origin: RouteEndpoint = field(repr=False)
    destination: RouteEndpoint = field(repr=False)
    mode: RouteMode
    provenance: Provenance = field(repr=False)
    reason: ClassVar[Literal["provider_returned_no_routes"]] = (
        "provider_returned_no_routes"
    )


class _Wire(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, hide_input_in_errors=True, extra="ignore"
    )


class _Datasource(_Wire):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    sourcename: str | None = Field(default=None, repr=False)
    attribution: str | None = Field(default=None, repr=False)
    license: str | None = Field(default=None, repr=False)
    url: str | None = Field(default=None, repr=False)


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
_Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
_Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
_RouteMetric = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class _Route(_Wire):
    time: _RouteMetric = Field(repr=False)
    distance: _RouteMetric = Field(repr=False)
    distance_units: Literal["Meters"]
    datasource: _Datasource | None = Field(default=None, repr=False)


class _Routes(_Wire):
    results: list[_Route] = Field(max_length=1, repr=False)


class _Properties(_Wire):
    place_id: _Identifier = Field(repr=False)
    name: str | None = Field(default=None, repr=False)
    formatted: str | None = Field(default=None, repr=False)
    datasource: _Datasource | None = Field(default=None, repr=False)

    def provenance(self) -> Provenance:
        return Provenance(
            None
            if self.datasource is None
            else self.datasource.model_dump_json(exclude_unset=True)
        )


class _Location(_Properties):
    lat: _Latitude = Field(repr=False)
    lon: _Longitude = Field(repr=False)


class _Geometry(_Wire):
    type: Literal["Point"]
    coordinates: tuple[_Longitude, _Latitude] = Field(repr=False)


class _PlaceProperties(_Properties):
    categories: list[str] = Field(default_factory=list, repr=False)


class _Feature(_Wire):
    type: Literal["Feature"]
    geometry: _Geometry = Field(repr=False)
    properties: _PlaceProperties = Field(repr=False)


class _Locations(_Wire):
    results: list[_Location] = Field(repr=False)

    @model_validator(mode="after")
    def bounded(self, info: ValidationInfo) -> _Locations:
        if info.context is None or len(self.results) > info.context["limit"]:
            raise ValueError("response exceeds requested limit")
        return self


class _Places(_Wire):
    type: Literal["FeatureCollection"]
    features: list[_Feature] = Field(repr=False)

    @model_validator(mode="after")
    def bounded(self, info: ValidationInfo) -> _Places:
        if info.context is None or len(self.features) > info.context["limit"]:
            raise ValueError("response exceeds requested limit")
        return self


def _limit(value: int, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise GeoapifyInputError()


def _route_endpoint(
    endpoint: RouteEndpoint,
) -> tuple[Point, tuple[Literal["geoapify_geography"], ...]]:
    if type(endpoint) is LocationLead or type(endpoint) is MealPlaceLead:
        point = endpoint.point
        sources: tuple[Literal["geoapify_geography"], ...] = ("geoapify_geography",)
    elif type(endpoint) is Point:
        point, sources = endpoint, ()
    else:
        raise GeoapifyInputError()
    if type(point) is not Point:
        raise GeoapifyInputError()
    return point, sources


class GeoapifyClient:
    """One shared attempt and exactly one transmission per admitted operation.

    Real transport requires live mode. Test mode requires an explicitly supplied
    MockTransport and a synthetic credential. Diagnostic mode cannot transmit.
    Credits remain available on empty results. No result establishes meal
    service, opening, menus, completeness, or a canonical restaurant identity.
    """

    credits = PROVIDER_CREDITS

    def __init__(
        self, calls: AttemptCalls, *, test_transport: httpx.MockTransport | None = None
    ) -> None:
        self._calls = calls
        self._test_transport = test_transport

    def _key(self, capability: Literal["location", "discovery", "route"]) -> str:
        settings = self._calls.settings
        if settings.mode == "diagnostic":
            raise GeoapifyPreflightError("live_mode_required")
        if settings.mode == "test":
            if type(self._test_transport) is not httpx.MockTransport:
                raise GeoapifyPreflightError("test_transport_required")
        elif self._test_transport is not None:
            raise GeoapifyPreflightError("test_transport_forbidden")
        selected = {
            "location": settings.location_provider,
            "discovery": settings.discovery_provider,
            "route": settings.route_provider,
        }[capability]
        if selected != "geoapify":
            raise GeoapifyPreflightError("unsupported_provider")
        key = settings.geoapify_api_key
        if key is None or not key.get_secret_value().strip():
            raise GeoapifyPreflightError("geoapify_key_missing")
        return key.get_secret_value()

    async def _get[T](
        self,
        path: str,
        params: dict[str, str],
        seconds: float,
        parse: Callable[[bytes], T],
    ) -> Parsed[T]:
        _install_permanent_process_http_log_filters()
        transport = (
            self._test_transport
            if self._test_transport is not None
            else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        )
        async with (
            httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
                timeout=seconds,
            ) as client,
            client.stream(
                "GET", f"https://api.geoapify.com{path}", params=params
            ) as response,
        ):
            if not 200 <= response.status_code < 300:
                raise RuntimeError("geographic request failed")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > _BODY_LIMIT:
                    raise RuntimeError("geographic response exceeded bound")
                body.extend(chunk)
            return Parsed(parse(bytes(body)))

    async def resolve_location(
        self, text: str, *, limit: int = 5
    ) -> Completed[tuple[LocationLead, ...]] | Stopped:
        if type(text) is not str or not text.strip() or len(text) > 500:
            raise GeoapifyInputError()
        _limit(limit, 10)
        key = self._key("location")

        def parse(body: bytes) -> tuple[LocationLead, ...]:
            response = _Locations.model_validate_json(body, context={"limit": limit})
            return tuple(
                LocationLead(
                    item.place_id,
                    Point(item.lat, item.lon),
                    item.name,
                    item.formatted,
                    item.provenance(),
                )
                for item in response.results
            )

        async def invoke(seconds: float) -> Parsed[tuple[LocationLead, ...]]:
            return await self._get(
                "/v1/geocode/search",
                {"text": text, "format": "json", "limit": str(limit), "apiKey": key},
                seconds,
                parse,
            )

        return await self._calls.run(
            CallSpec(capability="location", use="api_diagnostic", input_sources=()),
            invoke,
        )

    async def nearby_places(
        self, center: Point | LocationLead, *, radius_m: int, limit: int = 10
    ) -> Completed[tuple[MealPlaceLead, ...]] | Stopped:
        if type(center) is LocationLead:
            point = center.point
            sources: tuple[Literal["geoapify_geography"], ...] = ("geoapify_geography",)
        elif type(center) is Point:
            point, sources = center, ()
        else:
            raise GeoapifyInputError()
        if type(point) is not Point:
            raise GeoapifyInputError()
        _limit(radius_m, 5000)
        _limit(limit, 20)
        key = self._key("discovery")
        coordinates = f"{point.longitude},{point.latitude}"

        def parse(body: bytes) -> tuple[MealPlaceLead, ...]:
            response = _Places.model_validate_json(body, context={"limit": limit})
            return tuple(
                MealPlaceLead(
                    item.properties.place_id,
                    Point(item.geometry.coordinates[1], item.geometry.coordinates[0]),
                    item.properties.name,
                    item.properties.formatted,
                    tuple(item.properties.categories),
                    item.properties.provenance(),
                )
                for item in response.features
            )

        async def invoke(seconds: float) -> Parsed[tuple[MealPlaceLead, ...]]:
            return await self._get(
                "/v2/places",
                {
                    "categories": _CATEGORIES,
                    "filter": f"circle:{coordinates},{radius_m}",
                    "bias": f"proximity:{coordinates}",
                    "limit": str(limit),
                    "apiKey": key,
                },
                seconds,
                parse,
            )

        return await self._calls.run(
            CallSpec(
                capability="discovery", use="api_diagnostic", input_sources=sources
            ),
            invoke,
        )

    async def route(
        self, origin: RouteEndpoint, destination: RouteEndpoint, *, mode: RouteMode
    ) -> Completed[RouteAvailable | RouteUnavailable] | Stopped:
        if type(mode) is not str or mode not in {"walk", "drive"}:
            raise GeoapifyInputError()
        origin_point, origin_sources = _route_endpoint(origin)
        destination_point, destination_sources = _route_endpoint(destination)
        sources = origin_sources or destination_sources
        key = self._key("route")

        def parse(body: bytes) -> RouteAvailable | RouteUnavailable:
            response = _Routes.model_validate_json(body)
            if not response.results:
                return RouteUnavailable(origin, destination, mode, Provenance())
            item = response.results[0]
            provenance = Provenance(
                None
                if item.datasource is None
                else item.datasource.model_dump_json(exclude_unset=True)
            )
            return RouteAvailable(
                origin, destination, mode, item.time, item.distance, provenance
            )

        async def invoke(seconds: float) -> Parsed[RouteAvailable | RouteUnavailable]:
            params = {
                "waypoints": (
                    f"{origin_point.latitude},{origin_point.longitude}|"
                    f"{destination_point.latitude},{destination_point.longitude}"
                ),
                "mode": mode,
                "format": "json",
                "units": "metric",
                "apiKey": key,
            }
            if mode == "drive":
                params["traffic"] = "free_flow"
            return await self._get("/v1/routing", params, seconds, parse)

        return await self._calls.run(
            CallSpec(capability="route", use="api_diagnostic", input_sources=sources),
            invoke,
        )
