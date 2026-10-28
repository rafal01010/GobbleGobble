from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.candidates import CandidateBinding, assemble_candidates
from app.core.calls import Receipt
from app.identity import IdentityAssertion, IdentityLead, reconcile_outlets
from app.journey import JourneyEstimate, JourneyUnavailable
from app.opening import HoursEvidence, WeeklyPeriod
from app.providers.geoapify import MealPlaceLead, Point, Provenance, RouteAvailable
from app.providers.web_research import URLCitation
from app.schemas.evidence import EvidenceCollection
from app.schemas.search import (
    Coordinates,
    EstimatedJourney,
    Place,
    ResolvedLocation,
    SearchOrigin,
    SearchRequest,
    UnknownJourney,
    WalkingConstraint,
)
from app.web_discovery import WebMealLead

_NOW = datetime(2026, 10, 28, 4, tzinfo=UTC)
_ORIGIN = Point(14.58, 120.98)
_DESTINATION = MealPlaceLead(
    "provider_branch", Point(14.59, 120.99), "Synthetic branch", None, (), Provenance()
)
_REQUEST = SearchRequest(
    origin=SearchOrigin(
        kind="chosen",
        location=ResolvedLocation(
            coordinates=Coordinates(
                latitude=_ORIGIN.latitude, longitude=_ORIGIN.longitude
            ),
            timezone="Asia/Manila",
        ),
    ),
    eat_now_at=_NOW,
)
_IDENTITIES = reconcile_outlets((IdentityLead("branch_lead", _DESTINATION, "branch"),))
_PLACE = Place(
    place_id="branch",
    kind="branch",
    location=ResolvedLocation(
        coordinates=Coordinates(latitude=14.59, longitude=120.99),
        timezone="Asia/Manila",
    ),
)


def catalog() -> EvidenceCollection:
    declaration = {"decision": "unknown", "basis": "Synthetic declaration"}
    retention = {"mode": "ephemeral", "basis": "Synthetic case"}
    return EvidenceCollection.model_validate(
        {
            "place_ids": ("branch", "other"),
            "sources": (
                {
                    "source_id": "src_hours",
                    "subject_place_ids": ("branch",),
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
                },
            ),
            "evidence": (
                {
                    "evidence_id": "ev_hours",
                    "source_id": "src_hours",
                    "subject_place_ids": ("branch",),
                    "statement": "Synthetic opening and meal-service hours",
                    "locator": {"kind": "document"},
                    "freshness": {
                        "retrieved_at": _NOW,
                        "basis": "Synthetic applicability",
                    },
                },
            ),
        }
    )


def schedules(*, start=9 * 60, end=22 * 60, meal_end=22 * 60):
    return tuple(
        HoursEvidence(
            topic=topic,
            coverage="complete",
            valid_from=date(2026, 1, 1),
            valid_through=date(2026, 12, 31),
            evidence_ids=("ev_hours",),
            weekly=tuple(
                WeeklyPeriod(weekday=day, start_minute=start, end_minute=closing)
                for day in range(7)
            ),
        )
        for topic, closing in (("opening", end), ("meal_service", meal_end))
    )


def route(seconds=900.0):
    receipt = Receipt(
        local_request_id=UUID(int=1),
        provider="geoapify",
        capability="route",
        use="api_diagnostic",
        input_sources=(),
        model_role=None,
        requested_model=None,
        model_identity="not_applicable",
        started_at=_NOW,
        duration_seconds=0.1,
        admitted=True,
        outcome="success",
        diagnostic="none",
        usage=None,
        cost=None,
    )
    return JourneyEstimate(
        "identity_1",
        _REQUEST.travel,
        _NOW,
        RouteAvailable(_ORIGIN, _DESTINATION, "walk", seconds, 800.0, Provenance()),
        EstimatedJourney(
            mode="walk",
            duration_seconds=seconds,
            distance_m=800.0,
            basis="no_live_traffic",
            qualification="Synthetic estimate",
        ),
        receipt,
    )


def unavailable():
    return JourneyUnavailable(
        "identity_1",
        _ORIGIN,
        _DESTINATION,
        _REQUEST.travel,
        _NOW,
        "not_configured",
        UnknownJourney(qualification="Synthetic route gap"),
        None,
    )


@pytest.mark.parametrize(
    "seconds,hours,status,travel,reasons",
    [
        (900.0, schedules(), "qualified", "within_limit", ()),
        (900.1, schedules(), "excluded", "outside_limit", ("over_travel_limit",)),
        (900.0, (), "unconfirmed", "within_limit", ("service_unconfirmed",)),
        (
            None,
            schedules(),
            "unconfirmed",
            "unconfirmed",
            ("route_unconfirmed", "service_unconfirmed"),
        ),
        (
            None,
            schedules(start=13 * 60),
            "excluded",
            "unconfirmed",
            ("closed_now", "not_serving_now", "route_unconfirmed"),
        ),
        (
            900.0,
            schedules(meal_end=12 * 60 + 15),
            "excluded",
            "within_limit",
            ("not_serving_arrival",),
        ),
        (
            900.0,
            schedules(end=12 * 60 + 15),
            "excluded",
            "within_limit",
            ("closed_arrival",),
        ),
    ],
)
def test_actual_assembly_partitions_practical_facts(
    seconds, hours, status, travel, reasons
):
    evidence = catalog()
    journey = unavailable() if seconds is None else route(seconds)
    result = assemble_candidates(
        _REQUEST,
        _IDENTITIES,
        (CandidateBinding("identity_1", _PLACE, journey, hours),),
        evidence=evidence,
    )
    views = (*result.qualified, *result.unconfirmed, *result.excluded)
    assert len(views) == 1
    view = views[0]
    assert (view.status, view.travel, view.reasons) == (status, travel, reasons)
    assert len(getattr(result, status)) == 1
    assert view.place.journey == journey.journey
    assert view.journey is journey and view.opening.schedules == hours
    assert view.opening.place_id == view.place.place_id == "branch"
    assert view.place.opening == view.opening.now
    assert result.evidence is evidence and result.identities is _IDENTITIES
    assert _PLACE.journey.status == "unknown" and _PLACE.opening.status == "unknown"
    assert "example.com" not in repr(result) + repr(view)


@pytest.mark.parametrize(
    "mismatch",
    ["identity", "constraint", "origin", "destination", "endpoint", "metric", "mode"],
)
def test_journey_cannot_be_attached_to_another_place_or_request(mismatch):
    journey = route()
    if mismatch == "identity":
        journey = replace(journey, identity_id="other")
    elif mismatch == "constraint":
        journey = replace(journey, constraint=WalkingConstraint(minutes=5))
    elif mismatch == "origin":
        journey = replace(
            journey, route=replace(journey.route, origin=Point(15.0, 120.98))
        )
    elif mismatch == "destination":
        journey = replace(
            journey, route=replace(journey.route, destination=Point(15.0, 121.0))
        )
    elif mismatch == "endpoint":
        journey = replace(
            journey,
            route=replace(
                journey.route,
                destination=replace(_DESTINATION, place_id="other_branch"),
            ),
        )
    elif mismatch == "metric":
        journey = replace(journey, route=replace(journey.route, duration_seconds=100.0))
    else:
        journey = replace(journey, route=replace(journey.route, mode="drive"))
    with pytest.raises(ValueError, match="candidate"):
        assemble_candidates(
            _REQUEST,
            _IDENTITIES,
            (CandidateBinding("identity_1", _PLACE, journey),),
            evidence=catalog(),
        )


def test_unbound_web_only_outlet_and_absent_route_are_explicit():
    web = WebMealLead("Synthetic unresolved web meal lead", 0, 0, 0, 34, ())
    identities = reconcile_outlets(
        (
            IdentityLead("branch_lead", _DESTINATION, "branch"),
            IdentityLead("web_lead", web),
        )
    )
    result = assemble_candidates(
        _REQUEST,
        identities,
        (CandidateBinding("identity_1", _PLACE),),
        evidence=catalog(),
    )
    assert result.qualified == result.excluded == ()
    assert result.unconfirmed[0].reasons == ("route_unconfirmed", "service_unconfirmed")
    assert result.unconfirmed[0].journey is None
    assert result.unconfirmed[0].opening.arrival is None
    assert result.unresolved_outlets == (identities.outlets[1],)
    assert result.unresolved_outlets[0].leads[0].lead is web


def test_duplicate_bindings_wrong_place_location_and_cross_branch_hours_are_rejected():
    binding = CandidateBinding("identity_1", _PLACE)
    with pytest.raises(ValueError, match="unique known"):
        assemble_candidates(
            _REQUEST, _IDENTITIES, (binding, binding), evidence=catalog()
        )
    wrong = _PLACE.model_copy(update={"parent_place_id": "other"})
    with pytest.raises(ValueError, match="parent"):
        assemble_candidates(
            _REQUEST, _IDENTITIES, (replace(binding, place=wrong),), evidence=catalog()
        )
    wrong = _PLACE.model_copy(update={"location": _REQUEST.origin.location})
    with pytest.raises(ValueError, match="location"):
        assemble_candidates(
            _REQUEST, _IDENTITIES, (replace(binding, place=wrong),), evidence=catalog()
        )
    wrong = _PLACE.model_copy(update={"place_id": "other"})
    with pytest.raises(ValueError, match="outside the assessed place"):
        assemble_candidates(
            _REQUEST,
            _IDENTITIES,
            (replace(binding, place=wrong, schedules=schedules()),),
            evidence=catalog(),
        )


def support():
    text = "Synthetic containment or identity assertion"
    return WebMealLead(
        text,
        0,
        0,
        0,
        len(text),
        (URLCitation(0, len(text), "Synthetic", "https://example.com/identity"),),
    )


def test_parent_mapping_preserves_stall_and_venue_as_separate_views():
    venue = replace(_DESTINATION, place_id="provider_venue")
    identities = reconcile_outlets(
        (
            IdentityLead("stall", _DESTINATION, "stall"),
            IdentityLead("venue", venue, "venue"),
        ),
        (
            IdentityAssertion(
                "stall", "venue", "contained_by", support(), "Synthetic mapping"
            ),
        ),
    )
    child_place = _PLACE.model_copy(
        update={"kind": "stall", "parent_place_id": "venue"}
    )
    venue_place = _PLACE.model_copy(update={"place_id": "venue", "kind": "venue"})
    evidence = EvidenceCollection(place_ids=("branch", "venue"))
    child = CandidateBinding("identity_1", child_place)
    parent = CandidateBinding("identity_2", venue_place)
    result = assemble_candidates(
        _REQUEST, identities, (child, parent), evidence=evidence
    )
    assert tuple(view.place.place_id for view in result.unconfirmed) == (
        "branch",
        "venue",
    )
    assert result.unconfirmed[0].place.parent_place_id == "venue"
    assert result.unconfirmed[0].outlet.parent_identity_id == "identity_2"
    with pytest.raises(ValueError, match="parent"):
        assemble_candidates(_REQUEST, identities, (child,), evidence=evidence)
    with pytest.raises(ValueError, match="parent"):
        assemble_candidates(
            _REQUEST,
            identities,
            (
                replace(
                    child,
                    place=child_place.model_copy(update={"parent_place_id": "wrong"}),
                ),
                parent,
            ),
            evidence=evidence,
        )


def test_conflicting_identity_and_stale_embedded_practical_facts_cannot_qualify():
    other = replace(_DESTINATION, place_id="other_provider")
    identities = reconcile_outlets(
        (
            IdentityLead("branch_lead", _DESTINATION, "branch"),
            IdentityLead("other_lead", other, "branch"),
        ),
        tuple(
            IdentityAssertion(
                "branch_lead", "other_lead", relation, support(), "Synthetic conflict"
            )
            for relation in ("same_place", "distinct")
        ),
    )
    result = assemble_candidates(
        _REQUEST,
        identities,
        (CandidateBinding("identity_1", _PLACE, route(), schedules()),),
        evidence=catalog(),
    )
    assert result.qualified == ()
    assert result.unconfirmed[0].reasons == ("identity_unconfirmed",)
    assert result.identities.unresolved_assertions
    assert result.unresolved_outlets == (identities.outlets[1],)
    borrowed = _PLACE.model_copy(
        update={"journey": EstimatedJourney(mode="walk", duration_seconds=1.0)}
    )
    with pytest.raises(ValueError, match="different journey"):
        assemble_candidates(
            _REQUEST,
            _IDENTITIES,
            (CandidateBinding("identity_1", borrowed, route(), schedules()),),
            evidence=catalog(),
        )
    stale = result.unconfirmed[0].place
    later = _REQUEST.model_copy(
        update={"eat_now_at": datetime(2026, 10, 28, 15, tzinfo=UTC)}
    )
    with pytest.raises(ValueError, match="different opening"):
        assemble_candidates(
            later,
            _IDENTITIES,
            (CandidateBinding("identity_1", stale, route(), schedules()),),
            evidence=catalog(),
        )
