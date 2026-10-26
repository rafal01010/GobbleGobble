import sqlite3
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.schemas.lifecycle import (
    CompletedEvent,
    CompletedState,
    EventBatch,
    InsufficientCoverageOutcome,
    ProgressEvent,
    QueuedEvent,
    QueuedState,
    RunEvent,
    RunningState,
    RunSnapshot,
    TechnicalFailureOutcome,
)


class RunNotFound(Exception):
    def __init__(self) -> None:
        super().__init__("run_not_found")


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise sqlite3.ProgrammingError("run checkpoints require a transaction")


def _require_run_id(run_id: str) -> None:
    if type(run_id) is not str or not run_id.strip():
        raise ValueError("invalid_run_id")


def _event(snapshot: RunSnapshot, occurred_at: datetime) -> RunEvent:
    payload: QueuedEvent | ProgressEvent | CompletedEvent
    if isinstance(snapshot.state, QueuedState):
        payload = QueuedEvent()
    elif isinstance(snapshot.state, RunningState):
        payload = ProgressEvent(stage=snapshot.state.stage)
    elif isinstance(snapshot.state, CompletedState) and isinstance(
        snapshot.state.outcome, InsufficientCoverageOutcome | TechnicalFailureOutcome
    ):
        payload = CompletedEvent(outcome=snapshot.state.outcome)
    else:
        raise TypeError("invalid_checkpoint_state")
    return RunEvent(
        search_id=snapshot.search_id,
        run_id=snapshot.run_id,
        attempt_id=snapshot.attempt_id,
        sequence=snapshot.last_event_sequence,
        occurred_at=occurred_at,
        result_version_id=None,
        payload=payload,
    )


def _append_event(connection: sqlite3.Connection, snapshot: RunSnapshot) -> None:
    cursor = connection.execute(
        "SELECT CAST(unixepoch('now', 'subsec') * 1000 AS INTEGER) AS now_ms"
    )
    try:
        now_ms = cursor.fetchone()["now_ms"]
    finally:
        cursor.close()
    event = _event(
        snapshot, datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=now_ms)
    )
    connection.execute(
        "INSERT INTO run_events (run_id, sequence, attempt_id, event_json) "
        "VALUES (?, ?, ?, ?)",
        (event.run_id, event.sequence, event.attempt_id, event.model_dump_json()),
    ).close()


def _decode_event(row: sqlite3.Row, snapshot: RunSnapshot) -> RunEvent:
    try:
        event = RunEvent.model_validate_json(row["event_json"])
    except ValidationError:
        raise sqlite3.DatabaseError("invalid_run_event") from None
    if (
        type(row["sequence"]) is not int
        or event.sequence != row["sequence"]
        or event.run_id != row["run_id"]
        or event.attempt_id != row["attempt_id"]
        or event.search_id != snapshot.search_id
        or event.run_id != snapshot.run_id
        or event.sequence > snapshot.last_event_sequence
    ):
        raise sqlite3.DatabaseError("invalid_run_event")
    return event


def _validate_history(connection: sqlite3.Connection, snapshot: RunSnapshot) -> None:
    cursor = connection.execute(
        "SELECT count(*) AS count, max(sequence) AS maximum FROM run_events WHERE run_id = ?",
        (snapshot.run_id,),
    )
    try:
        row = cursor.fetchone()
    finally:
        cursor.close()
    if row["count"] != snapshot.last_event_sequence or row["maximum"] != (
        snapshot.last_event_sequence or None
    ):
        raise sqlite3.DatabaseError("invalid_run_event_history")
    if snapshot.last_event_sequence:
        cursor = connection.execute(
            "SELECT run_id, sequence, attempt_id, event_json FROM run_events "
            "WHERE run_id = ? AND sequence = ?",
            (snapshot.run_id, snapshot.last_event_sequence),
        )
        try:
            event = _decode_event(cursor.fetchone(), snapshot)
        finally:
            cursor.close()
        try:
            payload = _event(snapshot, event.occurred_at).payload
        except TypeError:
            raise sqlite3.DatabaseError("invalid_run_event_history") from None
        if event.attempt_id != snapshot.attempt_id or event.payload != payload:
            raise sqlite3.DatabaseError("invalid_run_event_history")


def _read_snapshot(connection: sqlite3.Connection, run_id: str) -> RunSnapshot:
    cursor = connection.execute(
        "SELECT search_id, run_id, snapshot_json FROM runs WHERE run_id = ?", (run_id,)
    )
    try:
        row = cursor.fetchone()
    finally:
        cursor.close()
    if row is None:
        raise RunNotFound()
    try:
        snapshot = RunSnapshot.model_validate_json(row["snapshot_json"])
    except ValidationError:
        raise sqlite3.DatabaseError("invalid_run_snapshot") from None
    if snapshot.run_id != row["run_id"] or snapshot.search_id != row["search_id"]:
        raise sqlite3.DatabaseError("invalid_run_snapshot")
    _validate_history(connection, snapshot)
    return snapshot


def read_events(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    after_sequence: int,
    limit: int,
) -> EventBatch:
    """Read a bounded suffix in a caller-owned short transaction, without authorization."""
    _require_transaction(connection)
    _require_run_id(run_id)
    if type(after_sequence) is not int or after_sequence < 0:
        raise ValueError("invalid_event_cursor")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid_event_limit")
    snapshot = _read_snapshot(connection, run_id)
    cursor = connection.execute(
        "SELECT run_id, sequence, attempt_id, event_json FROM run_events "
        "WHERE run_id = ? AND sequence > ? ORDER BY sequence LIMIT ?",
        (run_id, after_sequence, limit),
    )
    try:
        events = tuple(_decode_event(row, snapshot) for row in cursor.fetchall())
    finally:
        cursor.close()
    return EventBatch(search_id=snapshot.search_id, run_id=run_id, events=events)
