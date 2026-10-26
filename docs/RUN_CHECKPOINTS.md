# Committed work and ordered events

GG-005.05 implements [checkpoints.py](../apps/backend/app/db/checkpoints.py), [events.py](../apps/backend/app/db/events.py), and migration 5. One leased coordinator accepts contribution metadata and safe receipts with a running snapshot and matching progress event. Implementation awaits database verification at GG-007.

## Callable contract

| Callable | Result |
|---|---|
| `commit_checkpoint(connection, claim, *, stage, contributions=(), receipts=())` | Next `RunSnapshot`. Every accepted call advances the cursor and appends exactly one matching progress event, including empty or repeated batches. |
| `read_events(connection, run_id, *, after_sequence, limit)` | `EventBatch` in ascending sequence, strictly after the supplied cursor. |
| `read_committed_work(connection, run_id)` | Frozen `CommittedWork` containing the current snapshot and all accepted contribution and receipt metadata in acceptance order, with identity as the tie breaker. |

These are trusted internal repositories. They perform no browser authorization, open no storage, and never commit. Each requires a caller-owned short `transaction(connection)` block. This helper starts `BEGIN IMMEDIATE`, so its consistent view covers every query in a recovery read. No connection crosses an await or thread boundary. Returned values become usable after commit.

```python
from app.db.checkpoints import ContributionRecord, commit_checkpoint
from app.db.session import transaction

# Finish external work before entering the transaction.
with transaction(connection):
	snapshot = commit_checkpoint(
		connection,
		claim,
		stage="researching",
		contributions=(ContributionRecord(
			contribution_id=contribution_id,
			capability="reviews",
			outcome="completed",
			receipt_ids=(receipt.local_request_id,),
		),),
		receipts=(receipt,),
	)
# Deliver snapshot after successful commit.
```

Every failure must escape the transaction block. Catching an exception inside the block and committing can preserve provisional writes. A failed SQLite statement alone does not roll back earlier successful statements.

## Closed metadata and identities

`ContributionRecord` is strict, frozen, and rejects extra fields. It has a generated local UUID `contribution_id`, the existing closed `Capability`, an `outcome` of `completed`, `unavailable`, or `stopped`, and a tuple of local receipt UUIDs. References normalize to a sorted set. An empty tuple is valid. There is no arbitrary payload, URL, quote, place ID, source prose, or model response field. A completed record confirms accepted metadata. Research content must be reacquired.

Each batch accepts at most 256 contributions and 256 receipts. Each contribution accepts at most 256 incoming receipt links before normalization. Batch containers must be tuples. Input models are revalidated before any writes, including instances created through unchecked construction. Stage names retain the existing lifecycle model and may repeat.

Receipts persist exclusively through the existing `Receipt.safe_record()` projection. Canonical JSON sorts keys, uses compact separators and ASCII escapes, and rejects nonfinite values. Decimal USD strings retain their exact precision and scale. Missing usage and cost remain null. Enclosing call results and raw provider or model bodies never enter storage.

An identical receipt UUID and canonical safe projection within the same run is accepted once. Identical contribution UUIDs also compare capability, outcome, and normalized receipt references. Repetition preserves original attempt and accepted sequence. Changed content or cross-run UUID reuse raises fixed `ReceiptConflict("receipt_conflict")` or `ContributionConflict("contribution_conflict")`. Identical duplicates inside one incoming batch collapse before writes. Multiple contributions may reference one same-run receipt, including a receipt accepted by an earlier attempt. Missing references raise `ValueError("missing_contribution_receipt")` and roll back the checkpoint.

Recovery returns one row per unique receipt. Future cumulative accounting must count unique admitted receipt IDs and preserve unknown usage and cost. Repeated checkpoint calls still append progress events. This task adds no checkpoint-command replay ledger or exactly-once billing promise.

## Storage and final fence

Migration 5, `create_run_events_and_committed_work`, leaves migrations 1 through 4 unchanged.

| Table | Durable fields and relationships |
|---|---|
| `run_events` | Primary `(run_id, sequence)`, positive strict integer sequence, nullable attempt, and event JSON. Run and composite attempt foreign keys bind identity. |
| `run_contributions` | Globally unique contribution UUID, run, origin attempt, positive accepted event sequence, closed capability and outcome. Composite foreign keys bind the attempt and accepting event to the run. |
| `run_receipts` | Globally unique receipt UUID, run, origin attempt, positive accepted event sequence, and canonical safe JSON. Composite foreign keys bind the attempt and accepting event to the run. |
| `contribution_receipts` | Primary `(run_id, contribution_id, local_request_id)`. Composite foreign keys require both records in the same run. |

Acceptance indexes order contributions and receipts by run, accepted sequence, and identity. Repository operations append records and links without changing their original acceptance. These conventions do not sandbox trusted code holding an unrestricted SQLite connection. No cleanup or expiry policy is added.

The latest checkpoint remains `runs.snapshot_json`. The repository validates current ownership and event history, appends the next event and new metadata, then calls the existing private final snapshot writer. That final write matches exact previous snapshot JSON, run, attempt, worker, credential digest, and immutable attempt membership. It requires `expires_at_ms > CAST(unixepoch('now', 'subsec') * 1000 AS INTEGER)` in that same statement. A failed final fence raises `LeaseLost`, and the caller rolls back every provisional insert. No writes follow that fence. Its clock and commit-time limits remain those in [worker leases](WORKER_LEASES.md#time-and-mutation-rules).

## Event history and reads

New admission writes queued event 1 with snapshot cursor 1. Command replay adds nothing. First claim and expired takeover append `starting` and `recovering` progress events with the next sequence, preserving run identity across attempts. Renewal emits no event. Event timestamps come from SQLite UTC milliseconds. Queued and progress events have null result version.

Migration 5 creates empty metadata tables and leaves legitimate existing cursor-zero snapshots unchanged. Their first new claim or checkpoint starts at sequence 1. It invents no queue or progress history. A nonzero cursor without matching history is corruption.

`after_sequence` is a strict nonnegative integer. `limit` is a strict integer from 1 through 1000. Booleans are invalid. Both readers require nonblank run IDs and a transaction before SQL. Unknown runs raise fixed `RunNotFound("run_not_found")`. Reads validate stored JSON against scalar identities, current run identity and cursor, and accepted work against its progress event and attempt. Invalid records raise fixed database errors. The GG-005.06 HTTP boundary sanitizes operational SQLite failures, including GG-005.07 event subscription failures. Internal callers still receive storage errors.

History validation counts events and checks the maximum and latest payload against the current snapshot. This scans the run's event index on each checkpoint or read. `read_committed_work` returns all metadata and performs additional acceptance queries. These are local-scale recovery helpers. Pagination or a different history index requires later measured need. GG-005.08 adds [fenced non-result completion](CONTROLLED_WORKER.md) in `app/db/completion.py` and shared history projection for completed coverage-gap or technical-failure outcomes. Result publication, no-matches and cancellation require extension in their owning tasks. GG-005.07 separately adds the [authorized SSE subscription](RUN_EVENTS.md) over these readers. This repository adds no worker process, cancellation, retry or retained research content.

## Verification boundary

The pure checker runs from the repository root after backend setup:

```sh
apps/backend/.venv/bin/python data/verification/GG-005.05/attempt-01/sanity.py
```

It prohibits database opens and SQL and checks only literal builders, record validation, canonical receipt roundtrips, guards, fixed errors, redaction, import structure, and exact migration-1-through-4 preservation. It does not prove transactional behavior.

GG-007 must migrate empty and version-4 databases, inspect foreign keys and constraints, and prove admission, claim, reclaim, and checkpoint ordering. Required cases include same-ID repetition and conflict, shared receipts, cross-run isolation, receipt-only checkpoints, rollback at each provisional write, expiry crossing before the final fence, stale or wrong claims, restart recovery, legacy cursor-zero rows, and stored corruption.
