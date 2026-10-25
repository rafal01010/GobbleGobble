import json
import sqlite3
from datetime import UTC
from uuid import uuid4

from pydantic import ValidationError

from app.db.ownership import create_search, resolve_session
from app.schemas.lifecycle import QueuedState, RunSnapshot
from app.schemas.operations import (
    AcceptedCommand,
    CommandResponse,
    ReplayedCommand,
    StartRunRequest,
)


class CommandConflict(Exception):
    def __init__(self) -> None:
        super().__init__("command_conflict")


def _canonical_start(command: StartRunRequest) -> str:
    request = command.request.model_dump(mode="json")
    request["eat_now_at"] = (
        command.request.eat_now_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    coordinates = request["origin"]["location"]["coordinates"]
    for name in ("latitude", "longitude"):
        if coordinates[name] == 0:
            coordinates[name] = 0.0
    return json.dumps(
        {"canonical_version": 1, "kind": "start", "request": request},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _replayed_snapshot(row: sqlite3.Row, owner_session_id: str) -> RunSnapshot:
    if (
        row["search_owner"] != owner_session_id
        or row["stored_search_id"] != row["search_id"]
        or row["stored_run_id"] != row["run_id"]
    ):
        raise sqlite3.DatabaseError("invalid_run_snapshot")
    return _decode_snapshot(row["snapshot_json"], row["search_id"], row["run_id"])


def _decode_snapshot(snapshot_json: str, search_id: str, run_id: str) -> RunSnapshot:
    try:
        snapshot = RunSnapshot.model_validate_json(snapshot_json)
    except ValidationError:
        raise sqlite3.DatabaseError("invalid_run_snapshot") from None
    if snapshot.search_id != search_id or snapshot.run_id != run_id:
        raise sqlite3.DatabaseError("invalid_run_snapshot")
    return snapshot


def admit_start(
    connection: sqlite3.Connection,
    credential: object,
    command: StartRunRequest,
) -> CommandResponse:
    """Admit or replay a validated start within the caller's short transaction."""
    if not connection.in_transaction:
        raise sqlite3.ProgrammingError("run admission requires a transaction")
    principal = resolve_session(connection, credential)
    canonical_payload = _canonical_start(command)
    cursor = connection.execute(
        "SELECT c.command_kind, c.canonical_payload_json, c.search_id, c.run_id, "
        "r.search_id AS stored_search_id, r.run_id AS stored_run_id, "
        "r.snapshot_json, s.owner_session_id AS search_owner "
        "FROM run_commands AS c "
        "LEFT JOIN runs AS r ON r.search_id = c.search_id AND r.run_id = c.run_id "
        "LEFT JOIN search_ownership AS s ON s.search_id = c.search_id "
        "WHERE c.owner_session_id = ? AND c.command_id = ?",
        (principal._session_id, command.command_id),
    )
    try:
        row = cursor.fetchone()
    finally:
        cursor.close()
    if row is not None:
        if (
            row["command_kind"] != "start"
            or row["canonical_payload_json"] != canonical_payload
        ):
            raise CommandConflict()
        return CommandResponse(
            command=ReplayedCommand(
                command_id=command.command_id,
                run=_replayed_snapshot(row, principal._session_id),
            )
        )

    search = create_search(connection, principal)
    snapshot = RunSnapshot(
        search_id=search.search_id,
        run_id=uuid4().hex,
        operation="initial",
        attempt_id=None,
        state=QueuedState(),
        last_event_sequence=0,
    )
    connection.execute(
        "INSERT INTO runs (run_id, search_id, snapshot_json) VALUES (?, ?, ?)",
        (snapshot.run_id, snapshot.search_id, snapshot.model_dump_json()),
    ).close()
    connection.execute(
        "INSERT INTO run_commands "
        "(owner_session_id, command_id, command_kind, canonical_payload_json, "
        "search_id, run_id) VALUES (?, ?, ?, ?, ?, ?)",
        (
            principal._session_id,
            command.command_id,
            "start",
            canonical_payload,
            snapshot.search_id,
            snapshot.run_id,
        ),
    ).close()
    return CommandResponse(
        command=AcceptedCommand(command_id=command.command_id, run=snapshot)
    )
