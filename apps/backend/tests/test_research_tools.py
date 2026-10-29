import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from test_evidence_storage import fixture_collection

from app.core.settings import DatabaseSettings
from app.db.evidence import store_evidence
from app.db.migrations import migrate
from app.db.ownership import create_search, issue_session
from app.db.research_snapshots import FindingHandle, SnapshotCandidate, store_snapshot
from app.db.session import session, transaction
from app.research_tools import (
    LookupInput,
    PageInput,
    ResearchTools,
    SamplePageInput,
    TextInput,
)
from app.schemas.evidence import EvidenceCollection


@pytest.fixture
def setup(tmp_path):
    settings = DatabaseSettings(database_path=tmp_path / "research.sqlite")
    migrate(settings)
    raw = fixture_collection()
    for item in raw["evidence"]:
        item["freshness"]["fresh_until"] = "2026-11-30T00:00:00Z"
    raw["evidence"][-1]["statement"] = (
        "The chicken was excellent, but service was slow. 私はまた行きたい。"
    )
    for i in range(4):
        extra = dict(raw["evidence"][-1])
        extra["evidence_id"] = f"ev_google_{i}"
        extra["locator"] = dict(extra["locator"], review_id=f"review_{i}")
        raw["evidence"].append(extra)
    raw["review_assessments"] = [
        {
            "assessment_id": "review_a",
            "place_id": "branch_a",
            "google": {
                "status": "available",
                "source_id": "src_google",
                "sample": {
                    "state": "present",
                    "evidence_ids": [
                        "ev_google",
                        "ev_google_0",
                        "ev_google_1",
                        "ev_google_2",
                    ],
                    "sample_size": 4,
                    "assessed_evidence_ids": ["ev_google"],
                    "assessed_count": 1,
                    "reported_total": 50,
                    "reported_total_basis": "provider count",
                    "selection_method": "provider selected",
                    "requested_order": "relevance",
                    "observed_order": "provider order",
                    "bounds": {"max_reviews": 4},
                    "partial": True,
                    "qualification": "Only part of the sample was assessed.",
                    "omissions": ["Three reviews remain unassessed."],
                },
            },
            "findings": [],
        }
    ]
    collection = EvidenceCollection.model_validate_json(json.dumps(raw))
    with session(settings) as conn:
        with transaction(conn):
            principal = issue_session(conn).principal
            search = create_search(conn, principal)
            other = create_search(conn, principal)
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
                "snap",
                candidates=(
                    SnapshotCandidate("branch_a", "branch"),
                    SnapshotCandidate("branch_b", "branch"),
                ),
                findings=(
                    FindingHandle("branch_a", "food_a"),
                    FindingHandle("branch_a", "food_both"),
                    FindingHandle("branch_b", "food_b"),
                ),
            )
        yield conn, search, other, collection


def tools(setup, **kwargs):
    conn, search, _, collection = setup
    return ResearchTools(
        conn,
        search,
        "snap",
        place_ids=("branch_a", "branch_b"),
        clock=lambda: datetime(2026, 10, 30, tzinfo=UTC),
        max_content_bytes=1024,
        ephemeral=collection,
        source_classes={"src_google": "public_google_reviews"},
        **kwargs,
    )


def test_scoped_page_and_read_of_approved_evidence(setup):
    research = tools(setup)
    first = research.list_ephemeral_evidence(PageInput(place_id="branch_a", limit=2))
    assert first == {
        "status": "ok",
        "place_id": "branch_a",
        "evidence_ids": ["ev_google", "ev_google_0"],
        "deferred_count": 3,
        "restricted_count": 2,
        "next_after": "ev_google_0",
    }
    second = research.list_ephemeral_evidence(
        PageInput(place_id="branch_a", after=first["next_after"], limit=2)
    )
    assert second["evidence_ids"] == ["ev_google_1", "ev_google_2"]
    assert second["deferred_count"] == 1
    result = research.read_ephemeral_evidence(
        TextInput(place_id="branch_a", item_id="ev_google")
    )
    assert result["status"] == "available"
    assert result["source_id"] == "src_google"
    context = research.read_review_context(
        LookupInput(place_id="branch_a", item_id="review_a")
    )
    assert context == {
        "status": "available",
        "source_id": "src_google",
        "sample_state": "present",
        "sample_size": 4,
        "assessed_count": 1,
        "reported_total": 50,
        "reported_total_basis": "provider count",
        "max_reviews": 4,
        "max_pages": None,
        "selection_method": "provider selected",
        "requested_order": "relevance",
        "observed_order": "provider order",
        "partial": True,
        "qualification": "Only part of the sample was assessed.",
        "omissions": ("Three reviews remain unassessed.",),
        "complete_history": False,
    }
    assert research.read_review_context(
        LookupInput(place_id="branch_b", item_id="review_a")
    ) == {"status": "unavailable"}
    assert (
        result["statement"]
        == "The chicken was excellent, but service was slow. 私はまた行きたい。"
    )
    assert research.read_ephemeral_evidence(
        TextInput(place_id="branch_b", item_id="ev_google")
    ) == {"status": "unavailable"}
    assert research.read_ephemeral_evidence(
        TextInput(place_id="branch_a", item_id="ev_a")
    ) == {"status": "restricted"}
    first_finding = research.list_findings(PageInput(place_id="branch_a", limit=1))
    handle = first_finding["finding_handles"][0]
    assert handle != "food_a" and first_finding["deferred_count"] == 1
    assert research.read_finding(LookupInput(place_id="branch_a", item_id=handle)) == {
        "status": "restricted"
    }
    assert research.read_finding(LookupInput(place_id="branch_b", item_id=handle)) == {
        "status": "unavailable"
    }
    second_finding = research.list_findings(
        PageInput(place_id="branch_a", after=handle, limit=1)
    )
    assert second_finding["deferred_count"] == 0
    assert second_finding["finding_handles"][0] != handle
    sample = research.list_sample_evidence(
        SamplePageInput(place_id="branch_a", assessment_id="review_a", limit=2)
    )
    assert sample["evidence"] == [
        {"evidence_id": "ev_google", "assessed": True},
        {"evidence_id": "ev_google_0", "assessed": False},
    ]
    assert sample["deferred_count"] == 2
    last = research.list_sample_evidence(
        SamplePageInput(
            place_id="branch_a",
            assessment_id="review_a",
            after=sample["next_after"],
            limit=2,
        )
    )
    assert last["evidence"] == [
        {"evidence_id": "ev_google_1", "assessed": False},
        {"evidence_id": "ev_google_2", "assessed": False},
    ]
    assert last["deferred_count"] == 0
    assert research.list_sample_evidence(
        SamplePageInput(place_id="branch_b", assessment_id="review_a")
    ) == {"status": "unavailable"}


def test_expiry_policy_size_and_search_scope(setup):
    conn, search, other, collection = setup
    with pytest.raises(ValueError, match="unknown snapshot"):
        ResearchTools(
            conn,
            other,
            "snap",
            place_ids=("branch_a",),
            clock=lambda: datetime.now(UTC),
            max_content_bytes=1024,
        )
    expired = ResearchTools(
        conn,
        search,
        "snap",
        place_ids=("branch_a", "branch_b"),
        clock=lambda: datetime(2026, 12, 1, tzinfo=UTC),
        max_content_bytes=1024,
        ephemeral=collection,
        source_classes={"src_google": "public_google_reviews"},
    )
    assert expired.read_ephemeral_evidence(
        TextInput(place_id="branch_a", item_id="ev_google")
    ) == {"status": "stale"}
    denied = ResearchTools(
        conn,
        search,
        "snap",
        place_ids=("branch_a", "branch_b"),
        clock=lambda: datetime(2026, 10, 30, tzinfo=UTC),
        max_content_bytes=1024,
        ephemeral=collection,
    )
    assert denied.read_ephemeral_evidence(
        TextInput(place_id="branch_a", item_id="ev_google")
    ) == {"status": "restricted"}
    assert (
        denied.list_ephemeral_evidence(PageInput(place_id="branch_a"))["evidence_ids"]
        == []
    )
    raw = collection.model_dump()
    raw["evidence"] = tuple(
        item.model_copy(update={"statement": "長" * 2000})
        if item.evidence_id == "ev_google"
        else item
        for item in collection.evidence
    )
    oversized = ResearchTools(
        conn,
        search,
        "snap",
        place_ids=("branch_a", "branch_b"),
        clock=lambda: datetime(2026, 10, 30, tzinfo=UTC),
        max_content_bytes=1024,
        ephemeral=EvidenceCollection.model_validate(raw),
        source_classes={"src_google": "public_google_reviews"},
    )
    offset = 0
    chunks = []
    while True:
        part = oversized.read_ephemeral_evidence(
            TextInput(place_id="branch_a", item_id="ev_google", offset=offset)
        )
        assert part["status"] == "available"
        assert (
            len(
                json.dumps(part, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            <= 1024
        )
        chunks.append(part["statement"])
        if part["next_offset"] is None:
            assert part["omitted_characters"] == 0
            break
        assert part["omitted_characters"] > 0 and part["next_offset"] > offset
        offset = part["next_offset"]
    assert "".join(chunks) == "長" * 2000


def test_input_bounds():
    for args in (
        {"place_id": "branch_a", "limit": True},
        {"place_id": "../branch", "limit": 1},
        {"place_id": "branch_a", "limit": 21},
        {"place_id": "branch_a", "search_id": "elsewhere"},
    ):
        with pytest.raises(ValidationError):
            PageInput(**args)


def test_constructed_input_and_collection_are_revalidated(setup):
    research = tools(setup)
    with pytest.raises(ValidationError):
        research.list_ephemeral_evidence(
            PageInput.model_construct(place_id="branch_a", limit=True)
        )
    conn, search, _, collection = setup
    invalid = collection.model_copy(update={"place_ids": ("other_branch",)})
    with pytest.raises(ValidationError):
        ResearchTools(
            conn,
            search,
            "snap",
            place_ids=("branch_a", "branch_b"),
            clock=lambda: datetime(2026, 10, 30, tzinfo=UTC),
            max_content_bytes=1024,
            ephemeral=invalid,
        )


def test_clock_is_read_each_time_for_stored_and_ephemeral(setup):
    conn, search, _, collection = setup
    now = [datetime(2026, 10, 30, tzinfo=UTC)]
    research = ResearchTools(
        conn,
        search,
        "snap",
        place_ids=("branch_a", "branch_b"),
        clock=lambda: now[0],
        max_content_bytes=1024,
        ephemeral=collection,
        source_classes={"src_google": "public_google_reviews"},
    )
    handle = research.list_findings(PageInput(place_id="branch_a", limit=1))[
        "finding_handles"
    ][0]
    assert research.read_finding(LookupInput(place_id="branch_a", item_id=handle)) == {
        "status": "restricted"
    }
    now[0] = datetime(2026, 12, 1, tzinfo=UTC)
    assert research.read_finding(LookupInput(place_id="branch_a", item_id=handle)) == {
        "status": "stale"
    }
    assert research.read_ephemeral_evidence(
        TextInput(place_id="branch_a", item_id="ev_google")
    ) == {"status": "stale"}


def test_missing_freshness_returns_unknown_without_content(setup):
    conn, search, _, collection = setup
    raw = collection.model_dump()
    raw["evidence"] = tuple(
        item.model_copy(
            update={
                "freshness": item.freshness.model_copy(update={"fresh_until": None})
            }
        )
        if item.evidence_id == "ev_google"
        else item
        for item in collection.evidence
    )
    research = ResearchTools(
        conn,
        search,
        "snap",
        place_ids=("branch_a", "branch_b"),
        clock=lambda: datetime(2026, 10, 30, tzinfo=UTC),
        max_content_bytes=1024,
        ephemeral=EvidenceCollection.model_validate(raw),
        source_classes={"src_google": "public_google_reviews"},
    )
    assert research.read_ephemeral_evidence(
        TextInput(place_id="branch_a", item_id="ev_google")
    ) == {"status": "freshness_unknown"}
    assert research.read_review_context(
        LookupInput(place_id="branch_a", item_id="review_a")
    ) == {"status": "freshness_unknown"}
    handle = research.list_findings(PageInput(place_id="branch_a", limit=1))[
        "finding_handles"
    ][0]
    conn.execute(
        "UPDATE evidence_items SET content_json=json_set(content_json, '$.fresh_until', NULL) WHERE search_id=? AND evidence_id='ev_a'",
        (search.search_id,),
    )
    assert research.read_finding(LookupInput(place_id="branch_a", item_id=handle)) == {
        "status": "freshness_unknown"
    }
