import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass, field, replace
from uuid import uuid4

from pydantic import ValidationError

from app.db.runs import _decode_snapshot
from app.schemas.lifecycle import QueuedState, RunningState, RunSnapshot, RunStage

_NOW_MS = "CAST(unixepoch('now', 'subsec') * 1000 AS INTEGER)"


class LeaseBusy(Exception):
    def __init__(self) -> None:
        super().__init__("lease_busy")


class LeaseLost(Exception):
    def __init__(self) -> None:
        super().__init__("lease_lost")


class RunNotFound(Exception):
    def __init__(self) -> None:
        super().__init__("run_not_found")


class RunNotClaimable(Exception):
    def __init__(self) -> None:
        super().__init__("run_not_claimable")


@dataclass(frozen=True)
class LeaseClaim:
    run_id: str = field(repr=False)
    attempt_id: str = field(repr=False)
    worker_id: str = field(repr=False)
    credential: str = field(repr=False)
    expires_at_ms: int = field(repr=False)


@dataclass(frozen=True, repr=False)
class _StoredRun:
    snapshot: RunSnapshot
    snapshot_json: str
    lease: sqlite3.Row | None


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise sqlite3.ProgrammingError("worker leases require a transaction")


def _nonblank(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _duration_ms(lease_seconds: object) -> int:
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise ValueError("invalid_lease_seconds")
    return lease_seconds * 1000


def _credential_digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        raise LeaseLost()
    return hashlib.sha256(
        b"gobblegobble-worker-lease-v1\0" + value.encode("ascii")
    ).hexdigest()


def _claim_digest(claim: LeaseClaim) -> str:
    if type(claim) is not LeaseClaim or not all(
        _nonblank(value) for value in (claim.run_id, claim.attempt_id, claim.worker_id)
    ):
        raise LeaseLost()
    return _credential_digest(claim.credential)


def _decode_current(row: sqlite3.Row) -> _StoredRun:
    snapshot = _decode_snapshot(row["snapshot_json"], row["search_id"], row["run_id"])
    lease = row if row["lease_attempt_id"] is not None else None
    if snapshot.attempt_id is None:
        valid = lease is None and row["attempt_count"] == 0
    else:
        valid = (
            lease is not None
            and lease["lease_attempt_id"] == snapshot.attempt_id
            and lease["stored_attempt_id"] == snapshot.attempt_id
            and _nonblank(lease["worker_id"])
            and type(lease["credential_digest"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", lease["credential_digest"]) is not None
            and type(lease["expires_at_ms"]) is int
            and lease["expires_at_ms"] >= 0
            and type(lease["claimed_at_ms"]) is int
            and lease["claimed_at_ms"] >= 0
        )
    if not valid:
        raise sqlite3.DatabaseError("invalid_worker_lease_state")
    return _StoredRun(snapshot, row["snapshot_json"], lease)


def _read_current(connection: sqlite3.Connection, run_id: str) -> _StoredRun | None:
    cursor = connection.execute(
        "SELECT r.run_id, r.search_id, r.snapshot_json, "
        "l.attempt_id AS lease_attempt_id, l.worker_id, l.credential_digest, "
        "l.expires_at_ms, a.attempt_id AS stored_attempt_id, a.claimed_at_ms, "
        "(SELECT count(*) FROM run_attempts WHERE run_id = r.run_id) AS attempt_count "
        "FROM runs AS r LEFT JOIN run_leases AS l ON l.run_id = r.run_id "
        "LEFT JOIN run_attempts AS a "
        "ON a.run_id = l.run_id AND a.attempt_id = l.attempt_id WHERE r.run_id = ?",
        (run_id,),
    )
    try:
        row = cursor.fetchone()
    finally:
        cursor.close()
    return None if row is None else _decode_current(row)


def _running_snapshot(
    snapshot: RunSnapshot, attempt_id: str, state: RunningState
) -> RunSnapshot:
    return RunSnapshot(
        search_id=snapshot.search_id,
        run_id=snapshot.run_id,
        operation=snapshot.operation,
        attempt_id=attempt_id,
        state=state,
        last_event_sequence=snapshot.last_event_sequence,
    )


def _mutate_one(
    connection: sqlite3.Connection, sql: str, parameters: tuple, failure: Exception
) -> None:
    cursor = connection.execute(sql, parameters)
    try:
        changed = cursor.rowcount
    finally:
        cursor.close()
    if changed != 1:
        raise failure


def _write_snapshot(
    connection: sqlite3.Connection,
    current: _StoredRun,
    claim: LeaseClaim,
    digest: str,
    snapshot: RunSnapshot,
) -> None:
    _mutate_one(
        connection,
        "UPDATE runs SET snapshot_json = ? WHERE run_id = ? AND snapshot_json = ? "
        "AND EXISTS (SELECT 1 FROM run_leases AS l "
        "JOIN run_attempts AS a ON a.run_id = l.run_id AND a.attempt_id = l.attempt_id "
        "WHERE l.run_id = runs.run_id AND l.attempt_id = ? AND l.worker_id = ? "
        f"AND l.credential_digest = ? AND l.expires_at_ms > {_NOW_MS})",
        (
            snapshot.model_dump_json(),
            claim.run_id,
            current.snapshot_json,
            claim.attempt_id,
            claim.worker_id,
            digest,
        ),
        LeaseLost(),
    )


def claim_run(
    connection: sqlite3.Connection, run_id: str, worker_id: str, lease_seconds: int
) -> LeaseClaim:
    """Claim queued or expired running work in the caller's BEGIN IMMEDIATE block."""
    _require_transaction(connection)
    duration = _duration_ms(lease_seconds)
    if not _nonblank(run_id):
        raise ValueError("invalid_run_id")
    if not _nonblank(worker_id):
        raise ValueError("invalid_worker_id")
    current = _read_current(connection, run_id)
    if current is None:
        raise RunNotFound()
    if not isinstance(current.snapshot.state, QueuedState | RunningState):
        raise RunNotClaimable()
    claim = LeaseClaim(run_id, uuid4().hex, worker_id, secrets.token_urlsafe(32), 0)
    digest = _credential_digest(claim.credential)
    if current.lease is None:
        eligibility = "NOT EXISTS (SELECT 1 FROM run_leases WHERE run_id = runs.run_id)"
        previous: tuple[object, ...] = ()
    else:
        eligibility = (
            "EXISTS (SELECT 1 FROM run_leases WHERE run_id = runs.run_id "
            "AND attempt_id = ? AND worker_id = ? AND credential_digest = ? "
            f"AND expires_at_ms <= {_NOW_MS})"
        )
        previous = (
            current.lease["lease_attempt_id"],
            current.lease["worker_id"],
            current.lease["credential_digest"],
        )
    _mutate_one(
        connection,
        "INSERT INTO run_attempts (attempt_id, run_id, claimed_at_ms) "
        f"SELECT ?, run_id, {_NOW_MS} FROM runs "
        f"WHERE run_id = ? AND snapshot_json = ? AND {eligibility}",
        (claim.attempt_id, run_id, current.snapshot_json, *previous),
        LeaseBusy(),
    )
    if current.lease is None:
        sql = (
            "INSERT INTO run_leases "
            "(run_id, attempt_id, worker_id, credential_digest, expires_at_ms) "
            f"VALUES (?, ?, ?, ?, {_NOW_MS} + ?)"
        )
        parameters: tuple[object, ...] = (
            run_id,
            claim.attempt_id,
            worker_id,
            digest,
            duration,
        )
    else:
        sql = (
            "UPDATE run_leases SET attempt_id = ?, worker_id = ?, "
            f"credential_digest = ?, expires_at_ms = {_NOW_MS} + ? "
            "WHERE run_id = ? AND attempt_id = ? AND worker_id = ? "
            f"AND credential_digest = ? AND expires_at_ms <= {_NOW_MS}"
        )
        parameters = (claim.attempt_id, worker_id, digest, duration, run_id, *previous)
    _mutate_one(connection, sql, parameters, LeaseBusy())
    state = RunningState(stage="starting" if current.lease is None else "recovering")
    snapshot = _running_snapshot(current.snapshot, claim.attempt_id, state)
    _write_snapshot(connection, current, claim, digest, snapshot)
    return replace(claim, expires_at_ms=_read_expiry(connection, claim))


def _read_expiry(connection: sqlite3.Connection, claim: LeaseClaim) -> int:
    cursor = connection.execute(
        "SELECT expires_at_ms FROM run_leases WHERE run_id = ? AND attempt_id = ?",
        (claim.run_id, claim.attempt_id),
    )
    try:
        return cursor.fetchone()["expires_at_ms"]
    finally:
        cursor.close()


def _owned_running(connection: sqlite3.Connection, claim: LeaseClaim) -> _StoredRun:
    current = _read_current(connection, claim.run_id)
    if current is None or not isinstance(current.snapshot.state, RunningState):
        raise LeaseLost()
    return current


def renew_lease(
    connection: sqlite3.Connection, claim: LeaseClaim, lease_seconds: int
) -> LeaseClaim:
    """Renew a current live claim. Expired claims cannot be revived."""
    _require_transaction(connection)
    duration = _duration_ms(lease_seconds)
    digest = _claim_digest(claim)
    current = _owned_running(connection, claim)
    _mutate_one(
        connection,
        f"UPDATE run_leases SET expires_at_ms = {_NOW_MS} + ? "
        "WHERE run_id = ? AND attempt_id = ? AND worker_id = ? "
        f"AND credential_digest = ? AND expires_at_ms > {_NOW_MS} "
        "AND EXISTS (SELECT 1 FROM runs WHERE runs.run_id = run_leases.run_id "
        "AND snapshot_json = ?)",
        (
            duration,
            claim.run_id,
            claim.attempt_id,
            claim.worker_id,
            digest,
            current.snapshot_json,
        ),
        LeaseLost(),
    )
    return replace(claim, expires_at_ms=_read_expiry(connection, claim))


def advance_running(
    connection: sqlite3.Connection, claim: LeaseClaim, stage: RunStage
) -> RunSnapshot:
    """Change only the running stage after a fresh durable ownership check."""
    _require_transaction(connection)
    digest = _claim_digest(claim)
    try:
        state = RunningState(stage=stage)
    except ValidationError:
        raise ValueError("invalid_run_stage") from None
    current = _owned_running(connection, claim)
    snapshot = _running_snapshot(current.snapshot, claim.attempt_id, state)
    _write_snapshot(connection, current, claim, digest, snapshot)
    return snapshot
