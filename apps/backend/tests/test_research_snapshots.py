import json
from datetime import UTC, datetime

import pytest
from test_evidence_storage import fixture_collection

from app.core.settings import DatabaseSettings
from app.db.evidence import store_evidence
from app.db.migrations import migrate
from app.db.ownership import create_search, issue_session
from app.db.research_snapshots import (
    FindingHandle,
    SnapshotCandidate,
    SnapshotGap,
    SnapshotSample,
    lookup_snapshot_finding,
    store_snapshot,
    view_snapshot,
)
from app.db.session import session, transaction
from app.schemas.evidence import EvidenceCollection


def test_snapshot_indexes_broader_candidates_and_scoped_retained_findings(tmp_path):
    settings = DatabaseSettings(database_path=tmp_path / "snapshot.sqlite")
    assert migrate(settings) == (1, 2, 3, 4, 5, 6, 7)
    fixture = fixture_collection()
    for item in fixture["evidence"]:
        item["freshness"]["fresh_until"] = "2026-11-30T00:00:00Z"
    fixture["evidence"][1]["statement"] = "B soup was cold"
    fixture["food_claims"][1]["statement"] = "Soup was cold"
    fixture["review_assessments"] = [
        {
            "assessment_id": "assessment_b",
            "place_id": "branch_b",
            "google": {"status": "no_match", "reason": "no matching branch"},
            "fallback": {
                "kind": "food_evidence",
                "source_id": "src_shared",
                "reason": "no_match",
                "evidence_ids": ["ev_b"],
                "findings": [
                    {
                        "topic": "complaint",
                        "statement": "Soup was cold",
                        "subjects": [
                            {"place_id": "branch_b", "evidence_ids": ["ev_b"]}
                        ],
                    }
                ],
            },
        }
    ]
    collection = EvidenceCollection.model_validate_json(json.dumps(fixture))
    with session(settings) as conn:
        with transaction(conn):
            principal = issue_session(conn).principal
            search = create_search(conn, principal)
            other_search = create_search(conn, principal)
            store_evidence(
                conn,
                search,
                collection,
                source_classes={
                    "src_shared": "permitted_independent",
                    "src_google": "public_google_reviews",
                },
            )
            store_snapshot(
                conn,
                search,
                "snap_1",
                candidates=(
                    SnapshotCandidate(
                        "branch_a",
                        "branch",
                        "First",
                        identity_source="geoapify_geography",
                        latitude=14.5,
                        longitude=121.0,
                        timezone="Asia/Manila",
                    ),
                    SnapshotCandidate(
                        "branch_b",
                        "branch",
                        "Second",
                        identity_source="geoapify_geography",
                        latitude=14.6,
                        longitude=121.1,
                        timezone="Asia/Manila",
                    ),
                    SnapshotCandidate("branch_c", "branch"),
                ),
                findings=(
                    FindingHandle("branch_a", "food_a"),
                    FindingHandle("branch_b", "fallback_assessment_b_0"),
                ),
                gaps=(
                    SnapshotGap("branch_a", "restricted_source"),
                    SnapshotGap("branch_c", "missing_menu"),
                ),
                samples=(
                    SnapshotSample(
                        "branch_b", "src_shared", 1, 1, 24, "source_selected", True
                    ),
                ),
            )
        first = view_snapshot(conn, search, "snap_1", limit=1)
        assert first.candidates == (
            SnapshotCandidate(
                "branch_a",
                "branch",
                "First",
                identity_source="geoapify_geography",
                latitude=14.5,
                longitude=121.0,
                timezone="Asia/Manila",
            ),
        )
        assert first.deferred_candidates == 2
        assert first.selected == (FindingHandle("branch_a", "food_a"),)
        assert first.deferred_count == 1
        assert first.gaps == (SnapshotGap("branch_a", "restricted_source"),)
        second = view_snapshot(
            conn,
            search,
            "snap_1",
            limit=1,
            after=first.next_after,
            candidate_after=first.next_candidate_after,
        )
        assert second.candidates == (
            SnapshotCandidate(
                "branch_b",
                "branch",
                "Second",
                identity_source="geoapify_geography",
                latitude=14.6,
                longitude=121.1,
                timezone="Asia/Manila",
            ),
        )
        assert second.selected == (
            FindingHandle("branch_b", "fallback_assessment_b_0"),
        )
        assert second.deferred_count == 0
        assert second.samples == (
            SnapshotSample("branch_b", "src_shared", 1, 1, 24, "source_selected", True),
        )
        third = view_snapshot(
            conn, search, "snap_1", limit=1, candidate_after=second.next_candidate_after
        )
        assert third.candidates == (SnapshotCandidate("branch_c", "branch"),)
        assert third.gaps == (
            SnapshotGap("branch_c", "candidate_reacquisition_required"),
            SnapshotGap("branch_c", "missing_menu"),
        )
        now = datetime(2026, 10, 30, tzinfo=UTC)
        assert (
            lookup_snapshot_finding(
                conn, search, "snap_1", first.selected[0], now=now
            ).finding.statement
            == "Noodles"
        )
        assert (
            lookup_snapshot_finding(
                conn, search, "snap_1", second.selected[0], now=now
            ).finding.statement
            == "Soup was cold"
        )
        stale = lookup_snapshot_finding(
            conn,
            search,
            "snap_1",
            second.selected[0],
            now=datetime(2026, 11, 30, tzinfo=UTC),
        )
        assert stale.state == "stale" and stale.finding is None
        assert view_snapshot(
            conn, search, "snap_1", limit=5, place_id="branch_b"
        ).selected == (FindingHandle("branch_b", "fallback_assessment_b_0"),)
        with pytest.raises(ValueError, match="unknown snapshot"):
            view_snapshot(conn, other_search, "snap_1", limit=1)
        with pytest.raises(ValueError, match="unknown snapshot finding"):
            lookup_snapshot_finding(
                conn, search, "snap_1", FindingHandle("branch_b", "food_a"), now=now
            )
        with pytest.raises(ValueError, match="not retained"), transaction(conn):
            store_snapshot(
                conn,
                search,
                "snap_bad",
                candidates=(SnapshotCandidate("branch_a", "branch"),),
                findings=(FindingHandle("branch_a", "food_restricted"),),
            )
        with (
            pytest.raises(ValueError, match="sample metadata is not retainable"),
            transaction(conn),
        ):
            store_snapshot(
                conn,
                search,
                "snap_restricted_sample",
                candidates=(SnapshotCandidate("branch_a", "branch"),),
                findings=(),
                samples=(
                    SnapshotSample(
                        "branch_a", "src_google", 1, 1, None, "source_selected", True
                    ),
                ),
            )
        with (
            pytest.raises(
                ValueError, match="unavailable candidate cannot retain identity content"
            ),
            transaction(conn),
        ):
            store_snapshot(
                conn,
                search,
                "snap_unsafe_identity",
                candidates=(
                    SnapshotCandidate("branch_a", "branch", "SECRET REVIEW TEXT"),
                ),
                findings=(),
            )
        dump = "\n".join(conn.iterdump())
        assert "SECRET REVIEW TEXT" not in dump
        assert "SECRET DERIVATIVE" not in dump
        assert "SECRET AUTHOR" not in dump
        assert "0x1:0x2" in dump
