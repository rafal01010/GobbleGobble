# Lifecycle, operation and event contract

GG-004.04 defines typed lifecycle envelopes in [lifecycle.py](../apps/backend/app/schemas/lifecycle.py) and operation envelopes in [operations.py](../apps/backend/app/schemas/operations.py). It is implemented, awaiting section verification at GG-007. These schemas describe payloads. HTTP handlers, anonymous access, command admission, storage, worker execution and SSE delivery remain later tasks. GG-004.05 owns the authoritative API document and export interface.

The contract reuses [SearchRequest](SEARCH_CONTRACT.md) and the [recommendation relationship boundary](RECOMMENDATION_CONTRACT.md). The models are strict, frozen, reject extra fields and hide input values in formatted validation errors. These settings do not authorize logging or storing their contents.

## Run state and research outcomes

A run belongs to one search and retains its purpose across retry. Initial research, a food choice and returning to all options are distinct purposes. A retry continues the same purpose and run through another attempt. Attempt identity is absent before worker claim. It is separate from run identity and recommendation version identity.

The state variants distinguish queued, running, cancelling, completed and cancelled work. Completed work carries exactly one research outcome. Cancellation is a lifecycle state outside the research outcomes.

| Outcome | Meaning | Required result information |
|---|---|---|
| `ready` | Useful recommendations are complete for the assessed scope. A justified smaller set can be ready. | A published version reference. |
| `partial` | Useful recommendations exist with material unresolved limitations. | A published version reference and nonempty limitations. |
| `no_matches` | The assessment supports no suitable matches within its stated scope. | An explanation and assessed scope, with no new version. |
| `insufficient_coverage` | Available evidence cannot establish a useful result or a no-match conclusion. | Nonempty coverage gaps, with no new version. |
| `technical_failure` | Admitted execution failed. | A bounded failure code, safe message and retryability, with no new version. |

Missing optional menus or reviews does not automatically select an outcome. A timeout or provider failure cannot establish no matches. The unions reject contradictory fields, but they cannot prove that research supports an outcome's explanation.

The read-response relationship boundary contains a run snapshot, an optional `RecommendationCollection` and `display_version_id`. This version is the result displayed in the target run's context. It is not a declaration of the globally active search version. A failed or cancelled refinement can retain an earlier permitted result without claiming to have published it.

Referenced display versions must exist in the supplied collection and the collection must belong to the same search. A ready or partial outcome must identify that displayed version and have a nonempty displayed recommendation set. The collection supplies the existing candidate, exact-branch evidence and lineage checks. It does not authenticate records loaded from another repository.

## Operation contracts

The following table describes operation intent. It does not register paths or authorize access. Exact route documentation and authoritative export belong to GG-004.05. Credentials and their transport belong to GG-005.02.

| Operation and request model | Input | Success information |
|---|---|---|
| Resolve location, `ResolveLocationRequest` | Current coordinates or a nonblank chosen-location query. | Resolved origin with timezone, an explicit no-location-match result, or provider unavailability. Location lookup failure is distinct from research no matches. |
| Start, `StartRunRequest` | Command identity and the existing resolved `SearchRequest`. | Stable run identity and queued state on first admission. |
| Read, `ReadRunRequest` | Search and run identity. | Run snapshot and supplied display-result relationships. Reading does not admit work. |
| Watch, `WatchRunRequest` | Search and run identity, with a nonnegative sequence cursor. | Typed events strictly after the cursor, followed by newly committed events. Watching does not admit work. |
| Choose, `ChooseFoodRequest` | Command identity, search/run context and the exact offering-version/choice pair, or an explicit all-options parent version. | A queued refinement in the same search. |
| Retry, `RetryRunRequest` | Command identity and the target run/attempt context. | Continuation of the original run purpose. |
| Cancel, `CancelRunRequest` | Command identity and the target run/attempt context. | Explicit cancellation-requested, already-cancelling, already-cancelled or already-completed disposition. |

`ResolveLocationResponse` wraps the location outcome. `CommandResponse` wraps accepted or replayed start, choice and retry commands. `RunReadResponse` validates read results, `RunEvent` describes watch items and `CancelRunResponse` describes cancellation. `ErrorResponse` wraps `OperationError`.

First admission and idempotent replay are distinct response dispositions. A replay can return the canonical run's later state. It must not falsely promise that completed work is still queued. Reusing a command identity with conflicting input is an admission error.

The shared HTTP error envelope describes rejected input, unknown or unowned resources, conflicting commands, stale versions or invalid lifecycle commands. It is separate from `technical_failure` after work has been admitted. Messages must contain safe user-facing text rather than exceptions, secrets, provider responses or input echoes. A string type cannot establish that text is safe. Later adapters enforce that projection.

`expected_attempt_id` is required but nullable on retry and cancel commands. A null cancellation target identifies unclaimed queued work. Comparing that expectation with the actual current attempt requires loaded run state and remains admission work. A request alone cannot establish that an attempt is current. Likewise, a choice request preserves its offering-version pair but requires the loaded offering and active result to establish validity.

## Event identity and reconnect position

Events carry wire schema version 1, search identity, run identity, attempt identity, an aware timestamp and a strictly positive sequence. `result_version_id` is explicitly null before publication and on non-result outcomes. On ready or partial completion it must match the outcome's version. Wire schema version and recommendation version are different identities.

Queued, progress, cancelling, completed and cancelled payload variants carry only lifecycle information. Events contain no request coordinates, provider bodies, raw reviews, complete evidence collections or model messages. These structural restrictions do not replace source-specific retention and safe-message rules.

Sequence is scoped to a run and remains monotonic across retry attempts. A snapshot records its last committed sequence, including zero before any event. A watch cursor can be zero. Booleans are invalid counters. SSE uses the sequence as its `id` within the run's stream and JSON for the typed envelope. Reconnection resumes strictly after the last received sequence. A stored event replays with the same identity and payload.

A supplied event batch rejects mixed search/run identities and duplicate or decreasing sequences. Gaps remain valid because a batch may be a suffix or filtered selection. This validation does not establish durable ordering, event completeness, state transitions, atomic commits, lease fencing or replay followed by live delivery. GG-005 and GG-018 own those runtime guarantees.

## Examples and local evidence

Literal payloads and their generator live under [examples/lifecycle-contract](examples/lifecycle-contract/). The generator uses only the standard library and does not import application models. The 44 accepted payloads cover each operation, each outcome and retained-result reads. Rejected payloads cover contradictory states, absent or foreign results, version mismatches, invalid counters and unsafe extra payload fields. The task-local checker lives at `data/verification/GG-004.04/attempt-01/check_examples.py`. It exercises accepted payloads, JSON round trips and expected rejections, then exports task-local schemas and artifact identities.

Representative literal payloads cover these boundaries.

| Boundary | Accepted control | Rejected control |
|---|---|---|
| Useful limited result | [Partial outcome](examples/lifecycle-contract/accepted_outcome_partial.json). | [Missing limitations](examples/lifecycle-contract/rejected_partial_without_limitations.json). |
| Prior result after failure | [Technical failure with retained result](examples/lifecycle-contract/accepted_outcome_technical_failure.json). | [Failure claiming a new version](examples/lifecycle-contract/rejected_technical_failure_with_new_version.json). |
| Published result identity | [Ready outcome](examples/lifecycle-contract/accepted_outcome_ready.json). | [Foreign search](examples/lifecycle-contract/rejected_other_search_result.json), [empty selection](examples/lifecycle-contract/rejected_empty_ready_version.json). |
| Event identity | [Ready event](examples/lifecycle-contract/accepted_event_ready.json). | [Mismatched version](examples/lifecycle-contract/rejected_completed_event_version_mismatch.json). |
| Sequence and reconnect | [Ordered gaps across attempts](examples/lifecycle-contract/accepted_event_batch_gaps_and_retry.json), [zero cursor](examples/lifecycle-contract/accepted_watch_from_zero.json). | [Duplicate sequence](examples/lifecycle-contract/rejected_batch_duplicate.json), [boolean cursor](examples/lifecycle-contract/rejected_watch_boolean.json). |
| Admission and cancellation | [Completed replay](examples/lifecycle-contract/accepted_completed_command_replay.json), [completion won cancellation race](examples/lifecycle-contract/accepted_cancel_already_completed.json). | [False cancelled disposition](examples/lifecycle-contract/rejected_cancel_claims_completion.json). |

Run both commands from `apps/backend` with the installed backend environment.

```sh
.venv/bin/python ../../docs/examples/lifecycle-contract/generate_examples.py
.venv/bin/python ../../data/verification/GG-004.04/attempt-01/check_examples.py
```

Neither command accepts arguments, reads `.env` or contacts a service. The generator writes its named JSON payloads beside itself. The checker reads those files and writes evidence into its attempt directory. Both exit zero on success and nonzero on generation, validation, assertion or export failure. `checks.txt` records actual local outcomes.

GG-007 retains broad contract verification, including contradictory outcome and event examples. Source truth, permissions, freshness, active-version admission and authorization remain later runtime responsibilities. No endpoint or live application behavior is established by these payload checks.
