from __future__ import annotations

from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

type NonBlankText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
type PositiveMinutes = Annotated[int, Field(strict=True, gt=0)]
type TravelMode = Literal["walk", "drive"]


class _ContractModel(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )


class Coordinates(_ContractModel):
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)] = Field(
        repr=False
    )
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)] = Field(
        repr=False
    )


class ProviderReference(_ContractModel):
    provider: NonBlankText
    provider_id: NonBlankText = Field(repr=False)


class ResolvedLocation(_ContractModel):
    coordinates: Coordinates = Field(repr=False)
    timezone: NonBlankText = Field(repr=False)
    name: str | None = Field(default=None, repr=False)
    formatted_address: str | None = Field(default=None, repr=False)
    provider_refs: tuple[ProviderReference, ...] = Field(default=(), repr=False)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError("timezone must be a valid IANA timezone") from None
        return value


class SearchOrigin(_ContractModel):
    kind: Literal["current", "chosen"]
    location: ResolvedLocation = Field(repr=False)


class WalkingConstraint(_ContractModel):
    mode: Literal["walk"] = "walk"
    minutes: PositiveMinutes = 15


class DrivingConstraint(_ContractModel):
    mode: Literal["drive"] = "drive"
    minutes: Annotated[int, Field(strict=True, ge=30, le=30)] = 30


type TravelConstraint = Annotated[
    WalkingConstraint | DrivingConstraint, Field(discriminator="mode")
]


class SearchRequest(_ContractModel):
    origin: SearchOrigin = Field(repr=False)
    travel: TravelConstraint = Field(default_factory=WalkingConstraint)
    eat_now_at: AwareDatetime = Field(repr=False)


class UnknownJourney(_ContractModel):
    status: Literal["unknown"] = "unknown"
    qualification: str | None = Field(default=None, repr=False)


class EstimatedJourney(_ContractModel):
    status: Literal["estimated"] = "estimated"
    mode: TravelMode
    duration_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    distance_m: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    basis: Literal["no_live_traffic", "traffic_aware", "unspecified"] = "unspecified"
    qualification: str | None = Field(default=None, repr=False)


type JourneyFact = Annotated[
    UnknownJourney | EstimatedJourney, Field(discriminator="status")
]


class UnknownOpening(_ContractModel):
    status: Literal["unknown"] = "unknown"
    qualification: str | None = Field(default=None, repr=False)


class AssessedOpening(_ContractModel):
    status: Literal["assessed"] = "assessed"
    assessed_at: AwareDatetime = Field(repr=False)
    opening: Literal["open", "closed", "unknown"]
    meal_service: Literal["serving", "not_serving", "unknown"]
    qualification: str | None = Field(default=None, repr=False)


type OpeningFact = Annotated[
    UnknownOpening | AssessedOpening, Field(discriminator="status")
]


class Place(_ContractModel):
    """A search-scoped branch, venue or stall, before recommendation selection."""

    place_id: NonBlankText = Field(repr=False)
    kind: Literal["branch", "venue", "stall"]
    name: str | None = Field(default=None, repr=False)
    branch_label: str | None = Field(default=None, repr=False)
    parent_place_id: NonBlankText | None = Field(default=None, repr=False)
    location: ResolvedLocation = Field(repr=False)
    journey: JourneyFact = Field(default_factory=UnknownJourney, repr=False)
    opening: OpeningFact = Field(default_factory=UnknownOpening, repr=False)
