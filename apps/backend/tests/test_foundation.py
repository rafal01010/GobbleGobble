import json
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import ValidationError

from app import worker
from app.core.calls import Receipt
from app.core.settings import DatabaseSettings
from app.db import leases, migrations
from app.db.checkpoints import (
    ContributionConflict,
    ContributionRecord,
    ReceiptConflict,
    commit_checkpoint,
    read_committed_work,
)
from app.db.completion import complete_without_result
from app.db.events import read_events
from app.db.leases import (
    LeaseBusy,
    LeaseClaim,
    LeaseLost,
    RunNotClaimable,
    claim_run,
    renew_lease,
)
from app.db.migrations import Migration, MigrationError, migrate
from app.db.ownership import (
    SearchNotFound,
    create_search,
    issue_session,
    require_search_access,
)
from app.db.runs import CommandConflict, admit_start, read_snapshot
from app.db.session import session, transaction
from app.schemas.evidence import (
    AvailableReviews,
    EvidenceCollection,
    IndependentFoodFallback,
    IndependentReviewFallback,
    UnknownFoodClaim,
)
from app.schemas.lifecycle import (
    CompletedState,
    InsufficientCoverageOutcome,
    QueuedState,
    RunSnapshot,
)
from app.schemas.operations import StartRunRequest

EXAMPLES = Path(__file__).resolve().parents[3] / "docs/examples/evidence-contract"


@pytest.fixture
def database(tmp_path: Path) -> DatabaseSettings:
    settings = DatabaseSettings(database_path=tmp_path / "foundation.sqlite3")
    migrate(settings)
    return settings


@pytest.fixture
def connection(database: DatabaseSettings) -> Iterator[sqlite3.Connection]:
    with session(database) as opened:
        yield opened


@pytest.fixture
def database_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(leases, "_NOW_MS", "1000")


@pytest.fixture
def command() -> StartRunRequest:
    return StartRunRequest.model_validate_json(
        json.dumps(
            {
                "command_id": "foundation-command",
                "request": {
                    "origin": {
                        "kind": "current",
                        "location": {
                            "coordinates": {"latitude": 0.0, "longitude": 0.0},
                            "timezone": "Asia/Manila",
                        },
                    },
                    "travel": {"mode": "walk", "minutes": 15},
                    "eat_now_at": "2026-10-28T12:00:00+08:00",
                },
            }
        )
    )


@pytest.fixture
def credential(connection: sqlite3.Connection) -> str:
    with transaction(connection):
        return issue_session(connection).credential


@pytest.fixture
def admitted(
    connection: sqlite3.Connection, credential: str, command: StartRunRequest
) -> RunSnapshot:
    with transaction(connection):
        return admit_start(connection, credential, command).command.run


@pytest.fixture
def claim(
    connection: sqlite3.Connection, admitted: RunSnapshot, database_clock: None
) -> LeaseClaim:
    with transaction(connection):
        return claim_run(connection, admitted.run_id, "worker-a", 30)


@pytest.fixture
def receipt() -> Receipt:
    return Receipt(
        local_request_id=UUID("00000000-0000-0000-0000-000000000001"),
        provider="geoapify",
        capability="location",
        use="api_diagnostic",
        input_sources=(),
        model_role=None,
        requested_model=None,
        model_identity="not_applicable",
        started_at=datetime(2026, 10, 28, 4, tzinfo=UTC),
        duration_seconds=0.25,
        admitted=True,
        outcome="success",
        diagnostic="none",
        usage=None,
        cost=None,
    )


@pytest.fixture
def contribution(receipt: Receipt) -> ContributionRecord:
    return ContributionRecord(
        contribution_id=UUID("00000000-0000-0000-0000-000000000002"),
        capability="location",
        outcome="completed",
        receipt_ids=(receipt.local_request_id,),
    )


def test_migrations_create_durable_storage_and_rerun_without_changes(
    tmp_path: Path,
) -> None:
    settings = DatabaseSettings(database_path=tmp_path / "empty" / "foundation.db")
    assert migrate(settings) == (1, 2, 3, 4, 5, 6, 7)
    with session(settings) as opened, transaction(opened):
        credential = issue_session(opened).credential
    assert migrate(settings) == ()
    with session(settings) as opened, transaction(opened):
        assert opened.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert (
            opened.execute("SELECT count(*) FROM anonymous_sessions").fetchone()[0] == 1
        )
        assert issue_session(opened).credential != credential
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            opened.execute(
                "INSERT INTO search_ownership VALUES ('orphan', 'missing-owner')"
            )


def test_migrations_reject_tampered_history_without_applying_changes(
    database: DatabaseSettings, connection: sqlite3.Connection
) -> None:
    connection.execute(
        "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 3"
    )
    with pytest.raises(MigrationError, match="history_mismatch"):
        migrate(database)
    assert (
        connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 7
    )
    assert (
        connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version = 3"
        ).fetchone()[0]
        == "tampered"
    )


def test_simultaneous_migrations_apply_the_manifest_once(tmp_path: Path) -> None:
    settings = DatabaseSettings(database_path=tmp_path / "race" / "foundation.db")
    barrier = Barrier(2)

    def upgrade(_: int) -> tuple[int, ...]:
        barrier.wait(timeout=5)
        return migrate(settings)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upgrade, (1, 2)))
    assert sorted(results) == [(), (1, 2, 3, 4, 5, 6, 7)]
    with session(settings) as opened:
        assert [
            row["version"]
            for row in opened.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ] == [1, 2, 3, 4, 5, 6, 7]
    assert migrate(settings) == ()


def test_version_one_database_upgrades_and_admits_a_queued_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: StartRunRequest
) -> None:
    settings = DatabaseSettings(database_path=tmp_path / "version-one.sqlite3")
    manifest = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", manifest[:1])
    assert migrate(settings) == (1,)
    monkeypatch.setattr(migrations, "MIGRATIONS", manifest)
    assert migrate(settings) == (2, 3, 4, 5, 6, 7)
    with session(settings) as opened, transaction(opened):
        credential = issue_session(opened).credential
        response = admit_start(opened, credential, command)
        events = read_events(
            opened, response.command.run.run_id, after_sequence=0, limit=10
        )
    assert response.command.disposition == "accepted"
    assert response.command.run.state.model_dump() == {"status": "queued"}
    assert [
        (event.sequence, event.payload.model_dump()) for event in events.events
    ] == [(1, {"kind": "queued"})]
    assert migrate(settings) == ()


def test_failed_migration_rolls_back_schema_and_ledger(
    database: DatabaseSettings,
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        migrations.MIGRATIONS
        + (
            Migration(
                8,
                "controlled_failure",
                ("CREATE TABLE partial (id TEXT)", "INVALID SQL"),
            ),
        ),
    )
    with pytest.raises(sqlite3.OperationalError, match="syntax error"):
        migrate(database)
    assert (
        connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 7
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name = 'partial'"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "cursor", [0, 1], ids=["legacy-empty-history", "corrupt-history"]
)
def test_populated_upgrade_accepts_zero_cursor_and_rejects_missing_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cursor: int, database_clock: None
) -> None:
    settings = DatabaseSettings(database_path=tmp_path / "upgrade.sqlite3")
    manifest = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", manifest[:4])
    assert migrate(settings) == (1, 2, 3, 4)
    with session(settings) as opened, transaction(opened):
        principal = issue_session(opened).principal
        search = create_search(opened, principal)
        snapshot = RunSnapshot(
            search_id=search.search_id,
            run_id="legacy-run",
            operation="initial",
            attempt_id=None,
            state=QueuedState(),
            last_event_sequence=cursor,
        )
        opened.execute(
            "INSERT INTO runs VALUES (?, ?, ?)",
            (snapshot.run_id, snapshot.search_id, snapshot.model_dump_json()),
        )
    monkeypatch.setattr(migrations, "MIGRATIONS", manifest)
    assert migrate(settings) == (5, 6, 7)
    with session(settings) as opened:
        if cursor:
            with (
                pytest.raises(sqlite3.DatabaseError, match="invalid_run_event_history"),
                transaction(opened),
            ):
                claim_run(opened, "legacy-run", "upgrade-worker", 30)
            assert (
                opened.execute("SELECT count(*) FROM run_attempts").fetchone()[0] == 0
            )
            assert (
                read_snapshot(
                    opened, search_id=search.search_id, run_id="legacy-run"
                ).state.status
                == "queued"
            )
        else:
            with transaction(opened):
                claim_run(opened, "legacy-run", "upgrade-worker", 30)
            with transaction(opened):
                events = read_events(opened, "legacy-run", after_sequence=0, limit=10)
            assert [(e.sequence, e.payload.model_dump()) for e in events.events] == [
                (1, {"kind": "progress", "stage": "starting"})
            ]


def test_admission_persists_queued_event_and_replays_current_snapshot(
    connection: sqlite3.Connection,
    credential: str,
    command: StartRunRequest,
    database_clock: None,
) -> None:
    with transaction(connection):
        accepted = admit_start(connection, credential, command)
    assert accepted.command.disposition == "accepted"
    assert accepted.command.run.state.model_dump() == {"status": "queued"}
    assert accepted.command.run.last_event_sequence == 1
    with transaction(connection):
        events = read_events(
            connection, accepted.command.run.run_id, after_sequence=0, limit=10
        )
        claim_run(connection, accepted.command.run.run_id, "worker-a", 30)
    assert [event.payload.model_dump() for event in events.events] == [
        {"kind": "queued"}
    ]
    location = command.request.origin.location
    coordinates = location.coordinates.model_copy(update={"latitude": -0.0})
    origin = command.request.origin.model_copy(
        update={"location": location.model_copy(update={"coordinates": coordinates})}
    )
    equivalent = command.model_copy(
        update={
            "request": command.request.model_copy(
                update={
                    "eat_now_at": datetime(2026, 10, 28, 4, tzinfo=UTC),
                    "origin": origin,
                }
            )
        }
    )
    with transaction(connection):
        replayed = admit_start(connection, credential, equivalent)
    assert replayed.command.disposition == "replayed"
    assert replayed.command.run.run_id == accepted.command.run.run_id
    assert replayed.command.run.state.model_dump() == {
        "status": "running",
        "stage": "starting",
    }
    assert replayed.command.run.last_event_sequence == 2
    assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_conflicting_command_preserves_original_and_owner_isolation(
    connection: sqlite3.Connection,
    credential: str,
    command: StartRunRequest,
    admitted: RunSnapshot,
) -> None:
    conflicting = command.model_copy(
        update={
            "request": command.request.model_copy(
                update={"eat_now_at": command.request.eat_now_at + timedelta(minutes=1)}
            )
        }
    )
    with (
        pytest.raises(CommandConflict, match="command_conflict"),
        transaction(connection),
    ):
        admit_start(connection, credential, conflicting)
    with transaction(connection):
        other = issue_session(connection)
        separate = admit_start(connection, other.credential, command)
    assert separate.command.disposition == "accepted"
    assert separate.command.run.run_id != admitted.run_id
    with pytest.raises(SearchNotFound, match="not_found"):
        require_search_access(connection, other.credential, admitted.search_id)
    assert (
        require_search_access(connection, credential, admitted.search_id).search_id
        == admitted.search_id
    )
    assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 2


def test_simultaneous_workers_cannot_both_claim(
    database: DatabaseSettings, admitted: RunSnapshot, database_clock: None
) -> None:
    barrier = Barrier(2)

    def compete(worker_id: str) -> str:
        barrier.wait(timeout=5)
        try:
            with session(database) as opened, transaction(opened):
                claim_run(opened, admitted.run_id, worker_id, 30)
            return "claimed"
        except LeaseBusy:
            return "busy"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, ("worker-a", "worker-b")))
    assert sorted(outcomes) == ["busy", "claimed"]
    with session(database) as opened, transaction(opened):
        work = read_committed_work(opened, admitted.run_id)
        assert work.run.state.model_dump() == {"status": "running", "stage": "starting"}
        assert work.run.last_event_sequence == 2
        assert opened.execute("SELECT count(*) FROM run_attempts").fetchone()[0] == 1


def test_simultaneous_duplicate_admission_creates_one_run(
    database: DatabaseSettings, credential: str, command: StartRunRequest
) -> None:
    barrier = Barrier(2)

    def submit(_: int) -> tuple[str, str]:
        barrier.wait(timeout=5)
        with session(database) as opened, transaction(opened):
            response = admit_start(opened, credential, command)
        return response.command.disposition, response.command.run.run_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, (1, 2)))
    assert sorted(disposition for disposition, _ in responses) == [
        "accepted",
        "replayed",
    ]
    assert responses[0][1] == responses[1][1]
    with session(database) as opened, transaction(opened):
        assert opened.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert opened.execute("SELECT count(*) FROM run_commands").fetchone()[0] == 1
        events = read_events(opened, responses[0][1], after_sequence=0, limit=10)
    assert [
        (event.sequence, event.payload.model_dump()) for event in events.events
    ] == [(1, {"kind": "queued"})]


def test_renewal_extends_only_a_live_lease(
    connection: sqlite3.Connection, claim: LeaseClaim, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert claim.expires_at_ms == 31000
    monkeypatch.setattr(leases, "_NOW_MS", "2000")
    with transaction(connection):
        renewed = renew_lease(connection, claim, 60)
    assert renewed.expires_at_ms == 62000
    monkeypatch.setattr(leases, "_NOW_MS", "62000")
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        renew_lease(connection, renewed, 30)
    assert (
        connection.execute("SELECT expires_at_ms FROM run_leases").fetchone()[0]
        == 62000
    )


@pytest.mark.parametrize("identity", ["worker", "attempt", "credential"])
def test_wrong_identity_cannot_renew_checkpoint_or_complete(
    connection: sqlite3.Connection, claim: LeaseClaim, identity: str
) -> None:
    forged = {
        "worker": replace(claim, worker_id="other-worker"),
        "attempt": replace(claim, attempt_id="other-attempt"),
        "credential": replace(claim, credential="A" * 43),
    }[identity]
    outcome = InsufficientCoverageOutcome(gaps=("Controlled foundation case.",))
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        renew_lease(connection, forged, 30)
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        commit_checkpoint(connection, forged, stage="researching")
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        complete_without_result(connection, forged, outcome=outcome)
    with transaction(connection):
        snapshot = commit_checkpoint(connection, claim, stage="researching")
        events = read_events(connection, claim.run_id, after_sequence=0, limit=10)
    assert snapshot.last_event_sequence == 3
    assert [event.sequence for event in events.events] == [1, 2, 3]
    assert snapshot.state.model_dump() == {"status": "running", "stage": "researching"}


def test_reclaim_retains_accepted_work_and_fences_stale_attempt(
    connection: sqlite3.Connection,
    claim: LeaseClaim,
    receipt: Receipt,
    contribution: ContributionRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with transaction(connection):
        commit_checkpoint(
            connection,
            claim,
            stage="researching",
            contributions=(contribution,),
            receipts=(receipt,),
        )
    monkeypatch.setattr(leases, "_NOW_MS", "31000")
    with transaction(connection):
        replacement = claim_run(connection, claim.run_id, "worker-b", 30)
    assert replacement.attempt_id != claim.attempt_id
    assert replacement.expires_at_ms == 61000
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        renew_lease(connection, claim, 30)
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        commit_checkpoint(connection, claim, stage="publishing")
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        complete_without_result(
            connection,
            claim,
            outcome=InsufficientCoverageOutcome(gaps=("Controlled case.",)),
        )
    with transaction(connection):
        recovered = read_committed_work(connection, claim.run_id)
        snapshot = commit_checkpoint(
            connection,
            replacement,
            stage="composing",
            contributions=(contribution,),
            receipts=(receipt,),
        )
        replayed = read_committed_work(connection, claim.run_id)
    assert recovered.run.state.model_dump() == {
        "status": "running",
        "stage": "recovering",
    }
    assert recovered.run.last_event_sequence == 4
    assert snapshot.last_event_sequence == 5
    assert len(replayed.contributions) == 1
    assert len(replayed.receipts) == 1
    assert replayed.contributions[0].origin_attempt_id == claim.attempt_id
    assert replayed.contributions[0].accepted_sequence == 3
    assert replayed.receipts[0].origin_attempt_id == claim.attempt_id
    assert replayed.receipts[0].receipt.duration_seconds == 0.25
    assert replayed.receipts[0].receipt.usage is None
    assert replayed.receipts[0].receipt.cost is None


@pytest.mark.parametrize("conflict", ["receipt", "contribution", "missing-receipt"])
def test_checkpoint_failure_rolls_back_events_receipts_contributions_and_links(
    connection: sqlite3.Connection,
    claim: LeaseClaim,
    receipt: Receipt,
    contribution: ContributionRecord,
    conflict: str,
) -> None:
    with transaction(connection):
        commit_checkpoint(
            connection,
            claim,
            stage="researching",
            contributions=(contribution,),
            receipts=(receipt,),
        )
    new_receipt = receipt.model_copy(
        update={"local_request_id": UUID("00000000-0000-0000-0000-000000000003")}
    )
    new_contribution = contribution.model_copy(
        update={
            "contribution_id": UUID("00000000-0000-0000-0000-000000000004"),
            "receipt_ids": (new_receipt.local_request_id,),
        }
    )
    receipts: tuple[Receipt, ...]
    contributions: tuple[ContributionRecord, ...]
    if conflict == "receipt":
        receipts = (new_receipt, receipt.model_copy(update={"duration_seconds": 0.5}))
        contributions = (new_contribution,)
        failure: type[Exception] = ReceiptConflict
        message = "receipt_conflict"
    elif conflict == "contribution":
        receipts = (new_receipt,)
        contributions = (
            new_contribution,
            contribution.model_copy(update={"outcome": "unavailable"}),
        )
        failure = ContributionConflict
        message = "contribution_conflict"
    else:
        receipts = (new_receipt,)
        contributions = (
            new_contribution,
            contribution.model_copy(
                update={
                    "contribution_id": UUID("00000000-0000-0000-0000-000000000005"),
                    "receipt_ids": (UUID("00000000-0000-0000-0000-000000000099"),),
                }
            ),
        )
        failure = ValueError
        message = "missing_contribution_receipt"
    with pytest.raises(failure, match=message), transaction(connection):
        commit_checkpoint(
            connection,
            claim,
            stage="publishing",
            contributions=contributions,
            receipts=receipts,
        )
    with transaction(connection):
        work = read_committed_work(connection, claim.run_id)
        events = read_events(connection, claim.run_id, after_sequence=0, limit=10)
    assert work.run.state.model_dump() == {"status": "running", "stage": "researching"}
    assert work.run.last_event_sequence == 3
    assert [event.sequence for event in events.events] == [1, 2, 3]
    assert [
        (str(item.contribution.contribution_id), item.accepted_sequence)
        for item in work.contributions
    ] == [("00000000-0000-0000-0000-000000000002", 3)]
    assert [str(item.receipt.local_request_id) for item in work.receipts] == [
        "00000000-0000-0000-0000-000000000001"
    ]
    assert (
        connection.execute("SELECT count(*) FROM contribution_receipts").fetchone()[0]
        == 1
    )


def test_expiry_at_final_checkpoint_fence_rolls_back_the_entire_batch(
    connection: sqlite3.Connection,
    claim: LeaseClaim,
    receipt: Receipt,
    contribution: ContributionRecord,
) -> None:
    connection.execute(
        "CREATE TEMP TRIGGER expire_after_link AFTER INSERT ON contribution_receipts "
        "BEGIN UPDATE run_leases SET expires_at_ms = 1000 WHERE run_id = NEW.run_id; END"
    )
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        commit_checkpoint(
            connection,
            claim,
            stage="researching",
            contributions=(contribution,),
            receipts=(receipt,),
        )
    with transaction(connection):
        work = read_committed_work(connection, claim.run_id)
        events = read_events(connection, claim.run_id, after_sequence=0, limit=10)
    assert work.run.state.model_dump() == {"status": "running", "stage": "starting"}
    assert [event.sequence for event in events.events] == [1, 2]
    assert work.contributions == ()
    assert work.receipts == ()
    assert (
        connection.execute("SELECT count(*) FROM contribution_receipts").fetchone()[0]
        == 0
    )
    assert (
        connection.execute("SELECT expires_at_ms FROM run_leases").fetchone()[0]
        == 31000
    )
    connection.execute("DROP TRIGGER expire_after_link")
    with transaction(connection):
        committed = commit_checkpoint(
            connection,
            claim,
            stage="researching",
            contributions=(contribution,),
            receipts=(receipt,),
        )
        accepted = read_committed_work(connection, claim.run_id)
    assert committed.last_event_sequence == 3
    assert len(accepted.contributions) == 1
    assert len(accepted.receipts) == 1


def test_other_connection_sees_checkpoint_only_after_commit(
    database: DatabaseSettings,
    connection: sqlite3.Connection,
    admitted: RunSnapshot,
    claim: LeaseClaim,
) -> None:
    with session(database) as observer:
        with transaction(connection):
            pending = commit_checkpoint(connection, claim, stage="researching")
            observer.execute("BEGIN")
            before = read_snapshot(
                observer, search_id=admitted.search_id, run_id=admitted.run_id
            )
            before_events = read_events(
                observer, claim.run_id, after_sequence=0, limit=10
            )
            observer.execute("COMMIT")
        with transaction(observer):
            after = read_snapshot(
                observer, search_id=admitted.search_id, run_id=admitted.run_id
            )
            after_events = read_events(
                observer, claim.run_id, after_sequence=0, limit=10
            )
    assert pending.last_event_sequence == 3
    assert before.state.model_dump() == {"status": "running", "stage": "starting"}
    assert before.last_event_sequence == 2
    assert [event.sequence for event in before_events.events] == [1, 2]
    assert after.state.model_dump() == {"status": "running", "stage": "researching"}
    assert after.last_event_sequence == 3
    assert [
        (event.sequence, event.payload.model_dump()) for event in after_events.events
    ] == [
        (1, {"kind": "queued"}),
        (2, {"kind": "progress", "stage": "starting"}),
        (3, {"kind": "progress", "stage": "researching"}),
    ]


def test_completion_persists_terminal_event_and_rejects_further_worker_writes(
    connection: sqlite3.Connection, claim: LeaseClaim
) -> None:
    outcome = InsufficientCoverageOutcome(
        gaps=("No research in this controlled case.",)
    )
    with transaction(connection):
        completed = complete_without_result(connection, claim, outcome=outcome)
    assert completed.state.model_dump() == {
        "status": "completed",
        "outcome": {
            "kind": "insufficient_coverage",
            "gaps": ("No research in this controlled case.",),
        },
    }
    for operation in ("renew", "checkpoint", "complete", "claim"):
        with (
            pytest.raises(RunNotClaimable if operation == "claim" else LeaseLost),
            transaction(connection),
        ):
            if operation == "renew":
                renew_lease(connection, claim, 30)
            elif operation == "checkpoint":
                commit_checkpoint(connection, claim, stage="publishing")
            elif operation == "complete":
                complete_without_result(connection, claim, outcome=outcome)
            else:
                claim_run(connection, claim.run_id, "worker-b", 30)
    with transaction(connection):
        events = read_events(connection, claim.run_id, after_sequence=2, limit=10)
    assert [
        (event.sequence, event.payload.model_dump()) for event in events.events
    ] == [
        (
            3,
            {
                "kind": "completed",
                "outcome": {
                    "kind": "insufficient_coverage",
                    "gaps": ("No research in this controlled case.",),
                },
            },
        )
    ]
    assert events.events[0].result_version_id is None


def test_completion_expiry_fence_does_not_publish_a_terminal_event(
    connection: sqlite3.Connection, claim: LeaseClaim
) -> None:
    connection.execute(
        "CREATE TEMP TRIGGER expire_at_completion AFTER INSERT ON run_events "
        "WHEN NEW.sequence = 3 BEGIN UPDATE run_leases SET expires_at_ms = 1000 "
        "WHERE run_id = NEW.run_id; END"
    )
    outcome = InsufficientCoverageOutcome(gaps=("Controlled completion.",))
    with pytest.raises(LeaseLost, match="lease_lost"), transaction(connection):
        complete_without_result(connection, claim, outcome=outcome)
    with transaction(connection):
        work = read_committed_work(connection, claim.run_id)
    assert work.run.state.model_dump() == {"status": "running", "stage": "starting"}
    assert work.run.last_event_sequence == 2
    assert connection.execute("SELECT count(*) FROM run_events").fetchone()[0] == 2
    connection.execute("DROP TRIGGER expire_at_completion")
    with transaction(connection):
        finished = complete_without_result(connection, claim, outcome=outcome)
    assert finished.state.status == "completed"
    assert finished.last_event_sequence == 3


def test_diagnostic_worker_persists_replayable_terminal_state(
    database: DatabaseSettings, admitted: RunSnapshot, database_clock: None
) -> None:
    completed = worker.execute_run(
        database,
        admitted.run_id,
        worker.diagnostic_job(delay_seconds=0),
        worker_id="diagnostic",
    )
    assert isinstance(completed.state, CompletedState)
    assert completed.state.outcome.model_dump() == {
        "kind": "insufficient_coverage",
        "gaps": ("Diagnostic lifecycle check only. No place research was performed.",),
    }
    with session(database) as opened, transaction(opened):
        replay = read_events(opened, admitted.run_id, after_sequence=2, limit=10)
        durable = read_committed_work(opened, admitted.run_id)
    assert [(e.sequence, e.payload.kind) for e in replay.events] == [
        (3, "progress"),
        (4, "progress"),
        (5, "completed"),
    ]
    assert durable.run.state.status == "completed"
    assert durable.run.last_event_sequence == 5
    assert all(event.result_version_id is None for event in replay.events)


def test_worker_job_failure_preserves_committed_work_and_has_safe_terminal_failure(
    database: DatabaseSettings,
    admitted: RunSnapshot,
    database_clock: None,
    contribution: ContributionRecord,
    receipt: Receipt,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def successful_work() -> worker.CheckpointRecords:
        return worker.CheckpointRecords((contribution,), (receipt,))

    def failing_work() -> worker.CheckpointRecords:
        raise RuntimeError("sensitive-provider-payload")

    job = worker.WorkerJob(
        (
            worker.WorkerStep("researching", successful_work),
            worker.WorkerStep("composing", failing_work),
        ),
        InsufficientCoverageOutcome(gaps=("Unused normal outcome.",)),
    )
    completed = worker.execute_run(
        database, admitted.run_id, job, worker_id="failure-worker"
    )
    assert completed.state.model_dump() == {
        "status": "completed",
        "outcome": {
            "kind": "technical_failure",
            "code": "worker_failure",
            "message": "Worker execution failed.",
            "retryable": True,
        },
    }
    with session(database) as opened, transaction(opened):
        durable = read_committed_work(opened, admitted.run_id)
        events = read_events(opened, admitted.run_id, after_sequence=0, limit=10)
    assert durable.run.last_event_sequence == 4
    assert len(durable.contributions) == 1
    assert len(durable.receipts) == 1
    assert [e.payload.kind for e in events.events] == [
        "queued",
        "progress",
        "progress",
        "completed",
    ]
    assert "sensitive-provider-payload" not in caplog.text


def test_interrupted_worker_remains_recoverable_and_preserves_prior_attempt_work(
    database: DatabaseSettings,
    admitted: RunSnapshot,
    database_clock: None,
    contribution: ContributionRecord,
    receipt: Receipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def successful_work() -> worker.CheckpointRecords:
        return worker.CheckpointRecords((contribution,), (receipt,))

    def interrupted_work() -> worker.CheckpointRecords:
        raise KeyboardInterrupt

    outcome = InsufficientCoverageOutcome(gaps=("Controlled recovery only.",))
    interrupted_job = worker.WorkerJob(
        (
            worker.WorkerStep("researching", successful_work),
            worker.WorkerStep("composing", interrupted_work),
        ),
        outcome,
    )
    with pytest.raises(KeyboardInterrupt):
        worker.execute_run(
            database, admitted.run_id, interrupted_job, worker_id="interrupted"
        )
    with session(database) as opened, transaction(opened):
        interrupted = read_committed_work(opened, admitted.run_id)
    assert interrupted.run.state.model_dump() == {
        "status": "running",
        "stage": "researching",
    }
    assert interrupted.run.last_event_sequence == 3
    assert len(interrupted.contributions) == 1
    with pytest.raises(LeaseBusy, match="lease_busy"):
        worker.execute_run(
            database,
            admitted.run_id,
            worker.diagnostic_job(delay_seconds=0),
            worker_id="too-early",
        )
    monkeypatch.setattr(leases, "_NOW_MS", "31000")
    recovered = worker.execute_run(
        database,
        admitted.run_id,
        worker.WorkerJob((worker.WorkerStep("composing", successful_work),), outcome),
        worker_id="recovered",
    )
    assert recovered.state.status == "completed"
    assert recovered.attempt_id != interrupted.run.attempt_id
    with session(database) as opened, transaction(opened):
        durable = read_committed_work(opened, admitted.run_id)
        events = read_events(opened, admitted.run_id, after_sequence=3, limit=10)
    assert [(e.sequence, e.payload.model_dump()) for e in events.events] == [
        (4, {"kind": "progress", "stage": "recovering"}),
        (5, {"kind": "progress", "stage": "composing"}),
        (
            6,
            {
                "kind": "completed",
                "outcome": {
                    "kind": "insufficient_coverage",
                    "gaps": ("Controlled recovery only.",),
                },
            },
        ),
    ]
    assert len(durable.contributions) == 1
    assert len(durable.receipts) == 1
    assert durable.contributions[0].origin_attempt_id == interrupted.run.attempt_id
    assert durable.contributions[0].accepted_sequence == 3


def test_worker_work_runs_outside_the_database_write_transaction(
    database: DatabaseSettings, admitted: RunSnapshot, database_clock: None
) -> None:
    def independent_write() -> worker.CheckpointRecords:
        with session(database) as opened, transaction(opened):
            issue_session(opened)
        return worker.CheckpointRecords()

    job = worker.WorkerJob(
        (worker.WorkerStep("composing", independent_write),),
        InsufficientCoverageOutcome(gaps=("Controlled case.",)),
    )
    completed = worker.execute_run(
        database, admitted.run_id, job, worker_id="transaction-check"
    )
    assert completed.state.status == "completed"
    with session(database) as opened:
        assert (
            opened.execute("SELECT count(*) FROM anonymous_sessions").fetchone()[0] == 2
        )


@pytest.mark.parametrize(
    ("name", "status", "state", "sample_size"),
    [
        ("accepted_google_empty", "available", "empty", 0),
        ("accepted_google_omitted", "available", "omitted", 0),
        ("accepted_google_present", "available", "present", 1),
        ("accepted_partial_with_usable_complaint", "available", "present", 1),
        ("accepted_google_no_match", "no_match", None, None),
        ("accepted_google_provider_unavailable", "provider_unavailable", None, None),
        ("accepted_google_restricted", "restricted", None, None),
        ("accepted_google_zero_review", "zero_review", None, None),
    ],
)
def test_declared_google_review_outcomes_are_accepted(
    name: str, status: str, state: str | None, sample_size: int | None
) -> None:
    collection = EvidenceCollection.model_validate_json(
        (EXAMPLES / f"{name}.json").read_text()
    )
    assert collection.place_ids == ("branch_a", "branch_b")
    assessment = collection.review_assessments[0]
    assert assessment.place_id == "branch_a"
    assert assessment.google.status == status
    if isinstance(assessment.google, AvailableReviews):
        assert assessment.google.sample.state == state
        assert assessment.google.sample.sample_size == sample_size
        assert assessment.google.sample.complete_history is False


@pytest.mark.parametrize(
    "name",
    ["accepted_shared_source_scoped_menu_and_price", "accepted_stale_but_retainable"],
)
def test_shared_source_keeps_menu_price_and_unknown_claims_scoped_to_their_branch(
    name: str,
) -> None:
    collection = EvidenceCollection.model_validate_json(
        (EXAMPLES / f"{name}.json").read_text()
    )
    claims = [
        claim
        for claim in collection.food_claims
        if not isinstance(claim, UnknownFoodClaim)
    ]
    assert [
        (claim.topic, [(s.place_id, s.evidence_ids) for s in claim.subjects])
        for claim in claims
    ] == [
        ("menu", [("branch_a", ("ev_menu_a",))]),
        ("price", [("branch_b", ("ev_price_b",))]),
        ("food", [("branch_a", ("ev_menu_a",))]),
    ]
    gap = collection.food_claims[3]
    assert isinstance(gap, UnknownFoodClaim)
    assert gap.subject_place_ids == ("branch_a",)
    assert gap.gap == "Current drink prices were not obtained."
    assert "evidence_ids" not in gap.model_dump()


def test_explicit_multiplace_evidence_supports_each_named_subject() -> None:
    collection = EvidenceCollection.model_validate_json(
        (EXAMPLES / "accepted_explicit_multiplace.json").read_text()
    )
    claim = collection.food_claims[-1]
    assert not isinstance(claim, UnknownFoodClaim)
    assert [(subject.place_id, subject.evidence_ids) for subject in claim.subjects] == [
        ("branch_a", ("ev_both",)),
        ("branch_b", ("ev_both",)),
    ]
    assert collection.evidence[-1].subject_place_ids == ("branch_a", "branch_b")


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("accepted_labeled_independent_food_fallback", "food_evidence"),
        ("accepted_labeled_independent_review_fallback", "review_sample"),
    ],
)
def test_independent_fallback_preserves_its_label_and_primary_failure(
    name: str, kind: str
) -> None:
    collection = EvidenceCollection.model_validate_json(
        (EXAMPLES / f"{name}.json").read_text()
    )
    assessment = collection.review_assessments[0]
    assert assessment.google.status == "provider_unavailable"
    fallback = assessment.fallback
    assert isinstance(fallback, IndependentFoodFallback | IndependentReviewFallback)
    assert fallback.kind == kind
    assert fallback.reason == "provider_unavailable"
    assert fallback.source_id == "src_independent_a"
    if isinstance(fallback, IndependentFoodFallback):
        assert fallback.evidence_ids == ("ev_independent_a",)
        assert [
            (s.place_id, s.evidence_ids)
            for finding in fallback.findings
            for s in finding.subjects
        ] == [("branch_a", ("ev_independent_a",))]
    else:
        assert fallback.sample.state == "present"
        assert fallback.sample.evidence_ids == ("ev_independent_a",)
        assert fallback.sample.sample_size == 1


@pytest.mark.parametrize(
    ("name", "message"),
    [
        (
            "rejected_swapped_branch_menu",
            "supporting evidence belongs to another place",
        ),
        (
            "rejected_swapped_branch_price",
            "supporting evidence belongs to another place",
        ),
        (
            "rejected_swapped_branch_complaint",
            "supporting evidence belongs to another place",
        ),
        (
            "rejected_multisubject_union_of_support",
            "supporting evidence belongs to another place",
        ),
        ("rejected_nonexistent_evidence", "unknown supporting evidence"),
        ("rejected_source_id_as_evidence_id", "String should match pattern"),
        (
            "rejected_google_locator_branch_mismatch",
            "Google evidence branch differs from source branch",
        ),
        (
            "rejected_independent_google_identity",
            "independent evidence cannot use a Google locator",
        ),
        (
            "rejected_complaint_outside_declared_sample",
            "finding evidence is outside the assessed sample",
        ),
        (
            "rejected_fallback_reason_mismatch",
            "fallback reason differs from Google primary outcome",
        ),
        (
            "rejected_empty_sample_as_confirmed_zero",
            "confirmed zero reviews uses the zero_review outcome",
        ),
        ("rejected_zero_review_boolean_count", "Input should be a valid integer"),
    ],
)
def test_invalid_evidence_relationships_are_rejected(name: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        EvidenceCollection.model_validate_json((EXAMPLES / f"{name}.json").read_text())
