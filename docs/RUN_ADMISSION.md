# Queued run admission

GG-005.03 implements `admit_start` in [runs.py](../apps/backend/app/db/runs.py). A repeated start command has the same search and run identities. Implementation is complete, awaiting Section B verification at GG-007. GG-005.04 adds [worker claim and lease repositories](WORKER_LEASES.md). GG-005.06 adds the HTTP admission and status routes. GG-005.08 adds [targeted diagnostic execution](CONTROLLED_WORKER.md) for an explicitly selected admitted run. Production research execution remains later work.

## Callable contract

`admit_start(connection, credential, command: StartRunRequest) -> CommandResponse` uses the existing caller-owned SQLite connection and short `BEGIN IMMEDIATE` transaction. It resolves the supplied browser credential through [anonymous search ownership](SEARCH_ACCESS.md). It does not issue sessions, load settings, open storage, commit, perform network work, or await research.

The caller validates untrusted input with the authoritative `StartRunRequest` model. Do not bypass validation with `model_construct`, unchecked model copies, or mutated nested values. `command_id` is an opaque, owner-scoped key. It remains exact, including whitespace and case. A different owner can use the same command ID independently.

This is a callable integration example, not an HTTP endpoint. `settings` is existing `DatabaseSettings`, `browser_credential` is an issued browser credential, and `command` is validated input.

```python
from app.db.runs import admit_start
from app.db.session import session, transaction

with session(settings) as connection, transaction(connection):
	response = admit_start(connection, browser_credential, command)
# Deliver response only after the transaction commits successfully.
```

Let admission exceptions escape the transaction block. Catching an error inside the block and committing can preserve partial writes. The operation relies on the existing transaction helper for rollback. `BEGIN IMMEDIATE` must precede the credential and command lookups so concurrent writers serialize before deciding whether to create work.

## Admission and replay

The operation looks up the authenticated owner and command ID before generating a search or run. A new command creates one search ownership row, one run, one queued event, and one command record in the same transaction. Since GG-005.05, its `AcceptedCommand` contains an initial queued `RunSnapshot` with null `attempt_id` and `last_event_sequence = 1`. The matching queued event has sequence 1 and database time. No attempt is created. Existing cursor-zero snapshots remain unchanged until a new transition.

An equal normalized request returns `ReplayedCommand` with the same identities and the current persisted snapshot. It does not return a saved queued acknowledgment after progress. A worker may advance the run after commit and before the first response arrives. The accepted response describes the admission checkpoint.

The replay decoder validates stored JSON with the authoritative `RunSnapshot` and checks its IDs against the run and command columns. Missing rows, inconsistent ownership, invalid snapshots, and identity mismatches fail with fixed `sqlite3.DatabaseError("invalid_run_snapshot")`. They never admit replacement work. The replay lookup preserves an existing command row even if its linked storage is inconsistent.

Reusing the key with a different command kind or normalized payload raises `CommandConflict` with fixed message `command_conflict`. The error includes no submitted values. Invalid credentials retain `InvalidSession("invalid_session")`. Missing transactions raise a fixed `sqlite3.ProgrammingError` before SQL. Other SQLite failures remain operational failures rather than becoming replay or conflict responses. Future HTTP adapters must sanitize those failures and map the existing API error envelope.

## Canonical input

The immutable command payload is compact sorted JSON with `canonical_version = 1`, `kind = "start"`, and the complete validated `SearchRequest`. The command ID is the lookup key and is excluded from this payload.

- All defaults and null fields are included. Omitted walking defaults and explicit 15-minute walking compare equally.
- `eat_now_at` becomes UTC with exactly six fractional-second digits and a terminal `Z`. Different offsets for one instant compare equally. Admission does not substitute the current time.
- Latitude and longitude normalize signed zero to `0.0`. Nonzero coordinates retain their validated values.
- Strings and array order remain exact. Location labels, timezone names, provider references, addresses, and their order remain part of the comparison.
- JSON uses ASCII escapes and rejects non-finite numbers. Comparison uses the canonical JSON itself, not only a hash.

A retry must preserve its original meal instant. A changed instant or another changed normalized field needs a new command ID. Canonicalization or schema-default changes require an explicit compatibility decision for existing version-1 records. Do not silently reserialize old payloads using changed rules.

## Storage and privacy

Migration 3, `create_queued_run_admission`, appends the following objects. Migrations 1 and 2 remain unchanged.

| Object | Stored fields and constraints |
|---|---|
| `search_ownership_identity_owner_idx` | Unique `(search_id, owner_session_id)` parent key for command ownership. |
| `runs` | Primary `run_id`, referenced `search_id`, and validated-on-read `snapshot_json`. Unique `(search_id, run_id)` supplies the command membership key. |
| `run_commands` | Primary `(owner_session_id, command_id)`, command kind, canonical payload, search ID, and run ID. Composite foreign keys bind the command to the search owner and the run within that search. |
| `run_commands_start_run_idx` | One start command per run through a unique partial index. |

The full request is retained only in the immutable start-command record. The mutable run snapshot has no request location or browser credential. Exact coordinates, resolved location details, provider references, and meal time are sensitive runtime data. Keep canonical payloads, credentials, and raw operational errors out of logs, exception messages, URLs, and public responses. Model representation redaction does not make explicit serialization safe.

The default database is under ignored `data/runtime/`. Custom paths retain the operator's ignore and retention responsibilities. Replay depends on retaining the command mapping, run, and ownership rows. No expiry or cleanup policy is implemented. Deleting those rows requires an explicit replay and retention decision.

## Expected observations and deferred verification

At GG-007, a first start should add exactly one search, run, queued event, and command row. An identical start should replay with no new rows. A conflicting start should return `command_conflict` with no committed writes. Another owner using the same ID should get independent identities. Replay after progress should return the updated validated snapshot.

GG-007 must execute migration 3 against empty and earlier-version databases, inspect foreign keys and uniqueness, and prove identical, conflicting, concurrent, rollback, lost-response, restart, cross-owner, and corrupt-snapshot behavior. No SQL, migrations, database opens, HTTP checks, provider calls, model calls, or broad tests ran for this child.

The task-local checker covers pure canonicalization, malformed credentials and transaction guards before SQL, imports with database opening prohibited, migration-1/2 preservation, and representation safety. From the repository root after backend setup, run:

```sh
apps/backend/.venv/bin/python data/verification/GG-005.03/attempt-01/sanity.py
```

Worker claims and fenced running-stage changes are implemented in GG-005.04. Replay uses the shared validated snapshot decoder and returns the persisted current attempt and stage. GG-005.05 adds [atomic checkpoints and trusted event and work reads](RUN_CHECKPOINTS.md). GG-005.06 adds HTTP credential transport and public status reads. Watch, refinement, retry and cancel remain later tasks.


## HTTP admission and paired status reads

GG-005.06 exposes `POST /api/runs` as HTTP 202 with the existing `CommandResponse` for both accepted and replayed input. A first command returns queued state, null attempt and sequence 1. Replay returns the current persisted snapshot, which can have advanced. The typed JSON boundary preserves aware datetime strings and provider-reference arrays through strict JSON-mode validation. Boolean minutes, extra fields and nonfinite numbers remain invalid. No model configuration is weakened.

The cookie must already exist from explicit `POST /api/session` bootstrap. Invalid credentials receive 401 without issuance. The mutation Origin guard executes before body parsing. The handler opens, admits, commits and closes in one synchronous invocation before response delivery. It performs no provider, model or worker calls.

`GET /api/searches/{search_id}/runs/{run_id}` authorizes the search first, then uses the focused `read_snapshot` helper with both IDs. Unknown or foreign searches and mismatched runs share a fixed 404, including for searches owned by the same browser. Stored JSON or identity inconsistency remains an operational error. The response wraps the snapshot without inventing recommendation data. [The API reference](API.md#expected-request-and-response-examples) gives literal requests, success payloads and safe errors. HTTP behavior, timing, persistence and isolation remain GG-007 verification.
