import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db.events import read_events
from app.db.leases import LeaseLost
from app.db.ownership import OwnedSearch
from app.db.result_versions import (
    EphemeralResult,
    ResultItem,
    ResultUnavailable,
    StorableResult,
    StoredResult,
    publish_ephemeral_result,
    publish_result,
    read_result,
)
from app.db.runs import RunNotFound
from app.db.session import transaction
from app.schemas.evidence import EvidenceCollection

pytest_plugins = ["test_foundation"]


def input_data():
    path = Path("../../docs/examples/recommendation-contract/accepted_full_set.json")
    data = json.loads(path.read_text())["snapshots"][0]["evidence"]
    data["sources"][0]["policy"]["derived_output"]["decision"] = "allowed"
    data["sources"][0]["policy"]["display"]["decision"] = "allowed"
    for field in ("derived_content", "raw_content", "identifiers"):
        data["sources"][0]["retention"][field] = {
            "mode": "indefinite",
            "basis": "Synthetic permission.",
        }
    for item in data["evidence"]:
        item["freshness"]["fresh_until"] = "2026-10-26T05:00:00Z"
    evidence = EvidenceCollection.model_validate_json(json.dumps(data))
    result = StorableResult(
        version_id="ver_test",
        snapshot_id="snap_test",
        primaries=(
            ResultItem(
                place_id="branch_a",
                explanation="A menu choice.",
                evidence_ids=("ev_menu_a",),
                route="unconfirmed",
            ),
        ),
        runners_up=(),
        selection_evidence_ids=tuple(item.evidence_id for item in evidence.evidence),
    )
    return result, evidence


def prepare(connection, admitted):
    with transaction(connection):
        connection.execute(
            "INSERT INTO research_snapshots VALUES (?, ?)",
            (admitted.search_id, "snap_test"),
        )
        connection.execute(
            "INSERT INTO research_candidates(search_id, snapshot_id, place_id, kind, identity_source) VALUES (?, ?, ?, ?, ?)",
            (
                admitted.search_id,
                "snap_test",
                "branch_a",
                "branch",
                "reacquisition_required",
            ),
        )


def publish(connection, claim, result, evidence):
    return publish_result(
        connection,
        claim,
        result=result,
        evidence=evidence,
        source_classes={"src_group": "permitted_independent"},
        now=datetime(2026, 10, 25, 5, tzinfo=UTC),
        partial=True,
    )


def test_result_and_event_commit_together(connection, admitted, claim):
    result, evidence = input_data()
    prepare(connection, admitted)
    with transaction(connection):
        completed = publish(connection, claim, result, evidence)
    with transaction(connection):
        events = read_events(connection, claim.run_id, after_sequence=2, limit=10)
        stored = connection.execute(
            "SELECT content_json FROM result_versions"
        ).fetchone()[0]
    assert StoredResult.model_validate_json(stored).result == result
    assert "Synthetic" not in stored
    assert "https://example.invalid/menu" in stored
    assert completed.state.outcome.version_id == "ver_test"
    assert events.events[0].result_version_id == "ver_test"
    assert events.events[0].payload.outcome == completed.state.outcome
    with pytest.raises(sqlite3.IntegrityError, match="immutable_result_version"):
        connection.execute("UPDATE result_versions SET content_json='{}'")


def test_read_is_search_scoped_and_checks_source_freshness(connection, admitted, claim):
    result, evidence = input_data()
    prepare(connection, admitted)
    with transaction(connection):
        publish(connection, claim, result, evidence)
    with transaction(connection):
        saved = read_result(
            connection,
            OwnedSearch(admitted.search_id),
            run_id=claim.run_id,
            now=datetime(2026, 10, 25, 5, tzinfo=UTC),
        )
    assert saved is not None
    assert saved.result.primaries[0].explanation == "A menu choice."
    assert saved.sources[0].source_url == "https://example.invalid/menu"
    with pytest.raises(RunNotFound), transaction(connection):
        read_result(
            connection,
            OwnedSearch("other-search"),
            run_id=claim.run_id,
            now=datetime(2026, 10, 25, 5, tzinfo=UTC),
        )
    with pytest.raises(ResultUnavailable), transaction(connection):
        read_result(
            connection,
            OwnedSearch(admitted.search_id),
            run_id=claim.run_id,
            now=datetime(2026, 10, 26, 5, tzinfo=UTC),
        )


def test_restricted_or_incomplete_lineage_is_never_stored(connection, admitted, claim):
    result, evidence = input_data()
    prepare(connection, admitted)
    restricted = evidence.model_dump(mode="json")
    restricted["sources"][0]["retention"]["derived_content"] = {
        "mode": "ephemeral",
        "basis": "Restricted.",
    }
    with pytest.raises(ValueError, match="not permitted"), transaction(connection):
        publish(
            connection,
            claim,
            result,
            EvidenceCollection.model_validate_json(json.dumps(restricted)),
        )
    with pytest.raises(ValueError, match="lineage"), transaction(connection):
        publish(
            connection,
            claim,
            result.model_copy(update={"selection_evidence_ids": ("ev_menu_a",)}),
            evidence,
        )
    assert connection.execute("SELECT count(*) FROM result_versions").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM run_events").fetchone()[0] == 2


def test_reference_retention_and_display_permission_are_checked(
    connection, admitted, claim
):
    result, evidence = input_data()
    prepare(connection, admitted)
    unsafe = evidence.model_dump(mode="json")
    unsafe["sources"][0]["retention"]["identifiers"] = {
        "mode": "ephemeral",
        "basis": "Restricted identifiers.",
    }
    with pytest.raises(ValueError, match="reference storage"), transaction(connection):
        publish(
            connection,
            claim,
            result,
            EvidenceCollection.model_validate_json(json.dumps(unsafe)),
        )
    hidden = evidence.model_dump(mode="json")
    hidden["sources"][0]["policy"]["display"]["decision"] = "prohibited"
    with pytest.raises(ValueError, match="display"), transaction(connection):
        publish(
            connection,
            claim,
            result,
            EvidenceCollection.model_validate_json(json.dumps(hidden)),
        )
    assert connection.execute("SELECT count(*) FROM result_versions").fetchone()[0] == 0


def test_wrong_branch_and_unpermitted_source_class_are_rejected(
    connection, admitted, claim
):
    result, evidence = input_data()
    prepare(connection, admitted)
    wrong = result.model_copy(
        update={
            "primaries": (
                result.primaries[0].model_copy(update={"evidence_ids": ("ev_menu_b",)}),
            )
        }
    )
    with pytest.raises(ValueError, match="another place"), transaction(connection):
        publish(connection, claim, wrong, evidence)
    with pytest.raises(ValueError, match="not permitted"), transaction(connection):
        publish_result(
            connection,
            claim,
            result=result,
            evidence=evidence,
            source_classes={"src_group": "public_google_reviews"},
            now=datetime(2026, 10, 25, 5, tzinfo=UTC),
            partial=True,
        )
    assert connection.execute("SELECT count(*) FROM result_versions").fetchone()[0] == 0


def test_expired_lease_rolls_back_result_and_event(connection, admitted, claim):
    result, evidence = input_data()
    prepare(connection, admitted)
    connection.execute(
        "CREATE TEMP TRIGGER expire_after_version AFTER INSERT ON result_versions "
        "BEGIN UPDATE run_leases SET expires_at_ms=1000 WHERE run_id=NEW.run_id; END"
    )
    with pytest.raises(LeaseLost), transaction(connection):
        publish(connection, claim, result, evidence)
    assert connection.execute("SELECT count(*) FROM result_versions").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM run_events").fetchone()[0] == 2


def test_ephemeral_marker_commits_without_review_content(connection, admitted, claim):
    prepare(connection, admitted)
    marker = EphemeralResult(version_id="ver_123", snapshot_id="snap_test")
    with pytest.raises(ValidationError):
        EphemeralResult.model_validate(
            {
                "version_id": "ver_123",
                "snapshot_id": "snap_test",
                "review": "private_review_sentinel",
            }
        )
    with transaction(connection):
        completed = publish_ephemeral_result(connection, claim, result=marker)
    with transaction(connection):
        saved = read_result(
            connection,
            OwnedSearch(admitted.search_id),
            run_id=claim.run_id,
            now=datetime(2026, 10, 25, 5, tzinfo=UTC),
        )
        events = read_events(connection, claim.run_id, after_sequence=2, limit=10)
    assert saved == marker
    assert completed.state.outcome.kind == "partial"
    assert completed.state.outcome.version_id == "ver_123"
    assert events.events[0].result_version_id == "ver_123"
    stored = connection.execute("SELECT content_json FROM result_versions").fetchone()[
        0
    ]
    assert "private_review_sentinel" not in stored
    assert json.loads(stored) == {
        "kind": "ephemeral",
        "version_id": "ver_123",
        "snapshot_id": "snap_test",
    }
    with pytest.raises(RunNotFound), transaction(connection):
        read_result(
            connection,
            OwnedSearch("other-search"),
            run_id=claim.run_id,
            now=datetime(2026, 10, 25, 5, tzinfo=UTC),
        )


def test_ephemeral_marker_fences_lease_and_rolls_back(connection, admitted, claim):
    prepare(connection, admitted)
    connection.execute(
        "CREATE TEMP TRIGGER expire_ephemeral AFTER INSERT ON result_versions "
        "BEGIN UPDATE run_leases SET expires_at_ms=1000 WHERE run_id=NEW.run_id; END"
    )
    with pytest.raises(LeaseLost), transaction(connection):
        publish_ephemeral_result(
            connection,
            claim,
            result=EphemeralResult(version_id="ver_123", snapshot_id="snap_test"),
        )
    assert connection.execute("SELECT count(*) FROM result_versions").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM run_events").fetchone()[0] == 2


def test_ephemeral_marker_requires_owned_snapshot_and_running_state(
    connection, admitted, claim
):
    prepare(connection, admitted)
    with pytest.raises(ValueError, match="not owned"), transaction(connection):
        publish_ephemeral_result(
            connection,
            claim,
            result=EphemeralResult(version_id="ver_123", snapshot_id="snap_other"),
        )
    current = connection.execute(
        "SELECT snapshot_json FROM runs WHERE run_id=?", (claim.run_id,)
    ).fetchone()[0]
    data = json.loads(current)
    data["state"] = {"status": "cancelling"}
    connection.execute(
        "UPDATE runs SET snapshot_json=? WHERE run_id=?",
        (json.dumps(data), claim.run_id),
    )
    with pytest.raises(LeaseLost), transaction(connection):
        publish_ephemeral_result(
            connection,
            claim,
            result=EphemeralResult(version_id="ver_123", snapshot_id="snap_test"),
        )
    assert connection.execute("SELECT count(*) FROM result_versions").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM run_events").fetchone()[0] == 2
