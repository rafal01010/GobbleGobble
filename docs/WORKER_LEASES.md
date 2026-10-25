# Worker claims and lease fencing

GG-005.04 implements trusted internal worker operations in [leases.py](../apps/backend/app/db/leases.py) and appends migration 4. Implementation awaits Section B verification at GG-007. No worker process, HTTP route, scheduler, event, or publication operation exists yet.

## Callable contract

| Callable | Result |
|---|---|
| `claim_run(connection, run_id, worker_id, lease_seconds)` | Frozen `LeaseClaim` for a new attempt. |
| `renew_lease(connection, claim, lease_seconds)` | Same claim identity with an updated informational expiry. |
| `advance_running(connection, claim, stage)` | Validated `RunSnapshot` with only its running stage changed. |

Each operation requires the existing caller-owned short `BEGIN IMMEDIATE` transaction. The repository neither opens storage nor commits. Let every exception escape the transaction block so all partial changes roll back. Deliver returned values only after commit. Complete provider and model work outside transactions.

`lease_seconds` is a strict integer from 1 through 3600, inclusive. Booleans are invalid. Run and worker IDs must be nonblank strings. Worker IDs are trusted internal labels and grant no authority alone. `LeaseClaim` contains `run_id`, `attempt_id`, `worker_id`, `credential`, and integer `expires_at_ms`. Its frozen fields are omitted from representations. Explicit serialization can still expose them. The random credential is internal, distinct from browser credentials, and must stay out of URLs, public payloads, and logs.

## Storage

Migration 4, `create_worker_attempts_and_leases`, adds two tables. Migrations 1 through 3 remain unchanged.

| Table | Fields and constraints |
|---|---|
| `run_attempts` | Primary `attempt_id`, referenced `run_id`, integer nonnegative `claimed_at_ms`, and unique `(run_id, attempt_id)`. Claim operations append history and never edit old attempts. |
| `run_leases` | Primary referenced `run_id`, current `attempt_id`, nonblank `worker_id`, 64-character lowercase hexadecimal `credential_digest`, and integer nonnegative `expires_at_ms`. A composite foreign key binds the attempt to this run. |

Each run has at most one current lease row. Expiry leaves the row in place until replacement. An expired running claim receives a new attempt and credential. Its former attempt remains in history, while the registry replaces the old digest. The database stores only a SHA-256 digest with the domain prefix `gobblegobble-worker-lease-v1` followed by a NUL byte. The random credential comes from `secrets.token_urlsafe(32)`.

The validated running snapshot's attempt must match the registry and its history row. Queued state has neither a lease nor attempt history. Returning an attempted run to queued state is unsupported. A later retry design must explicitly evolve this invariant. Invalid JSON, mismatched snapshot identities, missing history, or inconsistent current ownership fail as operational corruption. They never justify inferred recovery.

## Time and mutation rules

SQLite supplies integer UTC epoch milliseconds with `CAST(unixepoch('now', 'subsec') * 1000 AS INTEGER)`. The [SQLite date and time reference](https://www.sqlite.org/lang_datefunc.html) defines the `subsec` modifier and `now` behavior. The inspected local SQLite version is 3.53.4. Unsupported database time functions fail operationally without a caller-clock fallback.

Each guarded mutation obtains fresh database time in that statement. The clock is not captured at transaction entry or taken from Python, caller input, or `LeaseClaim.expires_at_ms`. Equality with expiry is expired. Renewal requires the same still-live identity and sets expiry to database time plus the admitted duration. It never revives an expired claim at the mutation's clock reading.

`BEGIN IMMEDIATE` serializes writers before decision reads. Claim conditionally appends an attempt, inserts or replaces the registry row, and updates the validated snapshot in the same transaction. Its final snapshot update checks the exact expected stored JSON and an existing lease with the exact run, attempt, worker, and credential digest whose expiry exceeds fresh database time. Each conditional mutation checks its affected-row count. A failed guard aborts the transaction through the caller's rollback boundary.

Renewal requires exact current lease identities, live database expiry, and the exact validated running snapshot JSON. Advancement uses that same snapshot predicate and a fresh lease fence in the final update. Parsing a snapshot or possessing a returned claim does not authorize a later write.

A mutation is accepted at its serialized database statement, not at a promised future commit time. Another claimant cannot interleave before this short transaction commits. Later mutations still need a fresh fence. SQLite uses the trusted host wall clock. A clock rollback can make an unreplaced lease appear live again. This implementation provides no monotonic expiry guarantee.

These repositories do not sandbox trusted code that already holds an unrestricted database connection. Callers must use the repository operations instead of writing worker state directly.

## Transitions and errors

| Existing state or claim | Operation | Result |
|---|---|---|
| Queued, no history or lease | Claim | New attempt, `running` at `starting`. |
| Running with unexpired lease | Any claim, including the same worker | `LeaseBusy` with message `lease_busy`. |
| Running with expired consistent lease | Claim | New attempt and credential, `running` at `recovering`, old history retained. |
| Running with current live credential | Renew | Same attempt and credential, new database-derived expiry. |
| Running with current live credential | Advance | Requested valid running stage. |
| Missing, expired, replaced, or mismatched claim | Renew or advance | `LeaseLost` with message `lease_lost`. |
| Cancelling, completed, or cancelled | Claim | `RunNotClaimable` with message `run_not_claimable`. |
| Cancelling, completed, or cancelled | Renew or advance | `LeaseLost` with message `lease_lost`. |
| Unknown run | Claim | `RunNotFound` with message `run_not_found`. |

Both first claim and reclaim preserve search ID, run ID, operation, and event cursor. Advancement preserves those fields and the current attempt. Stage names describe progress. They define no ordering relation. This child adds no release, retry, cancellation, terminal-state, or publication API.

A missing transaction raises fixed `sqlite3.ProgrammingError("worker leases require a transaction")` before SQL. Invalid duration, run ID, worker ID, or stage raises a value-free `ValueError` code. Malformed claims raise fixed `LeaseLost` before SQL. For structurally valid claims, current stored corruption takes precedence over stale or mismatched credentials. Snapshot corruption retains `sqlite3.DatabaseError("invalid_run_snapshot")`. Ownership inconsistency uses `sqlite3.DatabaseError("invalid_worker_lease_state")`. Other SQLite errors remain operational failures and need sanitization at future external adapters.

Lease loss means the worker stops using this attempt to advance or publish work and discards later external results unless future recovery explicitly admits them. Expiry cannot stop an already-issued provider request or promise exactly-once billing.

## Checkpoints and deferred verification

`advance_running` is temporarily an eventless internal stage writer. GG-005.05 must internalize or replace this standalone operation with an atomic checkpoint and matching ordered event while retaining the fresh SQL ownership fence. No contribution or event table is added here.

The task-local pure checker runs from the repository root after backend setup:

```sh
apps/backend/.venv/bin/python data/verification/GG-005.04/attempt-01/sanity.py
```

It prohibits database opens and SQL. It checks imports, transaction and input guards, fixed errors, redaction, pure snapshot transitions and decoding, existing command replay decoding, and exact migration-1-through-3 preservation. These checks do not prove transactional behavior.

GG-007 must migrate empty and earlier-version databases, inspect constraints and history, and exercise duplicate and concurrent claims, renewal, expiry equality, reclaim, stale and wrong-credential writes, crossing expiry within one transaction, rollback, restart, corrupt state, and replay after progress. Broad tests, database opens, SQL execution, migrations, HTTP checks, and live calls remain deferred.
