import sqlite3

from pydantic import ValidationError

from app.db.events import _append_event, _require_transaction, _validate_history
from app.db.leases import (
    LeaseClaim,
    LeaseLost,
    _claim_digest,
    _owned_running,
    _write_snapshot,
)
from app.schemas.lifecycle import (
    CompletedState,
    InsufficientCoverageOutcome,
    RunSnapshot,
    TechnicalFailureOutcome,
)

type NonResultOutcome = InsufficientCoverageOutcome | TechnicalFailureOutcome


def _validated_outcome(outcome: NonResultOutcome) -> NonResultOutcome:
    try:
        if type(outcome) is InsufficientCoverageOutcome:
            return InsufficientCoverageOutcome.model_validate(outcome)
        if type(outcome) is TechnicalFailureOutcome:
            return TechnicalFailureOutcome.model_validate(outcome)
    except ValidationError:
        pass
    raise ValueError("invalid_completion_outcome") from None


def _completed_snapshot(current: RunSnapshot, outcome: NonResultOutcome) -> RunSnapshot:
    return RunSnapshot(
        search_id=current.search_id,
        run_id=current.run_id,
        operation=current.operation,
        attempt_id=current.attempt_id,
        state=CompletedState(outcome=outcome),
        last_event_sequence=current.last_event_sequence + 1,
    )


def complete_without_result(
    connection: sqlite3.Connection, claim: LeaseClaim, *, outcome: NonResultOutcome
) -> RunSnapshot:
    """Complete owned work without a result version in a caller-owned transaction."""
    _require_transaction(connection)
    outcome = _validated_outcome(outcome)
    digest = _claim_digest(claim)
    current = _owned_running(connection, claim)
    if current.lease is None or (
        current.lease["lease_attempt_id"],
        current.lease["worker_id"],
        current.lease["credential_digest"],
    ) != (claim.attempt_id, claim.worker_id, digest):
        raise LeaseLost()
    _validate_history(connection, current.snapshot)
    snapshot = _completed_snapshot(current.snapshot, outcome)
    _append_event(connection, snapshot)
    _write_snapshot(connection, current, claim, digest, snapshot)
    return snapshot
