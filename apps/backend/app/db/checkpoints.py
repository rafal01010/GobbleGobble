import json
import sqlite3
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.calls import Capability, Receipt
from app.db.events import (
    _append_event,
    _decode_event,
    _read_snapshot,
    _require_run_id,
    _require_transaction,
    _validate_history,
)
from app.db.leases import (
    LeaseClaim,
    LeaseLost,
    _claim_digest,
    _owned_running,
    _running_snapshot,
    _write_snapshot,
)
from app.schemas.lifecycle import ProgressEvent, RunningState, RunSnapshot, RunStage

MAX_BATCH_RECORDS = 256
MAX_RECEIPT_LINKS = 256


class ReceiptConflict(Exception):
    def __init__(self) -> None:
        super().__init__("receipt_conflict")


class ContributionConflict(Exception):
    def __init__(self) -> None:
        super().__init__("contribution_conflict")


class ContributionRecord(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    contribution_id: UUID = Field(repr=False)
    capability: Capability
    outcome: Literal["completed", "unavailable", "stopped"]
    receipt_ids: Annotated[tuple[UUID, ...], Field(max_length=MAX_RECEIPT_LINKS)] = (
        Field(default=(), repr=False)
    )

    @field_validator("receipt_ids")
    @classmethod
    def _normalize_links(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return tuple(sorted(set(value)))


@dataclass(frozen=True)
class StoredContribution:
    contribution: ContributionRecord = field(repr=False)
    origin_attempt_id: str = field(repr=False)
    accepted_sequence: int


@dataclass(frozen=True)
class StoredReceipt:
    receipt: Receipt = field(repr=False)
    origin_attempt_id: str = field(repr=False)
    accepted_sequence: int


@dataclass(frozen=True)
class CommittedWork:
    run: RunSnapshot = field(repr=False)
    contributions: tuple[StoredContribution, ...] = field(repr=False)
    receipts: tuple[StoredReceipt, ...] = field(repr=False)


def _canonical_receipt(receipt: Receipt) -> str:
    try:
        if type(receipt) is not Receipt:
            raise ValueError
        validated = Receipt.model_validate(receipt)
        return json.dumps(
            validated.safe_record(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except ValidationError, ValueError, TypeError:
        raise ValueError("invalid_receipt") from None


def _prepare_batch(
    contributions: tuple[ContributionRecord, ...], receipts: tuple[Receipt, ...]
) -> tuple[dict[UUID, ContributionRecord], dict[UUID, str]]:
    if (
        type(contributions) is not tuple
        or type(receipts) is not tuple
        or len(contributions) > MAX_BATCH_RECORDS
        or len(receipts) > MAX_BATCH_RECORDS
    ):
        raise ValueError("invalid_checkpoint_batch")
    contribution_records: dict[UUID, ContributionRecord] = {}
    receipt_records: dict[UUID, str] = {}
    for record in contributions:
        try:
            if type(record) is not ContributionRecord:
                raise ValueError
            record = ContributionRecord.model_validate(record)
        except ValidationError, ValueError:
            raise ValueError("invalid_contribution") from None
        previous = contribution_records.setdefault(record.contribution_id, record)
        if previous != record:
            raise ContributionConflict()
    for receipt in receipts:
        canonical = _canonical_receipt(receipt)
        previous_json = receipt_records.setdefault(receipt.local_request_id, canonical)
        if previous_json != canonical:
            raise ReceiptConflict()
    return contribution_records, receipt_records


def _one(
    connection: sqlite3.Connection, sql: str, parameters: tuple
) -> sqlite3.Row | None:
    cursor = connection.execute(sql, parameters)
    try:
        return cursor.fetchone()
    finally:
        cursor.close()


def _rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple
) -> list[sqlite3.Row]:
    cursor = connection.execute(sql, parameters)
    try:
        return cursor.fetchall()
    finally:
        cursor.close()


def _validate_acceptance(
    connection: sqlite3.Connection, row: sqlite3.Row, snapshot: RunSnapshot
) -> None:
    sequence = row["accepted_sequence"]
    if (
        row["run_id"] != snapshot.run_id
        or type(sequence) is not int
        or not 1 <= sequence <= snapshot.last_event_sequence
        or type(row["origin_attempt_id"]) is not str
        or not row["origin_attempt_id"].strip()
    ):
        raise sqlite3.DatabaseError("invalid_committed_work")
    event_row = _one(
        connection,
        "SELECT e.run_id, e.sequence, e.attempt_id, e.event_json FROM run_events AS e "
        "JOIN run_attempts AS a ON a.run_id = e.run_id AND a.attempt_id = e.attempt_id "
        "WHERE e.run_id = ? AND e.sequence = ?",
        (snapshot.run_id, sequence),
    )
    if event_row is None:
        raise sqlite3.DatabaseError("invalid_committed_work")
    event = _decode_event(event_row, snapshot)
    if event.attempt_id != row["origin_attempt_id"] or not isinstance(
        event.payload, ProgressEvent
    ):
        raise sqlite3.DatabaseError("invalid_committed_work")


def _decode_receipt(row: sqlite3.Row) -> Receipt:
    try:
        receipt = Receipt.model_validate_json(row["safe_record_json"])
        valid = (
            str(receipt.local_request_id) == row["local_request_id"]
            and _canonical_receipt(receipt) == row["safe_record_json"]
        )
    except ValidationError, ValueError, TypeError:
        valid = False
    if not valid:
        raise sqlite3.DatabaseError("invalid_stored_receipt")
    return receipt


def _decode_contribution(
    row: sqlite3.Row, receipt_ids: tuple[str, ...]
) -> ContributionRecord:
    try:
        record = ContributionRecord.model_validate_json(
            json.dumps(
                {
                    "contribution_id": row["contribution_id"],
                    "capability": row["capability"],
                    "outcome": row["outcome"],
                    "receipt_ids": receipt_ids,
                }
            )
        )
        if (
            str(record.contribution_id) != row["contribution_id"]
            or tuple(str(value) for value in record.receipt_ids) != receipt_ids
        ):
            raise ValueError
    except ValidationError, ValueError, TypeError:
        raise sqlite3.DatabaseError("invalid_stored_contribution") from None
    return record


def _contribution_links(
    connection: sqlite3.Connection, run_id: str, contribution_id: str
) -> tuple[str, ...]:
    return tuple(
        row["local_request_id"]
        for row in _rows(
            connection,
            "SELECT local_request_id FROM contribution_receipts "
            "WHERE run_id = ? AND contribution_id = ? ORDER BY local_request_id",
            (run_id, contribution_id),
        )
    )


def commit_checkpoint(
    connection: sqlite3.Connection,
    claim: LeaseClaim,
    *,
    stage: RunStage,
    contributions: tuple[ContributionRecord, ...] = (),
    receipts: tuple[Receipt, ...] = (),
) -> RunSnapshot:
    """Stage metadata and progress, then fence ownership. Let failures escape rollback."""
    _require_transaction(connection)
    digest = _claim_digest(claim)
    try:
        state = RunningState(stage=stage)
    except ValidationError:
        raise ValueError("invalid_run_stage") from None
    contribution_records, receipt_records = _prepare_batch(contributions, receipts)
    current = _owned_running(connection, claim)
    if current.lease is None or (
        current.lease["lease_attempt_id"],
        current.lease["worker_id"],
        current.lease["credential_digest"],
    ) != (claim.attempt_id, claim.worker_id, digest):
        raise LeaseLost()
    _validate_history(connection, current.snapshot)
    snapshot = _running_snapshot(current.snapshot, claim.attempt_id, state)
    _append_event(connection, snapshot)
    for identity, canonical in receipt_records.items():
        row = _one(
            connection,
            "SELECT * FROM run_receipts WHERE local_request_id = ?",
            (str(identity),),
        )
        if row is not None:
            if row["run_id"] != claim.run_id:
                raise ReceiptConflict()
            _validate_acceptance(connection, row, current.snapshot)
            _decode_receipt(row)
            if row["safe_record_json"] != canonical:
                raise ReceiptConflict()
            continue
        connection.execute(
            "INSERT INTO run_receipts "
            "(local_request_id, run_id, origin_attempt_id, accepted_sequence, safe_record_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(identity),
                claim.run_id,
                claim.attempt_id,
                snapshot.last_event_sequence,
                canonical,
            ),
        ).close()
    for identity, record in contribution_records.items():
        row = _one(
            connection,
            "SELECT * FROM run_contributions WHERE contribution_id = ?",
            (str(identity),),
        )
        if row is not None:
            if row["run_id"] != claim.run_id:
                raise ContributionConflict()
            _validate_acceptance(connection, row, current.snapshot)
            previous = _decode_contribution(
                row, _contribution_links(connection, claim.run_id, str(identity))
            )
            if previous != record:
                raise ContributionConflict()
            continue
        for receipt_id in record.receipt_ids:
            receipt_row = _one(
                connection,
                "SELECT * FROM run_receipts WHERE run_id = ? AND local_request_id = ?",
                (claim.run_id, str(receipt_id)),
            )
            if receipt_row is None:
                raise ValueError("missing_contribution_receipt")
            _validate_acceptance(connection, receipt_row, snapshot)
            _decode_receipt(receipt_row)
        connection.execute(
            "INSERT INTO run_contributions "
            "(contribution_id, run_id, origin_attempt_id, accepted_sequence, capability, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(identity),
                claim.run_id,
                claim.attempt_id,
                snapshot.last_event_sequence,
                record.capability,
                record.outcome,
            ),
        ).close()
        for receipt_id in record.receipt_ids:
            connection.execute(
                "INSERT INTO contribution_receipts (run_id, contribution_id, local_request_id) "
                "VALUES (?, ?, ?)",
                (claim.run_id, str(identity), str(receipt_id)),
            ).close()
    _write_snapshot(connection, current, claim, digest, snapshot)
    return snapshot


def read_committed_work(connection: sqlite3.Connection, run_id: str) -> CommittedWork:
    """Inspect all accepted metadata in one caller transaction, without authorization."""
    _require_transaction(connection)
    _require_run_id(run_id)
    snapshot = _read_snapshot(connection, run_id)
    receipts = []
    known_receipts = set()
    for row in _rows(
        connection,
        "SELECT * FROM run_receipts WHERE run_id = ? ORDER BY accepted_sequence, local_request_id",
        (run_id,),
    ):
        _validate_acceptance(connection, row, snapshot)
        receipt = _decode_receipt(row)
        known_receipts.add(str(receipt.local_request_id))
        receipts.append(
            StoredReceipt(receipt, row["origin_attempt_id"], row["accepted_sequence"])
        )
    contributions = []
    for row in _rows(
        connection,
        "SELECT * FROM run_contributions WHERE run_id = ? ORDER BY accepted_sequence, contribution_id",
        (run_id,),
    ):
        _validate_acceptance(connection, row, snapshot)
        links = _contribution_links(connection, run_id, row["contribution_id"])
        if not set(links) <= known_receipts:
            raise sqlite3.DatabaseError("invalid_committed_work")
        record = _decode_contribution(row, links)
        contributions.append(
            StoredContribution(
                record, row["origin_attempt_id"], row["accepted_sequence"]
            )
        )
    return CommittedWork(snapshot, tuple(contributions), tuple(receipts))
