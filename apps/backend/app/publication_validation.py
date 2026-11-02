from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.core.source_policy import Source
from app.evidence_validity import DerivedArtifact, assess_artifacts
from app.journey import JourneyEstimate
from app.opening import HoursEvidence, assess_opening, parse_osm_opening_hours
from app.providers.geoapify import Point, RouteEndpoint
from app.schemas.evidence import UnknownFoodClaim
from app.schemas.recommendations import Recommendation, RecommendationCollection


@dataclass(frozen=True)
class ClaimAssertion:
    place_id: str
    kind: Literal["food", "menu", "price", "complaint"]
    reference_id: str
    evidence_ids: tuple[str, ...]
    finding_index: int | None = None
    finding_source: Literal["google", "fallback"] = "google"


@dataclass(frozen=True)
class CheckedRecommendation:
    place_id: str = field(repr=False)
    claims: tuple[ClaimAssertion, ...] = field(repr=False)
    route_status: Literal["within_limit", "unconfirmed"]
    rationale_status: Literal["unverified"] = "unverified"


@dataclass(frozen=True)
class ComparisonAssertion:
    preferred_place_id: str
    alternative_place_id: str
    preferred_claim: ClaimAssertion
    alternative_claim: ClaimAssertion


@dataclass(frozen=True)
class OpeningEvidence:
    schedule: HoursEvidence
    osm_value: str


@dataclass(frozen=True)
class ValidatedPublication:
    qualified_primaries: tuple[CheckedRecommendation, ...]
    qualified_runners: tuple[CheckedRecommendation, ...]
    unconfirmed_primaries: tuple[CheckedRecommendation, ...]
    unconfirmed_runners: tuple[CheckedRecommendation, ...]
    comparisons: tuple[ComparisonAssertion, ...]
    unverified_draft: RecommendationCollection = field(repr=False)


def _point(endpoint: RouteEndpoint) -> Point:
    return endpoint if isinstance(endpoint, Point) else endpoint.point


def validate_publication(
    collection: RecommendationCollection,
    *,
    assertions: tuple[ClaimAssertion, ...] = (),
    comparisons: tuple[ComparisonAssertion, ...] = (),
    routes: Mapping[str, JourneyEstimate] | None = None,
    route_fresh_for: timedelta | None = None,
    schedules: Mapping[str, tuple[OpeningEvidence, ...]] | None = None,
    source_classes: Mapping[str, Source],
    now: datetime,
) -> ValidatedPublication:
    """Check declared references and route observations before publication.

    Owner rationale has no structured claim lineage and is not display-ready.
    """
    if now.utcoffset() is None:
        raise ValueError("publication time must be timezone-aware")
    if route_fresh_for is not None and route_fresh_for <= timedelta(0):
        raise ValueError("route freshness bound must be positive")
    collection = RecommendationCollection.model_validate(collection)
    if len(collection.versions) != 1:
        raise ValueError("publication requires one version")
    version = collection.versions[0]
    snapshot = next(
        s for s in collection.snapshots if s.snapshot_id == version.research_snapshot_id
    )
    evidence = snapshot.evidence
    selected = (*version.primaries, *version.runners_up)
    places = {p.place_id: p for p in snapshot.researched_candidates}
    selected_ids = {r.place_id for r in selected}
    if any(a.place_id not in selected_ids for a in assertions):
        raise ValueError("assertion subject is not selected")
    if len(set(assertions)) != len(assertions):
        raise ValueError("duplicate assertion")
    claims = {c.claim_id: c for c in evidence.food_claims}
    assessments = {a.assessment_id: a for a in evidence.review_assessments}
    by_place: dict[str, list[ClaimAssertion]] = {key: [] for key in selected_ids}
    declared = list(assertions)
    for recommendation in selected:
        for claim_id in recommendation.food_claim_ids:
            claim = claims[claim_id]
            if isinstance(claim, UnknownFoodClaim):
                raise TypeError("unknown claim cannot be asserted")
            subject = next(
                x for x in claim.subjects if x.place_id == recommendation.place_id
            )
            assertion = ClaimAssertion(
                recommendation.place_id, claim.topic, claim_id, subject.evidence_ids
            )
            if assertion not in declared:
                declared.append(assertion)
    for index, assertion in enumerate(declared):
        if assertion.kind not in (
            "food",
            "menu",
            "price",
            "complaint",
        ) or assertion.finding_source not in ("google", "fallback"):
            raise ValueError("unknown assertion kind or source")
        if not assertion.evidence_ids or len(set(assertion.evidence_ids)) != len(
            assertion.evidence_ids
        ):
            raise ValueError("assertion needs unique supporting evidence")
        if assertion.kind == "complaint":
            assessment = assessments.get(assertion.reference_id)
            if assessment is None or assessment.place_id != assertion.place_id:
                raise ValueError(
                    "complaint assessment belongs to another place or does not exist"
                )
            findings = (
                assessment.findings
                if assertion.finding_source == "google"
                else assessment.fallback.findings
                if assessment.fallback
                else ()
            )
            if (
                assertion.finding_index is None
                or not 0 <= assertion.finding_index < len(findings)
            ):
                raise ValueError("unknown complaint finding")
            finding = findings[assertion.finding_index]
            if finding.topic != "complaint":
                raise ValueError("referenced finding is not a complaint")
            subjects = finding.subjects
        else:
            referenced = claims.get(assertion.reference_id)
            if (
                referenced is None
                or isinstance(referenced, UnknownFoodClaim)
                or referenced.topic != assertion.kind
                or assertion.finding_index is not None
            ):
                raise ValueError("unknown or wrong-kind claim")
            subjects = referenced.subjects
        binding = next((s for s in subjects if s.place_id == assertion.place_id), None)
        if binding is None or set(assertion.evidence_ids) != set(binding.evidence_ids):
            raise ValueError("claim evidence does not support the asserted place")
        validity = assess_artifacts(
            evidence,
            (
                DerivedArtifact(
                    f"claim_{index}",
                    "claim",
                    assertion.evidence_ids,
                    place_id=assertion.place_id,
                ),
            ),
            source_classes=dict(source_classes),
            now=now,
            action="display",
        )[0]
        if not validity.allowed:
            raise ValueError(
                f"claim evidence unavailable: {validity.freshness}, {validity.retention}"
            )
        by_place[assertion.place_id].append(assertion)
    for comparison in comparisons:
        if (
            comparison.preferred_place_id == comparison.alternative_place_id
            or comparison.preferred_claim.place_id != comparison.preferred_place_id
            or comparison.alternative_claim.place_id != comparison.alternative_place_id
            or comparison.preferred_claim not in declared
            or comparison.alternative_claim not in declared
            or next(
                i
                for i, r in enumerate(selected)
                if r.place_id == comparison.preferred_place_id
            )
            >= next(
                i
                for i, r in enumerate(selected)
                if r.place_id == comparison.alternative_place_id
            )
        ):
            raise ValueError(
                "comparison does not bind each selected place to its claim"
            )
    route_map = dict(routes or {})
    schedule_map = dict(schedules or {})
    if not set(route_map) <= selected_ids:
        raise ValueError("route belongs to an unselected place")
    if not set(schedule_map) <= selected_ids:
        raise ValueError("hours belong to an unselected place")

    def checked(item: Recommendation) -> CheckedRecommendation:
        place = places[item.place_id]
        observation = route_map.get(item.place_id)
        if place.opening.status != "unknown":
            hours = schedule_map.get(item.place_id, ())
            if not hours:
                raise ValueError("opening claim lacks verified source binding")
            for index, supplied in enumerate(hours):
                schedule = supplied.schedule
                if len(schedule.evidence_ids) != 1:
                    raise ValueError(
                        "opening requires one directly scoped hours observation"
                    )
                evidence_id = schedule.evidence_ids[0]
                source_item = next(
                    (
                        item
                        for item in evidence.evidence
                        if item.evidence_id == evidence_id
                    ),
                    None,
                )
                if (
                    source_item is None
                    or source_item.statement != supplied.osm_value
                    or source_classes.get(source_item.source_id) != "geoapify_geography"
                    or parse_osm_opening_hours(
                        supplied.osm_value,
                        evidence_id=evidence_id,
                        local_date=collection.request.eat_now_at.astimezone(
                            ZoneInfo(place.location.timezone)
                        ).date(),
                    )
                    != schedule
                ):
                    raise ValueError(
                        "opening evidence is not the cited geographic hours observation"
                    )
                valid = assess_artifacts(
                    evidence,
                    (
                        DerivedArtifact(
                            f"hours_{index}",
                            "claim",
                            schedule.evidence_ids,
                            place_id=item.place_id,
                        ),
                    ),
                    source_classes=dict(source_classes),
                    now=now,
                    action="display",
                )[0]
                if not valid.allowed:
                    raise ValueError("opening source unavailable")
            assessed = assess_opening(
                collection.request,
                place_id=item.place_id,
                destination_timezone=place.location.timezone,
                journey=place.journey,
                evidence=evidence,
                schedules=tuple(item.schedule for item in hours),
            )
            if place.opening != assessed.now:
                raise ValueError("opening claim differs from assessed hours")
            if (
                assessed.now.opening == "closed"
                or assessed.now.meal_service == "not_serving"
            ):
                raise ValueError("place is not currently serving")
        if observation is None:
            if place.journey.status != "unknown":
                raise ValueError("journey claim lacks a route observation")
            return CheckedRecommendation(
                item.place_id, tuple(by_place[item.place_id]), "unconfirmed"
            )
        request = collection.request
        route = observation.route
        origin = request.origin.location.coordinates
        destination = place.location.coordinates
        if (
            observation.identity_id != item.place_id
            or observation.constraint != request.travel
            or _point(route.origin) != Point(origin.latitude, origin.longitude)
            or _point(route.destination)
            != Point(destination.latitude, destination.longitude)
            or route.mode != request.travel.mode
            or observation.journey.mode != route.mode
            or observation.journey.duration_seconds != route.duration_seconds
            or observation.journey.distance_m != route.distance_m
            or observation.journey.basis != route.estimate_basis
            or observation.observed_at.utcoffset() is None
            or observation.observed_at > now
            or not observation.receipt.admitted
            or observation.receipt.capability != "route"
            or observation.receipt.provider != "geoapify"
            or observation.receipt.outcome != "success"
            or observation.receipt.started_at > observation.observed_at
            or place.journey.status != "unknown"
            and place.journey != observation.journey
        ):
            raise ValueError("route does not match the selected place and request")
        if not observation.within_limit:
            raise ValueError("route exceeds the travel limit")
        if route_fresh_for is None or observation.observed_at < now - route_fresh_for:
            if place.journey.status != "unknown":
                raise ValueError("route observation is stale")
            return CheckedRecommendation(
                item.place_id, tuple(by_place[item.place_id]), "unconfirmed"
            )
        return CheckedRecommendation(
            item.place_id, tuple(by_place[item.place_id]), "within_limit"
        )

    primary = tuple(checked(item) for item in version.primaries)
    runners = tuple(checked(item) for item in version.runners_up)
    return ValidatedPublication(
        tuple(x for x in primary if x.route_status == "within_limit"),
        tuple(x for x in runners if x.route_status == "within_limit"),
        tuple(x for x in primary if x.route_status == "unconfirmed"),
        tuple(x for x in runners if x.route_status == "unconfirmed"),
        comparisons,
        collection,
    )
