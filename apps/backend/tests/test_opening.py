import json
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.opening import (
    DateHours,
    HoursEvidence,
    LocalPeriod,
    WeeklyPeriod,
    assess_opening,
    parse_osm_opening_hours,
)
from app.schemas.evidence import EvidenceCollection
from app.schemas.search import (
    Coordinates,
    EstimatedJourney,
    ResolvedLocation,
    SearchOrigin,
    SearchRequest,
    UnknownJourney,
)


def catalog(*, expires: str | None = None) -> EvidenceCollection:
    declaration = {"decision": "unknown", "basis": "Synthetic declaration"}
    retention = {"mode": "ephemeral", "basis": "Synthetic local example"}
    return EvidenceCollection.model_validate_json(
        json.dumps(
            {
                "place_ids": ["branch", "other_branch"],
                "sources": [
                    {
                        "source_id": "src_hours",
                        "subject_place_ids": ["branch", "other_branch"],
                        "publisher": "Synthetic publisher",
                        "source_url": "https://example.com/hours",
                        "origin": {"kind": "independent", "access_method": "Synthetic"},
                        "policy": {
                            key: declaration
                            for key in (
                                "access",
                                "display",
                                "model_input",
                                "derived_output",
                                "tracing",
                            )
                        },
                        "retention": {
                            key: retention
                            for key in ("raw_content", "derived_content", "identifiers")
                        },
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "ev_hours",
                        "source_id": "src_hours",
                        "subject_place_ids": ["branch"],
                        "statement": "Synthetic scoped hours assertion",
                        "locator": {
                            "kind": "document",
                            "exact_quote": "Synthetic hours",
                        },
                        "freshness": {
                            "retrieved_at": "2026-11-02T12:00:00Z",
                            "fresh_until": expires,
                            "basis": "Synthetic source applicability",
                        },
                    }
                ],
            }
        )
    )


def request(instant: str) -> SearchRequest:
    return SearchRequest(
        origin=SearchOrigin(
            kind="chosen",
            location=ResolvedLocation(
                coordinates=Coordinates(latitude=14.6, longitude=121.0),
                timezone="Asia/Manila",
            ),
        ),
        eat_now_at=datetime.fromisoformat(instant),
    )


def hours(
    start: int = 9 * 60,
    end: int = 22 * 60,
    *,
    topic: str = "opening",
    coverage: str = "complete",
    weekday: int | None = None,
) -> HoursEvidence:
    return HoursEvidence.model_validate(
        {
            "topic": topic,
            "coverage": coverage,
            "valid_from": date(2026, 1, 1),
            "valid_through": date(2026, 12, 31),
            "evidence_ids": ("ev_hours",),
            "weekly": tuple(
                WeeklyPeriod(weekday=day, start_minute=start, end_minute=end)
                for day in range(7)
                if weekday is None or day == weekday
            ),
        }
    )


def assess(
    instant: str,
    schedules: tuple[HoursEvidence, ...],
    *,
    seconds: float | None = 900,
    zone: str = "Asia/Manila",
    expires: str | None = None,
):
    return assess_opening(
        request(instant),
        place_id="branch",
        destination_timezone=zone,
        journey=UnknownJourney()
        if seconds is None
        else EstimatedJourney(mode="walk", duration_seconds=seconds),
        evidence=catalog(expires=expires),
        schedules=schedules,
    )


def test_source_retrieval_after_eat_now_and_destination_zone_are_supported() -> None:
    result = assess(
        "2026-10-28T04:00:00Z",
        (
            hours(),
            hours(topic="meal_service"),
        ),
        zone="America/New_York",
    )
    assert result.now.assessed_at.isoformat() == "2026-10-28T00:00:00-04:00"
    assert result.now.opening == "closed"
    assert result.availability == "unavailable"
    result = assess(
        "2026-10-28T04:00:00Z",
        (
            hours(),
            hours(topic="meal_service"),
        ),
    )
    assert result.now.assessed_at.isoformat() == "2026-10-28T12:00:00+08:00"
    assert result.arrival is not None
    assert result.arrival.assessed_at.isoformat() == "2026-10-28T12:15:00+08:00"
    assert result.availability == "available"
    assert result.schedules[0].evidence_ids == ("ev_hours",)


def test_arrival_at_closing_is_unavailable_and_meal_service_is_independent() -> None:
    result = assess(
        "2026-10-28T13:45:00Z",
        (
            hours(),
            hours(end=21 * 60 + 55, topic="meal_service"),
        ),
    )
    assert result.now.opening == "open"
    assert result.now.meal_service == "serving"
    assert result.arrival is not None
    assert result.arrival.opening == "closed"
    assert result.arrival.meal_service == "not_serving"
    assert result.availability == "unavailable"
    result = assess(
        "2026-10-28T13:50:00Z",
        (
            hours(),
            hours(end=21 * 60, topic="meal_service"),
        ),
        seconds=0,
    )
    assert result.now.opening == "open"
    assert result.now.meal_service == "not_serving"
    assert result.availability == "unavailable"


def test_closed_now_does_not_become_available_when_open_at_arrival() -> None:
    result = assess(
        "2026-10-28T00:50:00Z",
        (
            hours(),
            hours(topic="meal_service"),
        ),
    )
    assert result.now.opening == "closed"
    assert result.arrival is not None
    assert result.arrival.opening == "open"
    assert result.availability == "unavailable"


def test_overnight_sunday_hours_carry_into_monday_and_close_exclusively() -> None:
    schedules = (hours(22 * 60, 2 * 60, weekday=6),)
    result = assess("2026-11-01T17:50:00Z", schedules, seconds=600)
    assert result.now.assessed_at.isoformat() == "2026-11-02T01:50:00+08:00"
    assert result.now.opening == "open"
    assert result.arrival is not None
    assert result.arrival.opening == "closed"
    assert result.availability == "unavailable"


def test_missing_partial_hours_and_unavailable_journey_remain_unknown() -> None:
    result = assess("2026-10-28T04:00:00Z", ())
    assert result.now.opening == "unknown"
    assert result.now.meal_service == "unknown"
    assert result.availability == "unknown"
    result = assess("2026-10-28T15:00:00Z", (hours(coverage="partial"),))
    assert result.now.opening == "unknown"
    result = assess(
        "2026-10-28T04:00:00Z",
        (
            hours(),
            hours(topic="meal_service"),
        ),
        seconds=None,
    )
    assert result.now.opening == "open"
    assert result.now.meal_service == "serving"
    assert result.arrival is None
    assert result.availability == "unknown"


def test_date_exception_and_explicit_all_day_hours() -> None:
    schedule = hours(0, 1440).model_copy(
        update={"exceptions": (DateHours(day=date(2026, 10, 28), periods=()),)}
    )
    assert assess("2026-10-28T04:00:00Z", (schedule,)).now.opening == "closed"
    assert assess("2026-10-29T04:00:00Z", (schedule,)).now.opening == "open"


def test_expired_arrival_outside_applicability_and_conflicts_stay_unknown() -> None:
    result = assess("2026-10-28T04:00:00Z", (hours(),), expires="2026-10-28T04:15:00Z")
    assert result.now.opening == "open"
    assert result.arrival is not None
    assert result.arrival.opening == "unknown"
    assert result.availability == "unknown"
    assert "expired" in result.arrival.qualification
    schedule = hours().model_copy(update={"valid_through": date(2026, 10, 27)})
    assert assess("2026-10-28T04:00:00Z", (schedule,)).now.opening == "unknown"
    result = assess("2026-10-28T04:00:00Z", (hours(), hours(13 * 60, 14 * 60)))
    assert result.now.opening == "unknown"
    assert "conflict" in result.now.qualification
    assert result.availability == "unknown"


def test_spring_forward_arrival_uses_elapsed_seconds() -> None:
    result = assess(
        "2026-03-08T06:50:00Z",
        (
            hours(0, 4 * 60),
            hours(0, 4 * 60, topic="meal_service"),
        ),
        seconds=1200,
        zone="America/New_York",
    )
    assert result.now.assessed_at.isoformat() == "2026-03-08T01:50:00-05:00"
    assert result.arrival is not None
    assert result.arrival.assessed_at.isoformat() == "2026-03-08T03:10:00-04:00"
    assert result.availability == "available"


def test_fall_back_arrival_keeps_elapsed_time_across_the_fold() -> None:
    result = assess(
        "2026-11-01T05:50:00Z",
        (hours(0, 4 * 60), hours(0, 4 * 60, topic="meal_service")),
        seconds=1200,
        zone="America/New_York",
    )
    assert result.now.assessed_at.isoformat() == "2026-11-01T01:50:00-04:00"
    assert result.arrival is not None
    assert result.arrival.assessed_at.isoformat() == "2026-11-01T01:10:00-05:00"
    assert result.arrival.assessed_at.fold == 1
    assert result.availability == "available"


@pytest.mark.parametrize(
    "instant,offset,fold",
    [
        ("2026-11-01T05:15:00Z", "-04:00", 0),
        ("2026-11-01T06:15:00Z", "-05:00", 1),
    ],
)
def test_fall_back_both_folds_in_unambiguous_span(instant, offset, fold) -> None:
    result = assess(instant, (hours(0, 4 * 60),), seconds=0, zone="America/New_York")
    assert result.now.assessed_at.isoformat() == f"2026-11-01T01:15:00{offset}"
    assert result.now.assessed_at.fold == fold
    assert result.now.opening == "open"


@pytest.mark.parametrize(
    "instant,start,end",
    [
        ("2026-11-01T06:15:00Z", 60, 90),
        ("2026-03-08T07:15:00Z", 150, 240),
    ],
)
def test_ambiguous_or_nonexistent_boundary_stays_unknown(instant, start, end) -> None:
    result = assess(instant, (hours(start, end),), zone="America/New_York")
    assert result.now.opening == "unknown"
    assert "ambiguous or nonexistent" in result.now.qualification
    assert result.availability == "unknown"


def test_evidence_cannot_transfer_to_another_branch_or_unknown_id() -> None:
    with pytest.raises(ValueError, match="outside the assessed place"):
        assess_opening(
            request("2026-10-28T04:00:00Z"),
            place_id="other_branch",
            destination_timezone="Asia/Manila",
            journey=UnknownJourney(),
            evidence=catalog(),
            schedules=(hours(),),
        )
    with pytest.raises(ValueError, match="outside the assessed place"):
        assess(
            "2026-10-28T04:00:00Z",
            (hours().model_copy(update={"evidence_ids": ("ev_missing",)}),),
        )


def test_ambiguous_or_unchecked_periods_are_rejected() -> None:
    with pytest.raises(ValidationError):
        LocalPeriod(start_minute=0, end_minute=0)
    with pytest.raises(ValidationError):
        assess(
            "2026-10-28T04:00:00Z",
            (hours().model_copy(update={"valid_through": date(2025, 1, 1)}),),
        )


def test_osm_schedule_is_source_scoped_partial_and_local_date_bounded() -> None:
    schedule = parse_osm_opening_hours(
        "Mo-Fr 09:00-14:00, 17:00-22:00; Sa-Su 11:00-23:00",
        evidence_id="ev_hours",
        local_date=date(2026, 10, 28),
    )
    assert schedule is not None
    midday = assess("2026-10-28T04:00:00Z", (schedule,))
    assert midday.now.opening == "open"
    assert midday.now.meal_service == "unknown"
    assert midday.availability == "unknown"
    afternoon = assess("2026-10-28T07:00:00Z", (schedule,))
    assert afternoon.now.opening == "unknown"
    following_day = assess("2026-10-29T04:00:00Z", (schedule,))
    assert following_day.now.opening == "unknown"
    assert "outside" in following_day.now.qualification


def test_osm_overnight_and_all_day_are_interpreted() -> None:
    local_day = date(2026, 10, 28)
    overnight = parse_osm_opening_hours(
        "Tu 22:00-02:00", evidence_id="ev_hours", local_date=local_day
    )
    assert overnight is not None
    assert assess("2026-10-27T17:00:00Z", (overnight,)).now.opening == "open"
    all_day = parse_osm_opening_hours(
        "24/7", evidence_id="ev_hours", local_date=local_day
    )
    assert all_day is not None
    assert assess("2026-10-28T04:00:00Z", (all_day,)).now.opening == "open"


@pytest.mark.parametrize(
    "value",
    [
        "Mo-Fr 09:00-18:00; PH off",
        "Mo-Su 09:00-20:00; Fr 10:00-22:00",
        "Mo 09:00-24:01",
        "Mo 09:00-09:00",
        "Mo 09:00+",
        "",
    ],
)
def test_unsupported_or_overriding_osm_rules_stay_unknown(value: str) -> None:
    assert (
        parse_osm_opening_hours(
            value, evidence_id="ev_hours", local_date=date(2026, 10, 28)
        )
        is None
    )
