# Persistence and worker lifecycle

SQLite stores anonymous ownership, command replay, run snapshots, attempts, leases, events and safe work metadata. The repositories live in [app/db](../apps/backend/app/db/). They implement the diagnostic lifecycle; recommendation versions, user retry/cancellation and retained research content are not yet stored. See [local setup](SETUP.md) for commands and [the API](API.md) for HTTP transport.

## Configuration

`load_database_settings()` reads `GG_DATABASE_PATH` from the backend environment without provider/model configuration. The default is `<repository>/data/runtime/gobblegobble.sqlite3`. Relative paths resolve against the repository root. Blank paths, URI/colon syntax, `:memory:` and NUL characters are rejected. Loading settings does not create storage. The [settings reference](SETTINGS.md#independent-local-database-settings) defines precedence and errors.

`data/runtime/` and SQLite journal/WAL/shared-memory sidecars are ignored. A custom base file elsewhere is not automatically ignored. Its location, access and retention are the operator's responsibility.

## Connections and transactions

[session.py](../apps/backend/app/db/session.py) opens an existing file with `mode=rw`, `autocommit=True`, a five-second SQLite timeout and thread affinity. It enables foreign keys and returns rows by column name. Sessions own fresh connections, close them on exit and roll back unfinished transactions. Missing storage fails rather than being created by a request. Only explicit migration creates the directory/file.

`transaction(connection)` rejects nesting and uses SQL `BEGIN IMMEDIATE`, `COMMIT` and `ROLLBACK`. An escaping exception, including commit failure, rolls back while a transaction remains active. Connection `commit()` and `rollback()` are ineffective with `autocommit=True`, so the helper uses SQL commands.

Repository writes require the caller's short transaction. Let every failure escape it. Catching an error inside the block and then committing can preserve earlier provisional writes. Deliver return values only after commit. Bind SQL parameters and close cursors before returning.

Provider/model work, sleeps, user interaction and streaming must occur outside storage. An async caller offloads each complete synchronous operation into one thread; it never carries a connection across `await`. The event subscription uses a short deferred read transaction for a consistent authorized snapshot without reserving the writer lock. There is no pool, WAL-mode setup or automatic retry beyond SQLite's lock timeout.

## Migration history

[migrations.py](../apps/backend/app/db/migrations.py) owns the immutable ordered `MIGRATIONS` tuple. Each record has a contiguous integer version, name and individual SQL statements. Its SHA-256 checksum covers the exact version/name/statement JSON. Append migrations; never edit applied records.

The runner acquires `BEGIN IMMEDIATE` before inspecting schema/history. A fresh database applies the full manifest. An existing database must have an exact nonempty prefix of versions, names and checksums. Missing versions, altered history, future versions, invalid ledgers and nonempty unversioned schemas are rejected. The runner never stamps, resets, downgrades or deletes an unknown database.

All pending statements and ledger rows commit together or roll back together. Empty file/directory creation can survive failure and be initialized on retry. Concurrent migration processes serialize before checking pending work. A current schema performs no migration statements. Manifest entries cannot contain their own transaction commands, `VACUUM` or incompatible journal changes; the runner neither splits SQL nor uses `executescript()`.

| Version | Main storage |
|---|---|
| 1 | `schema_migrations`, with version, name and checksum. |
| 2 | `anonymous_sessions` and `search_ownership`. |
| 3 | `runs` and owner-scoped `run_commands`, with composite ownership/run-membership keys. |
| 4 | Append-only `run_attempts` and one current `run_leases` row per run. |
| 5 | `run_events`, `run_contributions`, `run_receipts` and same-run `contribution_receipts` links. |

Migration 5 leaves legitimate cursor-zero snapshots unchanged and invents no historical events. Ledger agreement detects edited migration records, not arbitrary manual changes to application tables.

## Anonymous ownership

[ownership.py](../apps/backend/app/db/ownership.py) issues a server-generated 256-bit browser credential, stores only its domain-prefixed SHA-256 digest and binds searches to a separate session identity. Search/run IDs grant no authority. A well-formed caller-selected credential cannot create an identity.

`issue_session` is a deliberate write, and the raw credential is delivered only after commit. `resolve_session` rejects malformed or unknown credentials. `create_search` binds a new ID to an existing principal. `require_search_access` returns the owned search; foreign and unknown searches share `not_found`. Invalid credentials retain `invalid_session` rather than causing replacement issuance.

Before any run read, replay, mutation or watch, authorize the search and select the run by both search ID and run ID. This pairing is required even when the browser owns both searches. Composite foreign keys bind command owner, search and run membership. Run-only trusted readers perform no browser authorization themselves.

Credentials and precise location must stay out of URLs, public snapshots and unnecessary logs. Frozen internal principal/claim representations hide fields, but explicit serialization can still expose them. Losing the browser credential loses access through this interface. Expiry, rotation, revocation, cleanup and account recovery are not implemented. Cookie/Origin requirements are documented in the API reference.

## Admission and replay

`admit_start` in [runs.py](../apps/backend/app/db/runs.py) receives validated `StartRunRequest` input inside the caller's `BEGIN IMMEDIATE` block. It resolves ownership and checks `(owner_session_id, command_id)` before allocating identities. It never waits for a provider, model or worker.

A first start atomically creates search ownership, one queued snapshot, queued event 1 and an immutable command record. The snapshot has no attempt. Equal normalized input returns the same identities and current persisted snapshot, including later progress. Conflicting payload or command-kind reuse raises `CommandConflict`. Another owner can independently use the same command ID. Corrupt/missing linked records fail operationally instead of admitting replacement work.

Canonical version-1 start JSON includes all defaults/nulls and the complete validated request, excludes the command lookup key, sorts keys and rejects nonfinite values. Equivalent walking defaults compare equally. Meal time normalizes to UTC with six fractional digits and `Z`; coordinate signed zero becomes `0.0`. Strings and array order remain exact. Comparison uses the full canonical JSON, not merely a hash.

A retry must retain its original meal instant. Changed normalized input needs a new command ID. Changing schema defaults or canonicalization requires compatibility handling for stored records. The complete request, including sensitive location, exists in the immutable command payload. No cleanup policy currently defines when replay records may be deleted.

## Worker leases

[leases.py](../apps/backend/app/db/leases.py) claims a specific queued run or reclaims an expired running attempt. A claim appends attempt history, installs a new random credential digest and commits running state at `starting` or `recovering` with the next event. A worker ID alone grants no authority. `lease_seconds` is a strict integer from 1 through 3600; boolean durations are invalid.

A live competing claim raises `LeaseBusy`. A missing run raises `RunNotFound`; a terminal/cancelling target raises `RunNotClaimable`. Reclaim preserves search/run/operation identity, retains old attempts and replaces the current credential. Queued state cannot retain attempt history or a lease; returning attempted work to queued is unsupported.

The final snapshot update compares exact prior snapshot JSON and current run/attempt/worker/credential/attempt-history identity. It also requires expiry to exceed fresh SQLite UTC milliseconds in that same statement. Equality is expired. Python time and the informational expiry returned in a claim cannot authorize a write. Renewal extends the same still-live claim from fresh database time, emits no event and cannot revive expiry.

Checkpoint/completion writers append their provisional event/metadata before this final fence. A failed fence raises `LeaseLost`; caller rollback must undo all provisional writes. No writes follow the fence. Acceptance happens at the serialized statement, not at a promised future commit time. The trusted host wall clock can roll back; no monotonic-expiry guarantee is provided.

Lease loss means stop advancing/publishing through that attempt and discard later external results unless explicitly accepted by future recovery. It cannot stop an already-issued request or ensure exactly-once external billing. Trusted code with an unrestricted database connection is not sandboxed by these helpers.

## Checkpoints and events

[checkpoints.py](../apps/backend/app/db/checkpoints.py) commits a running stage, contribution metadata and safe receipts with one matching progress event. Every accepted call advances sequence, even when a metadata batch is empty or repeated. Stage names may repeat and do not establish a transition-order state machine.

Each tuple batch accepts at most 256 contributions and 256 receipts. A contribution has a local UUID, closed capability/outcome and at most 256 incoming receipt links, normalized to a sorted set. It contains no arbitrary research payload, URL, quote, place ID or model response. Inputs are revalidated before writes, including unchecked model instances.

Receipts persist only their `safe_record()` projection in canonical JSON. Decimal cost precision is preserved; unknown usage/cost remains null. Same-ID, same-content metadata deduplicates within a run while retaining original attempt/acceptance sequence. Changed content or cross-run ID reuse raises `ReceiptConflict` or `ContributionConflict`. Receipt links must resolve within the run; missing links roll back the checkpoint. Multiple contributions may share an earlier same-run receipt.

`run_events` is keyed by `(run_id, sequence)`. Contributions and receipts reference their accepting progress event and attempt; link-table constraints require same-run records. Append conventions do not prevent trusted direct SQL from changing them.

[events.py](../apps/backend/app/db/events.py) reads a bounded ordered suffix strictly after the cursor. Internal limits are 1 through 1000; cursors are strict nonnegative integers. `read_committed_work` returns unique accepted metadata in event/identity order. Reads validate stored JSON, scalar identity, accepting events and latest snapshot/history agreement. Nonzero cursors without matching history are corruption. The history check scans the run event index; no scale claim is measured.

## Research snapshots

[research_snapshots.py](../apps/backend/app/db/research_snapshots.py) indexes candidate identities, retained finding handles, and fixed gap reasons by search and snapshot. It does not copy evidence or finding text into the snapshot tables. The caller stores permitted evidence first, then supplies only finding handles that already belong to the candidate's branch. Google review bodies and their restricted descendants cannot be indexed as stored findings. A permitted Google branch identifier remains in the evidence store for later reacquisition.

The caller identifies Geoapify candidate identity fields as such before saving a name, coordinates, and timezone. The index checks coordinate and timezone shape and the Geoapify storage policy, but it cannot verify the caller's provenance assertion. A candidate without permitted identity data keeps its local ID and kind, and receives a `candidate_reacquisition_required` gap. The index stores no arbitrary gap prose, route facts, or opening facts. It does not yet reacquire candidates or connect to admitted searches.

Sample counts, assessed counts, selection class, and partial status can be retained only for an already stored, branch-scoped independent source. Unknown or restricted material remains an explicit fixed gap. The caller must provide material omission and partial-sample gaps; the index cannot infer missing source content. At most eight sample records are accepted per candidate.

`view_snapshot` pages candidates and finding handles independently with limits from 1 to 100. Each page reports how many candidates and findings remain and supplies continuation handles. A `place_id` filter selects a branch for a narrower view. `lookup_snapshot_finding` checks the search, snapshot, place, and finding handle, then returns a finding only when every supporting item is fresh. Stale or undated support returns a state without finding content. Freshness does not itself establish permission to display or send the finding to a model. The existing evidence store remains the source for attributable source lookup and retained history.

## Completion and verification limits

[completion.py](../apps/backend/app/db/completion.py) accepts only revalidated coverage-gap or technical-failure outcomes. It preserves identities, advances sequence, appends the matching completed event and uses the same final live-lease fence. Lease rows remain historical; terminal state rejects later claims/renewals. Ready/partial results, no-matches publication and cancellation need their own storage and event projection.

The shared runner in [worker.py](../apps/backend/app/worker.py) owns claim, renewal, checkpoint and completion transactions. Its finite job callbacks receive no connection or lease credential and run after storage closes. A callback exception may publish only a fixed retryable `worker_failure` through a still-current claim. Storage errors escape; interruption leaves the attempt for expiry/reclaim. Arbitrarily long callbacks have no concurrent heartbeat and need bounded or cooperative work before production integration.

The [setup guide](SETUP.md#run-a-diagnostic-job) defines diagnostic commands and exit codes. The [foundation checks](SETUP.md#verify-the-local-foundation) exercise fresh storage, actual HTTP and worker processes, event replay and restart recovery. [Backend lifecycle tests](../apps/backend/tests/test_foundation.py) cover migration upgrades, command and lease contention, ownership, provisional-write rollback, final-fence expiry and controlled worker failure. These bounded checks establish no throughput or exactly-once billing guarantee.
