# Controlled worker

GG-005.08 implements a targeted worker command and a deterministic diagnostic job through the existing leased lifecycle. Implementation awaits Section B verification at GG-007. This job exercises durable progress and completion without making provider or model calls.

## Run one admitted diagnostic job

Use the installed Python 3.14 backend environment and an explicitly migrated database. Follow [backend setup](BACKEND_SETUP.md) and the [HTTP admission example](API.md#expected-request-and-response-examples) to obtain a queued run. Subscribe through the [event endpoint](RUN_EVENTS.md) before starting the worker to observe newly committed events at GG-007.

From `apps/backend`, inspect the command without loading configuration or opening storage.

```sh
.venv/bin/python -m app.worker --help
.venv/bin/python -m app.worker diagnostic --help
```

Run the diagnostic against the run ID returned by admission. Replace `RUN_ID` with that ID.

```sh
.venv/bin/python -m app.worker diagnostic --run-id RUN_ID
```

The command loads only database settings, including `GG_DATABASE_PATH`. It does not migrate storage or require provider/model credentials. The required `diagnostic` subcommand and explicit run ID are trusted local operator selection. The worker does not scan queued searches, create a session, admit another run or infer a fixture mode from missing credentials. Selecting a run deliberately advances that run as diagnostic work.

`--step-delay SECONDS` accepts a finite number from zero through five and defaults to one second before each of the two controlled checkpoints. The CLI generates a fresh worker ID and uses a fixed 30-second lease. It validates arguments before loading settings or opening storage. It does not override the configured database path.

The command writes `diagnostic_worker_starting` before attempting the claim and `diagnostic_worker_completed` only after durable completion. The starting token alone is not evidence that a claim succeeded. Failure messages contain fixed tokens rather than input, credentials, database paths, SQL or original exception text.

| Exit | Meaning and stderr token |
|---|---|
| 0 | Diagnostic completion committed. |
| 1 | `diagnostic_configuration_error`, `diagnostic_schema_error`, `diagnostic_storage_error`, `diagnostic_internal_error` or `diagnostic_worker_failed`. |
| 2 | Argument rejection with `diagnostic_argument_error`. Help exits zero before settings/storage access. |
| 3 | Unknown target, `diagnostic_run_not_found`. |
| 4 | Another live claim, `diagnostic_lease_busy`. |
| 5 | Terminal or otherwise unclaimable target, `diagnostic_run_not_claimable`. |
| 6 | Lost/expired claim, `diagnostic_lease_lost`. |
| 130 | Keyboard interruption, `diagnostic_interrupted`. |

This command is a single job process. It does not start an API server, daemon or scheduler. Process lifecycle scripts remain GG-006.06 and GG-006.07 work. Real research integration remains GG-013.01 work.

## Durable state sequence

For a fresh admission with sequence 1, the intended sequence is:

| Sequence | State | Meaning |
|---|---|---|
| 1 | `queued` | Existing admission and queued event. |
| 2 | `running`, `starting` | Claim creates an attempt and current lease. |
| 3 | `running`, `composing` | First controlled checkpoint. No food synthesis occurs. |
| 4 | `running`, `publishing` | Second controlled checkpoint. No recommendation is published. |
| 5 | `completed`, `insufficient_coverage` | The diagnostic lifecycle finishes with an explicit no-research gap. |

A reclaimed expired run instead starts at `recovering` under a new attempt and continues after its existing cursor. Absolute sequence values depend on existing history. Renewal changes lease time without adding an event.

The final gap is `Diagnostic lifecycle check only. No place research was performed.` Completion means the controlled job reached its terminal checkpoint. It does not mean that restaurant research, result quality or provider access succeeded. No fabricated recommendations, sources, contributions, receipts or no-matches conclusion are produced.

Status reads expose the completed snapshot with no recommendation version. SSE replays its stored completion event, drains through the terminal sequence and closes. Reconnecting at that sequence returns 204. These are implemented paths whose actual HTTP behavior remains unverified until GG-007. Cancellation transitions and recommendation publication remain later work.

## Shared lifecycle and terminal persistence

The reusable runner owns claim, renewal, checkpoints and completion. A typed job supplies finite steps with storage-free work callbacks and a final no-result outcome. A step receives neither a SQLite connection nor a lease credential. All work and delay occur after the previous connection has closed.

Claim and each later mutation use the existing short transaction helper. The runner renews before bounded work. A checkpoint or completion still checks current ownership against fresh database time. An expired lease cannot be revived, and work performed after lease loss cannot be persisted by that attempt.

The new completion operation accepts coverage-gap and technical-failure outcomes only. It checks current running state, exact claim and history, creates the next completed snapshot, provisionally appends its matching event and uses the existing final snapshot fence. Exceptions must escape the transaction so all provisional changes roll back. Commit precedes successful CLI output. The current lease row and attempt records remain historical; terminal state prevents a new claim or renewal.

The shared event projection now supports completed coverage-gap and technical-failure states for terminal history validation. This adds no result-version persistence or ready/partial publication. The command uses the same repositories intended for real worker integration rather than separate diagnostic SQL.

## Limits and verification boundary

The bounded diagnostic work needs no concurrent heartbeat. The runner does not promise renewal during an arbitrarily long blocking step. Real research integration must arrange suitably bounded or cooperative work before claiming that behavior. An ordinary work-callback exception attempts a fixed retryable `technical_failure` with code `worker_failure` through the same fenced completion path. An expired claim cannot publish that failure. Storage errors escape to the fixed CLI error boundary. A crash or interruption leaves the attempt for expiry and later reclaim. Repeating a completed target must not create another run or attempt.

From the repository root, run the pure checker with `apps/backend/.venv/bin/python data/verification/GG-005.08/attempt-01/sanity.py`. It requires the installed backend environment, accepts no arguments and exits nonzero on an assertion failure. The checker and command outcomes are recorded under `data/verification/GG-005.08/attempt-01/`. Pure parsing, construction, event projection and help checks do not prove database transactions or worker execution.

GG-007 must run the actual command and inspect durable state, events and safe output. Its scenarios must include successful admission-to-completion, subscription before progress, terminal replay and caught-up 204, duplicate invocation, unknown and busy targets, expiry/reclaim, stale credentials, expiry crossing the final fence, rollback after a provisional completion event, job failure, interruption, restart and corrupt storage. Measure actual progress and completion timing. Keep controlled evidence distinct from live research evidence.
