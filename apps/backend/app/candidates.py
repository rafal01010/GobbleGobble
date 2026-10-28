from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.identity import IdentityReconciliation, ReconciledOutlet
from app.journey import JourneyEstimate, JourneyUnavailable
from app.opening import HoursEvidence, OpeningAssessment, assess_opening
from app.providers.geoapify import MealPlaceLead, Point, RouteEndpoint
from app.schemas.evidence import EvidenceCollection
from app.schemas.search import Place, SearchRequest, UnknownJourney

type CandidateJourney = JourneyEstimate | JourneyUnavailable


@dataclass(frozen=True)
class CandidateBinding:
    identity_id: str = field(repr=False)
    place: Place = field(repr=False)
    journey: CandidateJourney | None = field(default=None, repr=False)
    schedules: tuple[HoursEvidence, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class CandidateView:
    outlet: ReconciledOutlet = field(repr=False)
    place: Place = field(repr=False)
    journey: CandidateJourney | None = field(repr=False)
    opening: OpeningAssessment = field(repr=False)
    travel: Literal["within_limit", "outside_limit", "unconfirmed"]
    status: Literal["qualified", "unconfirmed", "excluded"]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateViews:
    request: SearchRequest = field(repr=False)
    identities: IdentityReconciliation = field(repr=False)
    evidence: EvidenceCollection = field(repr=False)
    qualified: tuple[CandidateView, ...] = field(repr=False)
    unconfirmed: tuple[CandidateView, ...] = field(repr=False)
    excluded: tuple[CandidateView, ...] = field(repr=False)
    unresolved_outlets: tuple[ReconciledOutlet, ...] = field(repr=False)


def _point(endpoint: RouteEndpoint) -> Point:
    return endpoint if isinstance(endpoint, Point) else endpoint.point


def _check_journey(
    request: SearchRequest, binding: CandidateBinding, outlet: ReconciledOutlet
) -> None:
    result = binding.journey
    if result is None:
        return
    if result.identity_id != binding.identity_id or result.constraint != request.travel:
        raise ValueError("candidate journey identity or constraint differs")
    if isinstance(result, JourneyEstimate):
        origin, destination = result.route.origin, result.route.destination
        if (
            result.route.mode != request.travel.mode
            or result.journey.mode != result.route.mode
            or result.journey.duration_seconds != result.route.duration_seconds
            or result.journey.distance_m != result.route.distance_m
            or result.journey.basis != result.route.estimate_basis
        ):
            raise ValueError("candidate route and journey facts differ")
    else:
        origin, destination = result.origin, result.destination
        if result.route is not None and (
            result.route.origin != origin
            or result.route.destination != destination
            or result.route.mode != request.travel.mode
        ):
            raise ValueError("candidate unavailable route differs")
    coordinates = request.origin.location.coordinates
    if _point(origin) != Point(coordinates.latitude, coordinates.longitude):
        raise ValueError("candidate journey origin differs from request")
    coordinates = binding.place.location.coordinates
    if _point(destination) != Point(coordinates.latitude, coordinates.longitude):
        raise ValueError("candidate journey destination differs from place")
    if not isinstance(destination, Point) and not any(
        destination == lead.lead for lead in outlet.leads
    ):
        raise ValueError("candidate route endpoint belongs to another identity")


def assemble_candidates(
    request: SearchRequest,
    identities: IdentityReconciliation,
    bindings: tuple[CandidateBinding, ...],
    *,
    evidence: EvidenceCollection,
) -> CandidateViews:
    outlets = {outlet.identity_id: outlet for outlet in identities.outlets}
    by_identity = {binding.identity_id: binding for binding in bindings}
    place_ids = {binding.place.place_id for binding in bindings}
    if (
        len(outlets) != len(identities.outlets)
        or len(by_identity) != len(bindings)
        or len(place_ids) != len(bindings)
        or not set(by_identity) <= set(outlets)
        or not place_ids <= set(evidence.place_ids)
    ):
        raise ValueError(
            "candidate bindings must have unique known identities and places"
        )
    uncertain_leads = {
        lead_id for conflict in identities.conflicts for lead_id in conflict.lead_ids
    } | {
        lead_id
        for assertion in identities.unresolved_assertions
        for lead_id in (assertion.left_lead_id, assertion.right_lead_id)
    }
    views: dict[str, list[CandidateView]] = {
        "qualified": [],
        "unconfirmed": [],
        "excluded": [],
    }
    for binding in bindings:
        outlet = outlets[binding.identity_id]
        place = binding.place
        parent = by_identity.get(outlet.parent_identity_id or "")
        if outlet.kind is None or place.kind != outlet.kind:
            raise ValueError("candidate place kind must match identified outlet")
        if (outlet.parent_identity_id is not None and parent is None) or (
            place.parent_place_id != (parent.place.place_id if parent else None)
        ):
            raise ValueError("candidate parent differs from identified containment")
        provider_leads = tuple(
            lead.lead for lead in outlet.leads if isinstance(lead.lead, MealPlaceLead)
        )
        coordinates = place.location.coordinates
        if provider_leads and not any(
            lead.point == Point(coordinates.latitude, coordinates.longitude)
            for lead in provider_leads
        ):
            raise ValueError(
                "candidate location differs from identified provider leads"
            )
        if any(
            ref.provider == "geoapify"
            and ref.provider_id not in {lead.place_id for lead in provider_leads}
            for ref in place.location.provider_refs
        ):
            raise ValueError("candidate provider reference differs from identity")
        _check_journey(request, binding, outlet)
        journey = (
            binding.journey.journey
            if binding.journey is not None
            else UnknownJourney(qualification="No route assessment was supplied.")
        )
        if place.journey.status != "unknown" and place.journey != journey:
            raise ValueError("candidate place has a different journey fact")
        opening = assess_opening(
            request,
            place_id=place.place_id,
            destination_timezone=place.location.timezone,
            journey=journey,
            evidence=evidence,
            schedules=binding.schedules,
        )
        if place.opening.status != "unknown" and place.opening != opening.now:
            raise ValueError("candidate place has a different opening fact")
        travel: Literal["within_limit", "outside_limit", "unconfirmed"] = "unconfirmed"
        reasons: list[str] = []
        if isinstance(binding.journey, JourneyEstimate):
            travel = "within_limit" if binding.journey.within_limit else "outside_limit"
            if travel == "outside_limit":
                reasons.append("over_travel_limit")
        for label, fact in (("now", opening.now), ("arrival", opening.arrival)):
            if fact is not None:
                if fact.opening == "closed":
                    reasons.append(f"closed_{label}")
                if fact.meal_service == "not_serving":
                    reasons.append(f"not_serving_{label}")
        excluded = bool(reasons)
        if travel == "unconfirmed":
            reasons.append("route_unconfirmed")
        if opening.availability == "unknown":
            reasons.append("service_unconfirmed")
        if any(lead.lead_id in uncertain_leads for lead in outlet.leads):
            reasons.append("identity_unconfirmed")
        status: Literal["qualified", "unconfirmed", "excluded"] = (
            "excluded" if excluded else "unconfirmed" if reasons else "qualified"
        )
        view = CandidateView(
            outlet,
            place.model_copy(update={"journey": journey, "opening": opening.now}),
            binding.journey,
            opening,
            travel,
            status,
            tuple(reasons),
        )
        views[status].append(view)
    return CandidateViews(
        request,
        identities,
        evidence,
        tuple(views["qualified"]),
        tuple(views["unconfirmed"]),
        tuple(views["excluded"]),
        tuple(
            outlet
            for outlet in identities.outlets
            if outlet.identity_id not in by_identity
        ),
    )
