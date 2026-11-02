from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from uuid import UUID

from app.core.calls import CallSpec, Receipt
from app.core.settings import DatabaseSettings
from app.db import leases
from app.db.leases import LeaseClaim, LeaseLost, _claim_digest
from app.db.session import session, transaction


class AttemptCallLedger:
    """Record admitted calls before dispatch and reconcile their safe receipts."""

    def __init__(self, settings: DatabaseSettings, claim: LeaseClaim) -> None:
        self.settings = settings
        self.claim = claim
        self._tokens: dict[UUID, str] = {}

    def admit(self, request_id: UUID, spec: CallSpec, started_at: datetime) -> None:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with session(self.settings) as connection, transaction(connection):
            current = connection.execute(
                "SELECT 1 FROM run_leases WHERE run_id=? AND attempt_id=? AND worker_id=? "
                f"AND credential_digest=? AND expires_at_ms > {leases._NOW_MS}",
                (
                    self.claim.run_id,
                    self.claim.attempt_id,
                    self.claim.worker_id,
                    _claim_digest(self.claim),
                ),
            ).fetchone()
            if current is None:
                raise LeaseLost()
            connection.execute(
                "INSERT INTO attempt_call_ledger (request_id,run_id,attempt_id,capability,started_at,completion_digest) VALUES (?,?,?,?,?,?)",
                (
                    str(request_id),
                    self.claim.run_id,
                    self.claim.attempt_id,
                    spec.capability,
                    started_at.isoformat(),
                    digest,
                ),
            )
        self._tokens[request_id] = token

    def finish(self, receipt: Receipt) -> None:
        token = self._tokens[receipt.local_request_id]
        digest = hashlib.sha256(token.encode()).hexdigest()
        record = json.dumps(
            receipt.safe_record(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with session(self.settings) as connection, transaction(connection):
            cursor = connection.execute(
                "UPDATE attempt_call_ledger SET safe_record_json=? WHERE request_id=? AND run_id=? AND attempt_id=? AND completion_digest=? AND safe_record_json IS NULL",
                (
                    record,
                    str(receipt.local_request_id),
                    self.claim.run_id,
                    self.claim.attempt_id,
                    digest,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("call ledger completion rejected")
        del self._tokens[receipt.local_request_id]
