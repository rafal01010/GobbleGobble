import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NOW = "2026-10-25T04:00:00Z"


def origin():
    return {
        "kind": "chosen",
        "location": {
            "coordinates": {"latitude": 14.6, "longitude": 121.0},
            "timezone": "Asia/Manila",
            "name": "Synthetic origin",
        },
    }


def request():
    return {
        "origin": origin(),
        "travel": {"mode": "walk", "minutes": 15},
        "eat_now_at": NOW,
    }


def collection():
    return {
        "search_id": "search_example",
        "request": request(),
        "snapshots": [
            {
                "snapshot_id": "snap_initial",
                "researched_candidates": [
                    {
                        "place_id": "branch_a",
                        "kind": "branch",
                        "name": "Synthetic branch",
                        "location": origin()["location"],
                    }
                ],
                "evidence": {"place_ids": ["branch_a"]},
            }
        ],
        "versions": [
            {
                "version_id": "ver_initial",
                "research_snapshot_id": "snap_initial",
                "lineage": {"kind": "initial"},
                "primaries": [
                    {
                        "place_id": "branch_a",
                        "why_to_go": "A synthetic researched option for contract checks.",
                    }
                ],
                "runners_up": [],
                "shortfall_reason": "Only one option in this synthetic assessed scope.",
            }
        ],
    }


def outcomes():
    return {
        "ready": {"kind": "ready", "version_id": "ver_initial"},
        "partial": {
            "kind": "partial",
            "version_id": "ver_initial",
            "limitations": ["Route coverage remains incomplete for other candidates."],
        },
        "no_matches": {
            "kind": "no_matches",
            "assessed_scope": "The synthetic walking area at the requested instant.",
            "explanation": "The assessed eligible places did not meet this request.",
        },
        "insufficient_coverage": {
            "kind": "insufficient_coverage",
            "gaps": [
                "Coverage cannot establish eligibility for the discovered places."
            ],
        },
        "technical_failure": {
            "kind": "technical_failure",
            "code": "provider_unavailable",
            "message": "Research could not finish because a service was unavailable.",
            "retryable": True,
        },
    }


def snapshot(status="queued", outcome=None, attempt="attempt_1"):
    state = {"status": status}
    if status == "running":
        state["stage"] = "researching"
    if outcome is not None:
        state["outcome"] = deepcopy(outcome)
    return {
        "search_id": "search_example",
        "run_id": "run_example",
        "operation": "initial",
        "attempt_id": None if status == "queued" else attempt,
        "state": state,
        "last_event_sequence": 0 if status == "queued" else 5,
    }


def read(status="completed", outcome=None, prior=True):
    return {
        "run": snapshot(status, outcome),
        "recommendations": collection() if prior else None,
        "display_version_id": "ver_initial" if prior else None,
    }


def event(kind="progress", outcome=None, sequence=2, attempt="attempt_1"):
    payload = {"kind": kind}
    if kind == "progress":
        payload["stage"] = "researching"
    if outcome is not None:
        payload["outcome"] = deepcopy(outcome)
    return {
        "schema_version": 1,
        "search_id": "search_example",
        "run_id": "run_example",
        "attempt_id": None if kind == "queued" else attempt,
        "sequence": sequence,
        "occurred_at": NOW,
        "result_version_id": (
            outcome["version_id"]
            if outcome is not None and outcome["kind"] in ("ready", "partial")
            else None
        ),
        "payload": payload,
    }


def generate():
    examples = {}
    manifest = {}

    def add(name, model, payload, error=None):
        examples[name] = deepcopy(payload)
        manifest[name] = {"model": model, "accepted": error is None}
        if error is not None:
            manifest[name]["error_type"], manifest[name]["error_message"] = error
        return examples[name]

    def reject(name, model, payload, path, value, error):
        changed = deepcopy(payload)
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return add(name, model, changed, error)

    refs = {"search_id": "search_example", "run_id": "run_example"}
    current = {
        "location": {
            "kind": "current",
            "coordinates": {"latitude": 14.6, "longitude": 121.0},
        }
    }
    add("accepted_resolve_current", "ResolveLocationRequest", current)
    add(
        "accepted_resolve_chosen",
        "ResolveLocationRequest",
        {"location": {"kind": "chosen", "query": "Synthetic district"}},
    )
    add(
        "accepted_location_resolved",
        "ResolveLocationResponse",
        {"outcome": {"status": "resolved", "origin": origin()}},
    )
    add(
        "accepted_location_no_match",
        "ResolveLocationResponse",
        {"outcome": {"status": "no_match", "message": "No location matched."}},
    )
    add(
        "accepted_location_unavailable",
        "ResolveLocationResponse",
        {
            "outcome": {
                "status": "provider_unavailable",
                "message": "Location lookup is temporarily unavailable.",
                "retryable": True,
            }
        },
    )
    start = {"command_id": "command_start", "request": request()}
    add("accepted_start", "StartRunRequest", start)
    add("accepted_read_reference", "ReadRunRequest", refs)
    watch = {**refs, "after_sequence": 0}
    add("accepted_watch_from_zero", "WatchRunRequest", watch)
    add(
        "accepted_watch_after_sequence",
        "WatchRunRequest",
        {**watch, "after_sequence": 7},
    )
    choose = {
        **refs,
        "command_id": "command_choose",
        "selection": {
            "kind": "food_choice",
            "choice": {"version_id": "ver_initial", "choice_id": "noodles"},
        },
    }
    add("accepted_choose_offered_pair", "ChooseFoodRequest", choose)
    add(
        "accepted_choose_all_options",
        "ChooseFoodRequest",
        {
            **refs,
            "command_id": "command_all",
            "selection": {"kind": "all_options", "parent_version_id": "ver_initial"},
        },
    )
    for model, action in (("RetryRunRequest", "retry"), ("CancelRunRequest", "cancel")):
        for label, attempt in (("claimed", "attempt_1"), ("unclaimed", None)):
            add(
                f"accepted_{action}_{label}",
                model,
                {
                    **refs,
                    "command_id": "command_" + action,
                    "expected_attempt_id": attempt,
                },
            )
    accepted = {
        "command": {
            "disposition": "accepted",
            "command_id": "command_start",
            "run": snapshot(),
        }
    }
    add("accepted_queued_admission", "CommandResponse", accepted)
    retry = deepcopy(accepted)
    retry["command"]["run"].update(operation="food_choice", last_event_sequence=8)
    add("accepted_retry_preserves_purpose", "CommandResponse", retry)
    replay = {
        "command": {
            "disposition": "replayed",
            "command_id": "command_start",
            "run": snapshot("completed", outcomes()["ready"]),
        }
    }
    add("accepted_completed_command_replay", "CommandResponse", replay)
    for disposition, status in (
        ("requested", "cancelling"),
        ("already_cancelling", "cancelling"),
        ("already_cancelled", "cancelled"),
        ("already_completed", "completed"),
    ):
        add(
            "accepted_cancel_" + disposition,
            "CancelRunResponse",
            {
                "command_id": "command_cancel",
                "disposition": disposition,
                "run": snapshot(
                    status, outcomes()["ready"] if status == "completed" else None
                ),
            },
        )
    error = {
        "error": {
            "code": "command_conflict",
            "message": "The command identity was already used with different input.",
            "retryable": False,
        }
    }
    add("accepted_command_error", "ErrorResponse", error)
    add(
        "accepted_queued_without_result", "RunReadResponse", read("queued", prior=False)
    )
    add(
        "accepted_running_without_result",
        "RunReadResponse",
        read("running", prior=False),
    )
    add("accepted_cancelling_with_prior_result", "RunReadResponse", read("cancelling"))
    add("accepted_cancelled_with_prior_result", "RunReadResponse", read("cancelled"))
    for label, outcome in outcomes().items():
        add(
            "accepted_outcome_" + label,
            "RunReadResponse",
            read(
                outcome=outcome,
                prior=label in ("ready", "partial", "technical_failure"),
            ),
        )
    for kind in ("queued", "progress", "cancelling", "cancelled"):
        add("accepted_event_" + kind, "RunEvent", event(kind))
    add(
        "accepted_event_queued_cancellation",
        "RunEvent",
        event("cancelling", attempt=None),
    )
    for label, outcome in outcomes().items():
        add("accepted_event_" + label, "RunEvent", event("completed", outcome))
    batch = {
        **refs,
        "events": [
            event("progress", sequence=3),
            event("progress", sequence=8, attempt="attempt_2"),
        ],
    }
    add("accepted_event_batch_gaps_and_retry", "EventBatch", batch)
    add("accepted_empty_event_batch", "EventBatch", {**refs, "events": []})

    value_error = lambda message: ("value_error", message)
    reject(
        "rejected_queued_attempt",
        "RunSnapshot",
        snapshot(),
        ["attempt_id"],
        "attempt_1",
        value_error("queued state has no current attempt"),
    )
    reject(
        "rejected_running_without_attempt",
        "RunSnapshot",
        snapshot("running"),
        ["attempt_id"],
        None,
        value_error("running or completed state requires an attempt"),
    )
    reject(
        "rejected_completed_without_attempt",
        "RunSnapshot",
        snapshot("completed", outcomes()["ready"]),
        ["attempt_id"],
        None,
        value_error("running or completed state requires an attempt"),
    )
    add(
        "rejected_completed_without_outcome",
        "RunSnapshot",
        snapshot("completed"),
        ("missing", "Field required"),
    )
    reject(
        "rejected_queued_outcome",
        "RunSnapshot",
        snapshot(),
        ["state", "outcome"],
        outcomes()["ready"],
        ("extra_forbidden", "Extra inputs are not permitted"),
    )
    reject(
        "rejected_cancelled_outcome",
        "RunSnapshot",
        snapshot("cancelled"),
        ["state", "outcome"],
        outcomes()["ready"],
        ("extra_forbidden", "Extra inputs are not permitted"),
    )
    ready_read = read(outcome=outcomes()["ready"])
    reject(
        "rejected_ready_without_display",
        "RunReadResponse",
        ready_read,
        ["display_version_id"],
        None,
        value_error("successful outcome must match the display version"),
    )
    reject(
        "rejected_missing_display_version",
        "RunReadResponse",
        ready_read,
        ["display_version_id"],
        "ver_missing",
        value_error("display version is absent from supplied recommendations"),
    )
    reject(
        "rejected_empty_ready_version",
        "RunReadResponse",
        ready_read,
        ["recommendations", "versions", 0, "primaries"],
        [],
        value_error("display version requires nonempty recommendations"),
    )
    reject(
        "rejected_other_search_result",
        "RunReadResponse",
        ready_read,
        ["recommendations", "search_id"],
        "search_other",
        value_error("recommendation collection belongs to another search"),
    )
    reject(
        "rejected_success_version_mismatch",
        "RunReadResponse",
        ready_read,
        ["run", "state", "outcome", "version_id"],
        "ver_other",
        value_error("successful outcome must match the display version"),
    )
    reject(
        "rejected_partial_without_limitations",
        "RunReadResponse",
        read(outcome=outcomes()["partial"]),
        ["run", "state", "outcome", "limitations"],
        [],
        ("too_short", None),
    )
    for kind in ("no_matches", "insufficient_coverage", "technical_failure"):
        reject(
            "rejected_" + kind + "_with_new_version",
            "RunReadResponse",
            read(outcome=outcomes()[kind], prior=False),
            ["run", "state", "outcome", "version_id"],
            "ver_initial",
            ("extra_forbidden", "Extra inputs are not permitted"),
        )
    reject(
        "rejected_empty_coverage_gaps",
        "RunReadResponse",
        read(outcome=outcomes()["insufficient_coverage"], prior=False),
        ["run", "state", "outcome", "gaps"],
        [],
        ("too_short", None),
    )
    reject(
        "rejected_unbounded_failure_code",
        "RunReadResponse",
        read(outcome=outcomes()["technical_failure"], prior=False),
        ["run", "state", "outcome", "code"],
        "raw_exception_name",
        ("literal_error", None),
    )
    reject(
        "rejected_no_matches_without_scope",
        "RunReadResponse",
        read(outcome=outcomes()["no_matches"], prior=False),
        ["run", "state", "outcome", "assessed_scope"],
        "",
        ("string_too_short", None),
    )
    for label, number in (("zero", 0), ("boolean", True)):
        reject(
            "rejected_event_sequence_" + label,
            "RunEvent",
            event(),
            ["sequence"],
            number,
            ("greater_than_equal", None) if number == 0 else ("int_type", None),
        )
    reject(
        "rejected_boolean_schema_version",
        "RunEvent",
        event(),
        ["schema_version"],
        True,
        ("int_type", None),
    )
    reject(
        "rejected_progress_result_version",
        "RunEvent",
        event(),
        ["result_version_id"],
        "ver_initial",
        value_error("event result version differs from its published outcome"),
    )
    reject(
        "rejected_completed_event_version_mismatch",
        "RunEvent",
        event("completed", outcomes()["ready"]),
        ["result_version_id"],
        "ver_other",
        value_error("event result version differs from its published outcome"),
    )
    reject(
        "rejected_failure_event_result_version",
        "RunEvent",
        event("completed", outcomes()["technical_failure"]),
        ["result_version_id"],
        "ver_initial",
        value_error("event result version differs from its published outcome"),
    )
    reject(
        "rejected_progress_without_attempt",
        "RunEvent",
        event(),
        ["attempt_id"],
        None,
        value_error("progress or completed event requires an attempt"),
    )
    reject(
        "rejected_queued_event_attempt",
        "RunEvent",
        event("queued"),
        ["attempt_id"],
        "attempt_1",
        value_error("queued event has no current attempt"),
    )
    missing_version = event("queued")
    del missing_version["result_version_id"]
    add(
        "rejected_event_missing_explicit_version",
        "RunEvent",
        missing_version,
        ("missing", "Field required"),
    )
    reject(
        "rejected_event_naive_time",
        "RunEvent",
        event(),
        ["occurred_at"],
        "2026-10-25T04:00:00",
        ("timezone_aware", None),
    )
    reject(
        "rejected_event_provider_body",
        "RunEvent",
        event(),
        ["payload", "provider_body"],
        {"synthetic": "raw response"},
        ("extra_forbidden", "Extra inputs are not permitted"),
    )
    reject(
        "rejected_batch_other_search",
        "EventBatch",
        batch,
        ["events", 1, "search_id"],
        "search_other",
        value_error("event batch mixes search or run identities"),
    )
    reject(
        "rejected_batch_other_run",
        "EventBatch",
        batch,
        ["events", 1, "run_id"],
        "run_other",
        value_error("event batch mixes search or run identities"),
    )
    for label, sequence in (("duplicate", 3), ("decreasing", 2)):
        reject(
            "rejected_batch_" + label,
            "EventBatch",
            batch,
            ["events", 1, "sequence"],
            sequence,
            value_error("event sequences must be strictly increasing"),
        )
    for label, cursor in (("negative", -1), ("boolean", True)):
        reject(
            "rejected_watch_" + label,
            "WatchRunRequest",
            watch,
            ["after_sequence"],
            cursor,
            ("greater_than_equal", None) if cursor == -1 else ("int_type", None),
        )
    reject(
        "rejected_invalid_current_coordinates",
        "ResolveLocationRequest",
        current,
        ["location", "coordinates", "latitude"],
        91.0,
        ("less_than_equal", None),
    )
    reject(
        "rejected_unresolved_start",
        "StartRunRequest",
        start,
        ["request", "origin"],
        {"kind": "chosen", "query": "Synthetic district"},
        ("missing", "Field required"),
    )
    missing_choice_version = deepcopy(choose)
    del missing_choice_version["selection"]["choice"]["version_id"]
    add(
        "rejected_choice_without_offering_version",
        "ChooseFoodRequest",
        missing_choice_version,
        ("missing", "Field required"),
    )
    reject(
        "rejected_choice_snapshot_as_version",
        "ChooseFoodRequest",
        choose,
        ["selection", "choice", "version_id"],
        "snap_initial",
        ("string_pattern_mismatch", None),
    )
    reject(
        "rejected_freeform_food_choice",
        "ChooseFoodRequest",
        choose,
        ["selection", "freeform"],
        "Invent a different meal.",
        ("extra_forbidden", "Extra inputs are not permitted"),
    )
    reject(
        "rejected_first_admission_completed",
        "CommandResponse",
        accepted,
        ["command", "run"],
        snapshot("completed", outcomes()["ready"]),
        value_error("first command admission requires queued state"),
    )
    add(
        "rejected_cancel_claims_completion",
        "CancelRunResponse",
        {
            "command_id": "command_cancel",
            "disposition": "already_cancelled",
            "run": snapshot("cancelling"),
        },
        value_error("cancel disposition contradicts current run state"),
    )
    reject(
        "rejected_error_provider_body",
        "ErrorResponse",
        error,
        ["error", "provider_body"],
        "Synthetic raw provider response.",
        ("extra_forbidden", "Extra inputs are not permitted"),
    )
    for name, payload in examples.items():
        (ROOT / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    accepted_count = sum(item["accepted"] for item in manifest.values())
    print(
        f"Wrote {accepted_count} accepted and {len(examples) - accepted_count} rejected synthetic payloads."
    )


if __name__ == "__main__":
    generate()
