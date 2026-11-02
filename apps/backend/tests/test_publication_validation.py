import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.publication_validation import ClaimAssertion, validate_publication
from app.schemas.recommendations import RecommendationCollection

NOW = datetime(2026, 10, 25, 5, tzinfo=UTC)
PATH = Path("../../docs/examples/recommendation-contract/accepted_full_set.json")
pytest_plugins = ["test_foundation"]


def fixture():
    data = json.loads(PATH.read_text())
    data["versions"][0]["primaries"] = data["versions"][0]["primaries"][:2]
    data["versions"][0]["runners_up"] = []
    data["versions"][0]["shortfall_reason"] = "Only two candidates."
    data["versions"][0]["food_choices"] = []
    evidence = data["snapshots"][0]["evidence"]
    evidence["review_assessments"] = []
    for declaration in evidence["sources"][0]["policy"].values():
        declaration["decision"] = "allowed"
    for item in evidence["evidence"]:
        item["freshness"]["fresh_until"] = "2026-10-26T05:00:00Z"
    for branch in ("a", "b"):
        evidence["food_claims"].append(
            {
                "claim_id": f"test_price_{branch}",
                "status": "sourced",
                "topic": "price",
                "statement": "An example price.",
                "subjects": [
                    {
                        "place_id": f"branch_{branch}",
                        "evidence_ids": [f"ev_menu_{branch}"],
                    }
                ],
            }
        )
        evidence["review_assessments"].append(
            {
                "assessment_id": f"assessment_{branch}",
                "place_id": f"branch_{branch}",
                "google": {"status": "no_match", "reason": "No Google match."},
                "fallback": {
                    "kind": "food_evidence",
                    "source_id": "src_group",
                    "reason": "no_match",
                    "evidence_ids": [f"ev_menu_{branch}"],
                    "findings": [
                        {
                            "topic": "complaint",
                            "statement": "Service was slow.",
                            "subjects": [
                                {
                                    "place_id": f"branch_{branch}",
                                    "evidence_ids": [f"ev_menu_{branch}"],
                                }
                            ],
                        }
                    ],
                },
            }
        )
    return data


def validate(data, assertions=()):
    return validate_publication(
        RecommendationCollection.model_validate_json(json.dumps(data)),
        assertions=assertions,
        source_classes={"src_group": "permitted_independent"},
        now=NOW,
    )


def test_valid_same_branch_and_shared_source_remain_separate_from_route_unconfirmed():
    data = fixture()
    result = validate(
        data,
        (
            ClaimAssertion("branch_a", "price", "test_price_a", ("ev_menu_a",)),
            ClaimAssertion(
                "branch_b", "complaint", "assessment_b", ("ev_menu_b",), 0, "fallback"
            ),
        ),
    )
    assert not result.qualified_primaries
    assert [item.place_id for item in result.unconfirmed_primaries] == [
        "branch_a",
        "branch_b",
    ]
    assert result.unconfirmed_primaries[0].rationale_status == "unverified"
    assert len(result.unconfirmed_primaries[0].claims) == 2
    assert not hasattr(result.unconfirmed_primaries[0], "why_to_go")


@pytest.mark.parametrize(
    "assertion",
    [
        ClaimAssertion("branch_a", "menu", "menu_b", ("ev_menu_b",)),
        ClaimAssertion("branch_a", "price", "test_price_b", ("ev_menu_b",)),
        ClaimAssertion(
            "branch_a", "complaint", "assessment_b", ("ev_menu_b",), 0, "fallback"
        ),
        ClaimAssertion("branch_a", "menu", "test_price_a", ("ev_menu_a",)),
        ClaimAssertion("branch_a", "price", "missing", ("ev_menu_a",)),
        ClaimAssertion(
            "branch_a", "complaint", "assessment_a", ("ev_menu_a",), 20, "fallback"
        ),
        ClaimAssertion("branch_a", "menu", "menu_a", ("ev_menu_b",)),
    ],
)
def test_swapped_branch_wrong_kind_and_missing_reference_rejected(assertion):
    with pytest.raises(ValueError):
        validate(fixture(), (assertion,))


def test_stale_and_restricted_sources_rejected():
    stale = fixture()
    stale["snapshots"][0]["evidence"]["evidence"][0]["freshness"]["fresh_until"] = (
        "2026-10-25T04:30:00Z"
    )
    with pytest.raises(ValueError, match="stale"):
        validate(stale)
    restricted = fixture()
    restricted["snapshots"][0]["evidence"]["sources"][0]["policy"]["display"][
        "decision"
    ] = "prohibited"
    with pytest.raises(ValueError, match="unavailable"):
        validate(restricted)


def test_missing_optional_information_remains_available_and_unconfirmed():
    data = fixture()
    for item in data["versions"][0]["primaries"]:
        item["food_claim_ids"] = []
    data["snapshots"][0]["evidence"]["sources"] = []
    data["snapshots"][0]["evidence"]["evidence"] = []
    data["snapshots"][0]["evidence"]["food_claims"] = []
    data["snapshots"][0]["evidence"]["review_assessments"] = []
    assert len(validate(data).unconfirmed_primaries) == 2


def test_duplicate_or_excessive_groups_cannot_be_validated():
    data = fixture()
    data["versions"][0]["primaries"].append(data["versions"][0]["primaries"][0])
    with pytest.raises(ValidationError):
        validate(data)


def test_route_must_match_origin_destination_mode_and_limit(receipt):
    from dataclasses import replace

    from app.journey import JourneyEstimate
    from app.providers.geoapify import Point, Provenance, RouteAvailable
    from app.schemas.search import EstimatedJourney

    data = fixture()
    collection = RecommendationCollection.model_validate_json(json.dumps(data))
    place = collection.snapshots[0].researched_candidates[0]
    origin = collection.request.origin.location.coordinates
    destination = place.location.coordinates
    route = RouteAvailable(
        Point(origin.latitude, origin.longitude),
        Point(destination.latitude, destination.longitude),
        "walk",
        300.0,
        250.0,
        Provenance("{}"),
    )
    journey = EstimatedJourney(
        mode="walk", duration_seconds=300.0, distance_m=250.0, basis="no_live_traffic"
    )
    observed = JourneyEstimate(
        place.place_id,
        collection.request.travel,
        NOW,
        route,
        journey,
        receipt.model_copy(
            update={"capability": "route", "started_at": NOW - timedelta(minutes=20)}
        ),
    )
    result = validate_publication(
        collection,
        routes={place.place_id: observed},
        route_fresh_for=timedelta(minutes=15),
        source_classes={"src_group": "permitted_independent"},
        now=NOW,
    )
    assert [item.place_id for item in result.qualified_primaries] == ["branch_a"]
    assert [item.place_id for item in result.unconfirmed_primaries] == ["branch_b"]
    stale = replace(observed, observed_at=NOW - timedelta(minutes=16))
    old = validate_publication(
        collection,
        routes={place.place_id: stale},
        route_fresh_for=timedelta(minutes=15),
        source_classes={"src_group": "permitted_independent"},
        now=NOW,
    )
    assert [item.place_id for item in old.unconfirmed_primaries] == [
        "branch_a",
        "branch_b",
    ]
    without_bound = validate_publication(
        collection,
        routes={place.place_id: observed},
        source_classes={"src_group": "permitted_independent"},
        now=NOW,
    )
    assert len(without_bound.unconfirmed_primaries) == 2
    for bad_route in (
        replace(route, destination=Point(0, 0)),
        replace(route, mode="drive"),
    ):
        with pytest.raises(ValueError, match="route does not match"):
            validate_publication(
                collection,
                routes={place.place_id: replace(observed, route=bad_route)},
                source_classes={"src_group": "permitted_independent"},
                now=NOW,
            )
    longer_route = replace(route, duration_seconds=9999.0)
    longer = observed.journey.model_copy(update={"duration_seconds": 9999.0})
    with pytest.raises(ValueError, match="exceeds"):
        validate_publication(
            collection,
            routes={
                place.place_id: replace(observed, route=longer_route, journey=longer)
            },
            source_classes={"src_group": "permitted_independent"},
            now=NOW,
        )


def test_opening_requires_fresh_same_place_schedule():
    from copy import deepcopy

    from app.opening import assess_opening, parse_osm_opening_hours
    from app.publication_validation import OpeningEvidence

    data = fixture()
    evidence = data["snapshots"][0]["evidence"]
    source = deepcopy(evidence["sources"][0])
    source["source_id"] = "src_geo"
    evidence["sources"].append(source)
    hours = deepcopy(evidence["evidence"][0])
    hours.update(
        {"evidence_id": "ev_hours_a", "source_id": "src_geo", "statement": "24/7"}
    )
    evidence["evidence"].append(hours)
    collection = RecommendationCollection.model_validate_json(json.dumps(data))
    snapshot = collection.snapshots[0]
    place = snapshot.researched_candidates[0]
    local_date = collection.request.eat_now_at.date()
    schedule = parse_osm_opening_hours(
        "24/7", evidence_id="ev_hours_a", local_date=local_date
    )
    assessed = assess_opening(
        collection.request,
        place_id=place.place_id,
        destination_timezone=place.location.timezone,
        journey=place.journey,
        evidence=snapshot.evidence,
        schedules=(schedule,),
    )
    data["snapshots"][0]["researched_candidates"][0]["opening"] = (
        assessed.now.model_dump(mode="json")
    )
    collection = RecommendationCollection.model_validate_json(json.dumps(data))
    sources = {"src_group": "permitted_independent", "src_geo": "geoapify_geography"}
    with pytest.raises(ValueError, match="source binding"):
        validate_publication(collection, source_classes=sources, now=NOW)
    result = validate_publication(
        collection,
        schedules={"branch_a": (OpeningEvidence(schedule, "24/7"),)},
        source_classes=sources,
        now=NOW,
    )
    assert result.unconfirmed_primaries[0].place_id == "branch_a"
    wrong = schedule.model_copy(update={"evidence_ids": ("ev_menu_a",)})
    with pytest.raises(ValueError, match="not the cited"):
        validate_publication(
            collection,
            schedules={"branch_a": (OpeningEvidence(wrong, "24/7"),)},
            source_classes=sources,
            now=NOW,
        )


def test_comparative_preference_keeps_both_branch_scoped_claims():
    from app.publication_validation import ComparisonAssertion

    data = fixture()
    first = ClaimAssertion("branch_a", "menu", "menu_a", ("ev_menu_a",))
    second = ClaimAssertion("branch_b", "menu", "menu_b", ("ev_menu_b",))
    comparison = ComparisonAssertion("branch_a", "branch_b", first, second)
    collection = RecommendationCollection.model_validate_json(json.dumps(data))
    result = validate_publication(
        collection,
        comparisons=(comparison,),
        source_classes={"src_group": "permitted_independent"},
        now=NOW,
    )
    assert result.comparisons == (comparison,)
    wrong = ComparisonAssertion("branch_a", "branch_b", second, first)
    with pytest.raises(ValueError, match="comparison"):
        validate_publication(
            collection,
            comparisons=(wrong,),
            source_classes={"src_group": "permitted_independent"},
            now=NOW,
        )
    reversed_preference = ComparisonAssertion("branch_b", "branch_a", second, first)
    with pytest.raises(ValueError, match="comparison"):
        validate_publication(
            collection,
            comparisons=(reversed_preference,),
            source_classes={"src_group": "permitted_independent"},
            now=NOW,
        )
