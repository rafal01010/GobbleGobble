import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from fastapi.openapi.models import OpenAPI
from pydantic import BaseModel, ValidationError, computed_field

from app import schema_export
from app.schemas import lifecycle, operations
from app.schemas.recommendations import (
    FoodRefinementLineage,
    RecommendationCollection,
)
from app.schemas.search import EstimatedJourney, Place, SearchRequest

BACKEND = Path(__file__).resolve().parents[1]
EXAMPLES = BACKEND.parents[1] / "docs/examples"


class ManifestEntry(TypedDict):
    model: str
    accepted: bool
    error_type: NotRequired[str]
    error_message: NotRequired[str | None]


LIFECYCLE_MANIFEST: dict[str, ManifestEntry] = json.loads(
    (EXAMPLES / "lifecycle-contract/manifest.json").read_text()
)
MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        lifecycle.RunSnapshot,
        lifecycle.RunReadResponse,
        lifecycle.RunEvent,
        lifecycle.EventBatch,
        operations.ResolveLocationRequest,
        operations.ResolveLocationResponse,
        operations.StartRunRequest,
        operations.ReadRunRequest,
        operations.WatchRunRequest,
        operations.ChooseFoodRequest,
        operations.RetryRunRequest,
        operations.CancelRunRequest,
        operations.CommandResponse,
        operations.CancelRunResponse,
        operations.ErrorResponse,
    )
}
ACCEPTED_RECOMMENDATIONS = (
    "accepted_full_set",
    "accepted_two_runners",
    "accepted_smaller_set",
    "accepted_broader_pool_inference",
    "accepted_shared_multiplace",
    "accepted_unknown_gap_and_omitted_food",
    "accepted_new_snapshot_refinement",
)
REJECTED_RECOMMENDATIONS = (
    ("rejected_four_primaries", "too_long", None),
    ("rejected_five_runners", "too_long", None),
    (
        "rejected_duplicate_primary",
        "value_error",
        "recommendation groups must have unique disjoint places",
    ),
    (
        "rejected_duplicate_runner",
        "value_error",
        "recommendation groups must have unique disjoint places",
    ),
    (
        "rejected_group_overlap",
        "value_error",
        "recommendation groups must have unique disjoint places",
    ),
    (
        "rejected_unknown_candidate",
        "value_error",
        "place is not a researched candidate",
    ),
    (
        "rejected_discovery_candidate",
        "value_error",
        "place is not a researched candidate",
    ),
    (
        "rejected_missing_shortfall",
        "value_error",
        "smaller recommendation sets require a shortfall reason",
    ),
    ("rejected_unknown_food_claim", "value_error", "unknown food claim reference"),
    (
        "rejected_wrong_branch_food_support",
        "value_error",
        "food claim belongs to another place",
    ),
    (
        "rejected_unknown_only_food_support",
        "value_error",
        "food choices require asserted food claims",
    ),
    (
        "rejected_price_only_food_support",
        "value_error",
        "food choices require food or menu claims",
    ),
    (
        "rejected_choice_version_mismatch",
        "value_error",
        "food choice differs from offering version",
    ),
    (
        "rejected_stale_choice_repeated_local_id",
        "value_error",
        "selected food choice differs from parent version",
    ),
    ("rejected_missing_parent", "value_error", "unknown parent version"),
    ("rejected_missing_snapshot", "value_error", "unknown research snapshot"),
    ("rejected_self_parent", "value_error", "version cannot be its own parent"),
    ("rejected_cyclic_lineage", "value_error", "version lineage contains a cycle"),
)


REJECTED_LIFECYCLE = (
    ("rejected_queued_attempt", "value_error", "queued state has no current attempt"),
    (
        "rejected_running_without_attempt",
        "value_error",
        "running or completed state requires an attempt",
    ),
    (
        "rejected_completed_without_attempt",
        "value_error",
        "running or completed state requires an attempt",
    ),
    ("rejected_completed_without_outcome", "missing", "Field required"),
    ("rejected_queued_outcome", "extra_forbidden", "Extra inputs are not permitted"),
    ("rejected_cancelled_outcome", "extra_forbidden", "Extra inputs are not permitted"),
    (
        "rejected_ready_without_display",
        "value_error",
        "successful outcome must match the display version",
    ),
    (
        "rejected_missing_display_version",
        "value_error",
        "display version is absent from supplied recommendations",
    ),
    (
        "rejected_empty_ready_version",
        "value_error",
        "display version requires nonempty recommendations",
    ),
    (
        "rejected_other_search_result",
        "value_error",
        "recommendation collection belongs to another search",
    ),
    (
        "rejected_success_version_mismatch",
        "value_error",
        "successful outcome must match the display version",
    ),
    ("rejected_partial_without_limitations", "too_short", None),
    (
        "rejected_no_matches_with_new_version",
        "extra_forbidden",
        "Extra inputs are not permitted",
    ),
    (
        "rejected_insufficient_coverage_with_new_version",
        "extra_forbidden",
        "Extra inputs are not permitted",
    ),
    (
        "rejected_technical_failure_with_new_version",
        "extra_forbidden",
        "Extra inputs are not permitted",
    ),
    ("rejected_empty_coverage_gaps", "too_short", None),
    ("rejected_unbounded_failure_code", "literal_error", None),
    ("rejected_no_matches_without_scope", "string_too_short", None),
    ("rejected_event_sequence_zero", "greater_than_equal", None),
    ("rejected_event_sequence_boolean", "int_type", None),
    ("rejected_boolean_schema_version", "int_type", None),
    (
        "rejected_progress_result_version",
        "value_error",
        "event result version differs from its published outcome",
    ),
    (
        "rejected_completed_event_version_mismatch",
        "value_error",
        "event result version differs from its published outcome",
    ),
    (
        "rejected_failure_event_result_version",
        "value_error",
        "event result version differs from its published outcome",
    ),
    (
        "rejected_progress_without_attempt",
        "value_error",
        "progress or completed event requires an attempt",
    ),
    (
        "rejected_queued_event_attempt",
        "value_error",
        "queued event has no current attempt",
    ),
    ("rejected_event_missing_explicit_version", "missing", "Field required"),
    ("rejected_event_naive_time", "timezone_aware", None),
    (
        "rejected_event_provider_body",
        "extra_forbidden",
        "Extra inputs are not permitted",
    ),
    (
        "rejected_batch_other_search",
        "value_error",
        "event batch mixes search or run identities",
    ),
    (
        "rejected_batch_other_run",
        "value_error",
        "event batch mixes search or run identities",
    ),
    (
        "rejected_batch_duplicate",
        "value_error",
        "event sequences must be strictly increasing",
    ),
    (
        "rejected_batch_decreasing",
        "value_error",
        "event sequences must be strictly increasing",
    ),
    ("rejected_watch_negative", "greater_than_equal", None),
    ("rejected_watch_boolean", "int_type", None),
    ("rejected_invalid_current_coordinates", "less_than_equal", None),
    ("rejected_unresolved_start", "missing", "Field required"),
    ("rejected_choice_without_offering_version", "missing", "Field required"),
    ("rejected_choice_snapshot_as_version", "string_pattern_mismatch", None),
    (
        "rejected_freeform_food_choice",
        "extra_forbidden",
        "Extra inputs are not permitted",
    ),
    (
        "rejected_first_admission_completed",
        "value_error",
        "first command admission requires queued state",
    ),
    (
        "rejected_cancel_claims_completion",
        "value_error",
        "cancel disposition contradicts current run state",
    ),
    (
        "rejected_error_provider_body",
        "extra_forbidden",
        "Extra inputs are not permitted",
    ),
)


@pytest.mark.parametrize(
    ("name", "origin", "mode", "minutes"),
    [
        ("request-current-default-walking", "current", "walk", 15),
        ("request-chosen-adjustable-walking", "chosen", "walk", 22),
        ("request-chosen-fixed-driving", "chosen", "drive", 30),
    ],
)
def test_search_request_examples_preserve_origin_and_travel(
    name: str, origin: str, mode: str, minutes: int
) -> None:
    request = SearchRequest.model_validate_json(
        (EXAMPLES / "search-contract" / f"{name}.json").read_text()
    )
    assert (request.origin.kind, request.travel.mode, request.travel.minutes) == (
        origin,
        mode,
        minutes,
    )
    assert SearchRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize(
    ("name", "kind", "journey"),
    [
        ("place-branch-unknown", "branch", "unknown"),
        ("place-stall-unknown", "stall", "unknown"),
        ("place-estimated-assessed", "branch", "estimated"),
    ],
)
def test_place_examples_preserve_unknowns_and_branch_identity(
    name: str, kind: str, journey: str
) -> None:
    place = Place.model_validate_json(
        (EXAMPLES / "search-contract" / f"{name}.json").read_text()
    )
    assert (place.kind, place.journey.status) == (kind, journey)
    assert Place.model_validate_json(place.model_dump_json()) == place
    if name == "place-branch-unknown":
        assert [ref.model_dump() for ref in place.location.provider_refs] == [
            {"provider": "example-geography", "provider_id": "example-branch-01"},
            {"provider": "example-place-directory", "provider_id": "example-branch-a"},
        ]
        assert place.branch_label == "Illustrative east branch"
        assert place.opening.status == "unknown"
    elif name == "place-stall-unknown":
        assert place.parent_place_id == "example-place-food-court-01"
    else:
        assert isinstance(place.journey, EstimatedJourney)
        assert place.journey.duration_seconds == 720.0
        assert place.opening.model_dump(mode="json")["meal_service"] == "unknown"


@pytest.mark.parametrize(
    ("name", "model", "code"),
    [
        ("invalid-request-coordinates", SearchRequest, "less_than_equal"),
        ("invalid-request-timezone", SearchRequest, "value_error"),
        ("invalid-request-driving", SearchRequest, "int_type"),
        ("invalid-place-unknown-journey-duration", Place, "extra_forbidden"),
    ],
)
def test_invalid_search_examples_are_rejected(
    name: str, model: type[BaseModel], code: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        model.model_validate_json(
            (EXAMPLES / "search-contract" / f"{name}.json").read_text()
        )
    assert code in [error["type"] for error in raised.value.errors(include_input=False)]


@pytest.mark.parametrize("name", ACCEPTED_RECOMMENDATIONS)
def test_accepted_recommendation_examples_roundtrip(name: str) -> None:
    collection = RecommendationCollection.model_validate_json(
        (EXAMPLES / "recommendation-contract" / f"{name}.json").read_text()
    )
    assert collection.search_id == "search_example"
    assert (
        RecommendationCollection.model_validate_json(collection.model_dump_json())
        == collection
    )


@pytest.mark.parametrize(("name", "code", "message"), REJECTED_RECOMMENDATIONS)
def test_invalid_recommendation_examples_are_rejected(
    name: str, code: str, message: str | None
) -> None:
    with pytest.raises(ValidationError) as raised:
        RecommendationCollection.model_validate_json(
            (EXAMPLES / "recommendation-contract" / f"{name}.json").read_text()
        )
    assert any(
        error["type"] == code and (message is None or message in error["msg"])
        for error in raised.value.errors(include_input=False)
    )


def test_full_recommendation_groups_and_food_choices_have_distinct_scopes() -> None:
    collection = RecommendationCollection.model_validate_json(
        (EXAMPLES / "recommendation-contract/accepted_full_set.json").read_text()
    )
    version = collection.versions[0]
    assert [item.place_id for item in version.primaries] == [
        "branch_a",
        "branch_b",
        "branch_c",
    ]
    assert [item.place_id for item in version.runners_up] == [
        "branch_d",
        "branch_e",
        "branch_f",
        "branch_g",
    ]
    assert version.food_choices[0].supports[0].place_id == "branch_h"
    assert collection.snapshots[0].discovery_leads[0].place_id == "branch_lead"
    assert (collection.request.travel.mode, collection.request.travel.minutes) == (
        "walk",
        15,
    )


def test_smaller_recommendation_set_requires_and_preserves_its_reason() -> None:
    small = RecommendationCollection.model_validate_json(
        (EXAMPLES / "recommendation-contract/accepted_smaller_set.json").read_text()
    ).versions[0]
    assert len(small.primaries) == 1
    assert small.runners_up == ()
    assert (
        small.shortfall_reason
        == "Only one supported option was found in this synthetic research."
    )
    sufficient = RecommendationCollection.model_validate_json(
        (EXAMPLES / "recommendation-contract/accepted_two_runners.json").read_text()
    ).versions[0]
    assert len(sufficient.runners_up) == 2
    assert sufficient.shortfall_reason is None


def test_food_choice_preserves_inference_and_explicit_multiplace_support() -> None:
    inferred = RecommendationCollection.model_validate_json(
        (
            EXAMPLES / "recommendation-contract/accepted_broader_pool_inference.json"
        ).read_text()
    )
    assert inferred.versions[0].food_choices[0].supports[0].food_claim_ids == (
        "inferred_h",
    )
    claim = next(
        claim
        for claim in inferred.snapshots[0].evidence.food_claims
        if claim.claim_id == "inferred_h"
    )
    assert claim.model_dump(mode="json")["status"] == "inferred"
    assert (
        claim.model_dump(mode="json")["basis"]
        == "The branch-specific menu observation is the stated inference basis."
    )
    shared = (
        RecommendationCollection.model_validate_json(
            (
                EXAMPLES / "recommendation-contract/accepted_shared_multiplace.json"
            ).read_text()
        )
        .versions[0]
        .food_choices[0]
    )
    assert [
        (support.place_id, support.food_claim_ids) for support in shared.supports
    ] == [("branch_a", ("shared_noodles",)), ("branch_b", ("shared_noodles",))]


def test_recommendations_allow_unknown_food_and_omitted_food_without_invented_claims() -> (
    None
):
    primaries = (
        RecommendationCollection.model_validate_json(
            (
                EXAMPLES
                / "recommendation-contract/accepted_unknown_gap_and_omitted_food.json"
            ).read_text()
        )
        .versions[0]
        .primaries
    )
    assert primaries[0].food_claim_ids == ("unknown_a",)
    assert primaries[1].food_claim_ids == ()


def test_refinement_uses_the_offering_version_when_local_choice_ids_repeat() -> None:
    chain = RecommendationCollection.model_validate_json(
        (
            EXAMPLES / "recommendation-contract/accepted_new_snapshot_refinement.json"
        ).read_text()
    )
    assert [version.version_id for version in chain.versions] == [
        "ver_all",
        "ver_refined",
        "ver_initial",
    ]
    assert [snapshot.snapshot_id for snapshot in chain.snapshots] == [
        "snap_initial",
        "snap_refined",
    ]
    assert chain.versions[0].lineage.kind == "all_options"
    refinement = chain.versions[1]
    assert isinstance(refinement.lineage, FoodRefinementLineage)
    assert refinement.lineage.choice.model_dump() == {
        "version_id": "ver_initial",
        "choice_id": "noodles",
    }
    assert refinement.research_snapshot_id == "snap_refined"
    assert [version.food_choices[0].choice_id for version in chain.versions] == [
        "noodles",
        "noodles",
        "noodles",
    ]
    stale = chain.model_dump(mode="json")
    stale["versions"][0]["lineage"] = {
        "kind": "food_choice",
        "parent_version_id": "ver_refined",
        "choice": {"version_id": "ver_initial", "choice_id": "noodles"},
    }
    with pytest.raises(
        ValidationError, match="selected food choice differs from parent version"
    ):
        RecommendationCollection.model_validate_json(json.dumps(stale))


def test_example_catalogs_and_manifest_models_are_complete() -> None:
    assert {path.stem for path in (EXAMPLES / "search-contract").glob("*.json")} == {
        "request-current-default-walking",
        "request-chosen-adjustable-walking",
        "request-chosen-fixed-driving",
        "place-branch-unknown",
        "place-stall-unknown",
        "place-estimated-assessed",
        "invalid-request-coordinates",
        "invalid-request-timezone",
        "invalid-request-driving",
        "invalid-place-unknown-journey-duration",
    }
    assert {path.stem for path in (EXAMPLES / "lifecycle-contract").glob("*.json")} == {
        *LIFECYCLE_MANIFEST,
        "manifest",
    }
    assert {entry["model"] for entry in LIFECYCLE_MANIFEST.values()} == set(MODELS)
    assert {
        path.stem for path in (EXAMPLES / "recommendation-contract").glob("*.json")
    } == set(ACCEPTED_RECOMMENDATIONS) | {case[0] for case in REJECTED_RECOMMENDATIONS}


@pytest.mark.parametrize(
    "name", [name for name, entry in LIFECYCLE_MANIFEST.items() if entry["accepted"]]
)
def test_accepted_lifecycle_manifest_examples_roundtrip(name: str) -> None:
    model = MODELS[LIFECYCLE_MANIFEST[name]["model"]]
    value = model.model_validate_json(
        (EXAMPLES / "lifecycle-contract" / f"{name}.json").read_text()
    )
    assert model.model_validate_json(value.model_dump_json()) == value


def test_lifecycle_results_preserve_prior_display_and_qualified_outcomes() -> None:
    queued = lifecycle.RunReadResponse.model_validate_json(
        (
            EXAMPLES / "lifecycle-contract/accepted_queued_without_result.json"
        ).read_text()
    )
    assert queued.run.state.status == "queued"
    assert queued.recommendations is None
    assert queued.display_version_id is None
    partial = lifecycle.RunReadResponse.model_validate_json(
        (EXAMPLES / "lifecycle-contract/accepted_outcome_partial.json").read_text()
    )
    assert partial.run.state.model_dump() == {
        "status": "completed",
        "outcome": {
            "kind": "partial",
            "version_id": "ver_initial",
            "limitations": ("Route coverage remains incomplete for other candidates.",),
        },
    }
    for name in (
        "accepted_outcome_ready",
        "accepted_cancelled_with_prior_result",
        "accepted_outcome_technical_failure",
    ):
        response = lifecycle.RunReadResponse.model_validate_json(
            (EXAMPLES / "lifecycle-contract" / f"{name}.json").read_text()
        )
        assert response.display_version_id == "ver_initial"
    failed = lifecycle.RunReadResponse.model_validate_json(
        (
            EXAMPLES / "lifecycle-contract/accepted_outcome_technical_failure.json"
        ).read_text()
    )
    assert isinstance(failed.run.state, lifecycle.CompletedState)
    assert failed.run.state.outcome.kind == "technical_failure"


def test_lifecycle_commands_preserve_choice_identity_retry_purpose_and_current_state() -> (
    None
):
    choice = operations.ChooseFoodRequest.model_validate_json(
        (EXAMPLES / "lifecycle-contract/accepted_choose_offered_pair.json").read_text()
    )
    assert isinstance(choice.selection, operations.FoodChoiceSelection)
    assert choice.selection.choice.model_dump() == {
        "version_id": "ver_initial",
        "choice_id": "noodles",
    }
    retry = operations.CommandResponse.model_validate_json(
        (
            EXAMPLES / "lifecycle-contract/accepted_retry_preserves_purpose.json"
        ).read_text()
    )
    assert retry.command.run.operation == "food_choice"
    assert retry.command.run.last_event_sequence == 8
    replay = operations.CommandResponse.model_validate_json(
        (
            EXAMPLES / "lifecycle-contract/accepted_completed_command_replay.json"
        ).read_text()
    )
    assert replay.command.disposition == "replayed"
    assert replay.command.run.state.status == "completed"
    for name, expected in (
        ("accepted_cancel_requested", "cancelling"),
        ("accepted_cancel_already_completed", "completed"),
    ):
        response = operations.CancelRunResponse.model_validate_json(
            (EXAMPLES / "lifecycle-contract" / f"{name}.json").read_text()
        )
        assert response.run.state.status == expected
    watch = operations.WatchRunRequest.model_validate_json(
        (EXAMPLES / "lifecycle-contract/accepted_watch_from_zero.json").read_text()
    )
    assert watch.after_sequence == 0


def test_event_examples_allow_sequence_gaps_and_attempt_changes_without_publishing_progress() -> (
    None
):
    batch = lifecycle.EventBatch.model_validate_json(
        (
            EXAMPLES / "lifecycle-contract/accepted_event_batch_gaps_and_retry.json"
        ).read_text()
    )
    assert [
        (event.sequence, event.attempt_id, event.result_version_id)
        for event in batch.events
    ] == [(3, "attempt_1", None), (8, "attempt_2", None)]
    queued = lifecycle.RunEvent.model_validate_json(
        (EXAMPLES / "lifecycle-contract/accepted_event_queued.json").read_text()
    )
    assert queued.payload.kind == "queued"
    assert queued.result_version_id is None
    partial = lifecycle.RunEvent.model_validate_json(
        (EXAMPLES / "lifecycle-contract/accepted_event_partial.json").read_text()
    )
    assert partial.payload.kind == "completed"
    assert partial.result_version_id == "ver_initial"


def _references(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            yield reference
        discriminator = value.get("discriminator")
        if isinstance(discriminator, dict):
            yield from discriminator.get("mapping", {}).values()
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def test_openapi_export_contains_live_routes_and_resolved_domain_references() -> None:
    document = schema_export.build_openapi_document()
    OpenAPI.model_validate(document)
    assert document["openapi"] == "3.1.0"
    assert document["info"] == {
        "title": "GobbleGobble backend contract",
        "version": "0.1.0",
    }
    paths = document["paths"]
    assert set(paths) == {
        "/ready",
        "/api/session",
        "/api/runs",
        "/api/searches/{search_id}/runs/{run_id}",
        "/api/searches/{search_id}/runs/{run_id}/events",
    }
    assert paths["/api/runs"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/StartRunRequest"}
    assert paths["/api/runs"]["post"]["responses"]["202"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/CommandResponse"}
    assert paths["/api/searches/{search_id}/runs/{run_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"] == {"$ref": "#/components/schemas/RunReadResponse"}
    assert paths["/api/searches/{search_id}/runs/{run_id}/events"]["get"]["responses"][
        "200"
    ]["content"]["text/event-stream"]["schema"] == {
        "$ref": "#/components/schemas/RunEvent"
    }
    assert paths["/api/runs"]["post"]["responses"]["422"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
    for reference in _references(document):
        assert reference.startswith("#/components/schemas/")
        target: object = document
        for token in reference[2:].split("/"):
            assert isinstance(target, dict)
            target = target[token.replace("~1", "/").replace("~0", "~")]
        assert isinstance(target, dict)
    schemas = document["components"]["schemas"]
    assert {
        "ResolveLocationRequest",
        "ResolveLocationResponse",
        "StartRunRequest",
        "ReadRunRequest",
        "WatchRunRequest",
        "ChooseFoodRequest",
        "RetryRunRequest",
        "CancelRunRequest",
        "CommandResponse",
        "CancelRunResponse",
        "ErrorResponse",
        "RunReadResponse",
        "SearchRequest",
        "Place",
        "Evidence",
        "EvidenceCollection",
        "ReviewAssessment",
        "ResearchSnapshot",
        "FoodChoice",
        "RecommendationVersion",
        "RecommendationCollection",
        "RunEvent",
        "EventBatch",
        "FoodClaim",
        "RunOutcome",
        "RunState",
        "EventPayload",
    } <= schemas.keys()
    assert schemas["DrivingConstraint"]["properties"]["minutes"]["minimum"] == 30
    assert schemas["DrivingConstraint"]["properties"]["minutes"]["maximum"] == 30
    assert schemas["WalkingConstraint"]["properties"]["minutes"]["default"] == 15
    assert schemas["RecommendationVersion"]["properties"]["primaries"]["maxItems"] == 3
    assert schemas["RecommendationVersion"]["properties"]["runners_up"]["maxItems"] == 4
    assert schemas["EventSequence"]["minimum"] == 1
    assert schemas["EventCursor"]["minimum"] == 0
    wire_version = schemas["RunEvent"]["properties"]["schema_version"]
    assert wire_version["minimum"] == wire_version["maximum"] == 1
    assert set(schemas["RunOutcome"]["discriminator"]["mapping"]) == {
        "ready",
        "partial",
        "no_matches",
        "insufficient_coverage",
        "technical_failure",
    }
    assert set(schemas["FoodClaim"]["discriminator"]["mapping"]) == {
        "sourced",
        "inferred",
        "unknown",
    }
    for name in ("RetryRunRequest", "CancelRunRequest"):
        assert "expected_attempt_id" in schemas[name]["required"]
        assert {"type": "null"} in schemas[name]["properties"]["expected_attempt_id"][
            "anyOf"
        ]


def test_openapi_cli_is_deterministic_without_configuration_database_or_network(
    tmp_path: Path,
) -> None:
    environment = {
        "PYTHONPATH": str(BACKEND),
        "GG_DATABASE_PATH": ":memory:",
        "GG_TRUSTED_FRONTEND_ORIGINS": "invalid-json",
    }
    command = [sys.executable, "-m", "app.schema_export"]
    first = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    second = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    assert first.stdout.endswith("\n") and not first.stdout.endswith("\n\n")
    assert json.loads(first.stdout)["info"]["title"] == "GobbleGobble backend contract"
    guarded_program = """
from unittest.mock import patch
from app.core import settings
with (
    patch('sqlite3.connect', side_effect=AssertionError('database accessed')),
    patch('socket.socket.connect', side_effect=AssertionError('network accessed')),
    patch('socket.create_connection', side_effect=AssertionError('network accessed')),
    patch.object(settings, 'load_api_settings', side_effect=AssertionError('environment loaded')),
    patch.object(settings, 'load_database_settings', side_effect=AssertionError('environment loaded')),
):
    from app.schema_export import main
    main()
"""
    guarded = subprocess.run(
        [sys.executable, "-c", guarded_program],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert guarded.stdout == first.stdout
    assert guarded.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_openapi_export_preserves_validation_and_serialization_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ModeProbe(BaseModel):
        value: int

        @computed_field
        def doubled(self) -> int:
            return self.value * 2

    monkeypatch.setattr(
        schema_export, "_ROOT_MODELS", schema_export._ROOT_MODELS + (ModeProbe,)
    )
    schemas = schema_export.build_openapi_document()["components"]["schemas"]
    assert "doubled" not in schemas["ModeProbe-Input"]["properties"]
    assert schemas["ModeProbe-Output"]["properties"]["doubled"]["readOnly"] is True
    assert "doubled" in schemas["ModeProbe-Output"]["required"]


def test_openapi_cli_help_and_unknown_arguments(tmp_path: Path) -> None:
    command = [sys.executable, "-m", "app.schema_export"]
    environment = {"PYTHONPATH": str(BACKEND)}
    help_result = subprocess.run(
        [*command, "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert help_result.returncode == 0
    assert "stdout" in help_result.stdout
    assert "--help" in help_result.stdout
    rejected = subprocess.run(
        [*command, "--output", "unused.json"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert rejected.returncode == 2
    assert "unrecognized arguments" in rejected.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("name", "code", "message"), REJECTED_LIFECYCLE)
def test_invalid_lifecycle_manifest_examples_are_rejected(
    name: str, code: str, message: str | None
) -> None:
    model = MODELS[LIFECYCLE_MANIFEST[name]["model"]]
    with pytest.raises(ValidationError) as raised:
        model.model_validate_json(
            (EXAMPLES / "lifecycle-contract" / f"{name}.json").read_text()
        )
    assert any(
        error["type"] == code and (message is None or message in error["msg"])
        for error in raised.value.errors(include_input=False)
    )


def test_lifecycle_manifest_rejections_match_the_explicit_expected_cases() -> None:
    assert {
        name for name, entry in LIFECYCLE_MANIFEST.items() if not entry["accepted"]
    } == {case[0] for case in REJECTED_LIFECYCLE}
