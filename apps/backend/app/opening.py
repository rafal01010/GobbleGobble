from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.evidence import EvidenceCollection, EvidenceId
from app.schemas.search import AssessedOpening, JourneyFact, SearchRequest


class _HoursModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class LocalPeriod(_HoursModel):
    start_minute: Annotated[int, Field(ge=0, lt=1440)]
    end_minute: Annotated[int, Field(ge=0, le=1440)]

    @model_validator(mode="after")
    def nonempty(self) -> LocalPeriod:
        if self.start_minute == self.end_minute:
            raise ValueError("equal period endpoints are ambiguous")
        return self


class WeeklyPeriod(LocalPeriod):
    weekday: Annotated[int, Field(ge=0, le=6)]


class DateHours(_HoursModel):
    day: date
    periods: tuple[LocalPeriod, ...] = Field(repr=False)


class HoursEvidence(_HoursModel):
    topic: Literal["opening", "meal_service"]
    coverage: Literal["complete", "partial"]
    valid_from: date
    valid_through: date
    evidence_ids: Annotated[tuple[EvidenceId, ...], Field(min_length=1)] = Field(
        repr=False
    )
    weekly: tuple[WeeklyPeriod, ...] = Field(default=(), repr=False)
    exceptions: tuple[DateHours, ...] = Field(default=(), repr=False)

    @model_validator(mode="after")
    def consistent(self) -> HoursEvidence:
        if self.valid_through < self.valid_from:
            raise ValueError("hours applicability is reversed")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("duplicate hours evidence")
        if len({item.day for item in self.exceptions}) != len(self.exceptions):
            raise ValueError("duplicate date exception")
        return self


@dataclass(frozen=True)
class OpeningAssessment:
    place_id: str = field(repr=False)
    timezone: str = field(repr=False)
    now: AssessedOpening = field(repr=False)
    arrival: AssessedOpening | None = field(repr=False)
    schedules: tuple[HoursEvidence, ...] = field(repr=False)

    @property
    def availability(self) -> Literal["available", "unavailable", "unknown"]:
        facts = (self.now,) if self.arrival is None else (self.now, self.arrival)
        if any(
            item.opening == "closed" or item.meal_service == "not_serving"
            for item in facts
        ):
            return "unavailable"
        if self.arrival is not None and all(
            item.opening == "open" and item.meal_service == "serving" for item in facts
        ):
            return "available"
        return "unknown"


def _boundary_instants(wall: datetime, zone: ZoneInfo) -> set[datetime]:
    return {
        instant
        for fold in (0, 1)
        if (instant := wall.replace(tzinfo=zone, fold=fold).astimezone(UTC))
        .astimezone(zone)
        .replace(tzinfo=None)
        == wall
    }


def _contains(schedule: HoursEvidence, local: datetime, zone: ZoneInfo) -> bool | None:
    instant = local.astimezone(UTC)
    uncertain = False
    exceptions = {item.day: item.periods for item in schedule.exceptions}
    for day in (local.date() - timedelta(days=1), local.date()):
        periods = exceptions.get(day)
        if periods is None:
            periods = tuple(
                item for item in schedule.weekly if item.weekday == day.weekday()
            )
        midnight = datetime.combine(day, datetime.min.time())
        for period in periods:
            start = midnight + timedelta(minutes=period.start_minute)
            end = midnight + timedelta(minutes=period.end_minute)
            if period.end_minute < period.start_minute:
                end += timedelta(days=1)
            starts = _boundary_instants(start, zone)
            ends = _boundary_instants(end, zone)
            if len(starts) != 1 or len(ends) != 1:
                uncertain = True
                continue
            if next(iter(starts)) <= instant < next(iter(ends)):
                return True
    return None if uncertain else False


def assess_opening(
    request: SearchRequest,
    *,
    place_id: str,
    destination_timezone: str,
    journey: JourneyFact,
    evidence: EvidenceCollection,
    schedules: tuple[HoursEvidence, ...] = (),
) -> OpeningAssessment:
    if place_id not in evidence.place_ids:
        raise ValueError("opening place is outside supplied evidence")
    zone = ZoneInfo(destination_timezone)
    schedules = tuple(HoursEvidence.model_validate(item) for item in schedules)
    by_id = {item.evidence_id: item for item in evidence.evidence}
    for schedule in schedules:
        for identifier in schedule.evidence_ids:
            support = by_id.get(identifier)
            if support is None or place_id not in support.subject_place_ids:
                raise ValueError("hours evidence is outside the assessed place")
    now_utc = request.eat_now_at.astimezone(UTC)

    def at(instant: datetime) -> AssessedOpening:
        local = instant.astimezone(zone)
        states: dict[str, set[bool]] = {"opening": set(), "meal_service": set()}
        gaps: set[str] = set()
        for schedule in schedules:
            if not schedule.valid_from <= local.date() <= schedule.valid_through:
                gaps.add("Hours are outside their declared local-date applicability.")
                continue
            support = tuple(by_id[item] for item in schedule.evidence_ids)
            if any(
                item.freshness.fresh_until is not None
                and instant >= item.freshness.fresh_until.astimezone(UTC)
                for item in support
            ):
                gaps.add("Hours evidence has expired at the assessment instant.")
                continue
            if any(item.freshness.fresh_until is None for item in support):
                gaps.add("Some evidence has no declared freshness expiry.")
            contained = _contains(schedule, local, zone)
            if contained is None:
                gaps.add("A schedule boundary is ambiguous or nonexistent during DST.")
            elif contained:
                states[schedule.topic].add(True)
            elif schedule.coverage == "complete":
                states[schedule.topic].add(False)
        opening = states["opening"]
        service = states["meal_service"]
        if len(opening) > 1 or len(service) > 1:
            gaps.add("Supplied hours conflict.")
        return AssessedOpening(
            assessed_at=local,
            opening="open"
            if opening == {True}
            else "closed"
            if opening == {False}
            else "unknown",
            meal_service="serving"
            if service == {True}
            else "not_serving"
            if service == {False}
            else "unknown",
            qualification=" ".join(
                (
                    "Based on supplied local civil-time schedules.",
                    "Source truth and holiday coverage are not independently verified.",
                    *sorted(gaps),
                )
            ),
        )

    arrival = None
    if journey.status == "estimated":
        if journey.mode != request.travel.mode:
            raise ValueError("journey mode differs from the request")
        arrival = at(now_utc + timedelta(seconds=journey.duration_seconds))
    return OpeningAssessment(
        place_id, destination_timezone, at(now_utc), arrival, schedules
    )
