import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.core.settings import DatabaseSettings
from app.db.evidence import lookup_evidence, store_evidence
from app.db.migrations import migrate
from app.db.ownership import create_search, issue_session
from app.db.session import session, transaction
from app.schemas.evidence import EvidenceCollection


def fixture_collection():
    allow = {"decision": "allowed", "basis": "synthetic permission"}
    durable = {"mode": "indefinite", "basis": "synthetic permission"}
    return {
        "place_ids": ["branch_a", "branch_b"],
        "sources": [
            {
                "source_id": "src_shared",
                "subject_place_ids": ["branch_a", "branch_b"],
                "publisher": "Example",
                "source_url": "https://example.org/menu",
                "origin": {"kind": "independent", "access_method": "fixture"},
                "policy": {
                    key: allow
                    for key in [
                        "access",
                        "display",
                        "model_input",
                        "derived_output",
                        "tracing",
                    ]
                },
                "retention": {
                    key: durable
                    for key in ["raw_content", "derived_content", "identifiers"]
                },
            },
            {
                "source_id": "src_google",
                "subject_place_ids": ["branch_a"],
                "publisher": "SECRET AUTHOR",
                "source_url": "https://google.com/SECRET_REVIEW",
                "origin": {
                    "kind": "google",
                    "branch": {"feature_id": "0x1:0x2"},
                    "access_method": "fixture",
                },
                "policy": {
                    key: allow
                    for key in [
                        "access",
                        "display",
                        "model_input",
                        "derived_output",
                        "tracing",
                    ]
                },
                "retention": {
                    key: durable
                    for key in ["raw_content", "derived_content", "identifiers"]
                },
            },
        ],
        "evidence": [
            {
                "evidence_id": "ev_a",
                "source_id": "src_shared",
                "subject_place_ids": ["branch_a"],
                "statement": "A serves noodles",
                "locator": {"kind": "document", "section": "A menu"},
                "freshness": {
                    "retrieved_at": "2026-10-29T00:00:00Z",
                    "basis": "fixture",
                },
            },
            {
                "evidence_id": "ev_b",
                "source_id": "src_shared",
                "subject_place_ids": ["branch_b"],
                "statement": "B serves soup",
                "locator": {"kind": "document", "section": "B menu"},
                "freshness": {
                    "retrieved_at": "2026-10-29T00:00:00Z",
                    "basis": "fixture",
                },
            },
            {
                "evidence_id": "ev_shared",
                "source_id": "src_shared",
                "subject_place_ids": ["branch_a", "branch_b"],
                "statement": "Both branches offer tea",
                "locator": {"kind": "document"},
                "freshness": {
                    "retrieved_at": "2026-10-29T00:00:00Z",
                    "basis": "fixture",
                },
            },
            {
                "evidence_id": "ev_google",
                "source_id": "src_google",
                "subject_place_ids": ["branch_a"],
                "statement": "SECRET REVIEW TEXT",
                "locator": {
                    "kind": "google_review",
                    "branch": {"feature_id": "0x1:0x2"},
                    "review_id": "secret",
                    "source_url": "https://google.com/SECRET_REVIEW",
                    "link_basis": "review",
                },
                "freshness": {
                    "retrieved_at": "2026-10-29T00:00:00Z",
                    "basis": "fixture",
                },
            },
        ],
        "food_claims": [
            {
                "claim_id": "a",
                "status": "sourced",
                "topic": "menu",
                "statement": "Noodles",
                "subjects": [{"place_id": "branch_a", "evidence_ids": ["ev_a"]}],
            },
            {
                "claim_id": "b",
                "status": "sourced",
                "topic": "menu",
                "statement": "Soup",
                "subjects": [{"place_id": "branch_b", "evidence_ids": ["ev_b"]}],
            },
            {
                "claim_id": "both",
                "status": "sourced",
                "topic": "food",
                "statement": "Tea",
                "subjects": [
                    {"place_id": "branch_a", "evidence_ids": ["ev_shared"]},
                    {"place_id": "branch_b", "evidence_ids": ["ev_shared"]},
                ],
            },
            {
                "claim_id": "restricted",
                "status": "sourced",
                "topic": "food",
                "statement": "SECRET DERIVATIVE",
                "subjects": [{"place_id": "branch_a", "evidence_ids": ["ev_google"]}],
            },
        ],
    }


def test_branch_scoped_evidence_and_restricted_content(tmp_path):
    settings = DatabaseSettings(database_path=tmp_path / "data.sqlite")
    migrate(settings)
    collection = EvidenceCollection.model_validate_json(
        json.dumps(fixture_collection())
    )
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
        a = lookup_evidence(conn, search, "branch_a")
        b = lookup_evidence(conn, search, "branch_b")
        assert [(e.evidence_id, e.statement) for e in a.evidence] == [
            ("ev_a", "A serves noodles"),
            ("ev_shared", "Both branches offer tea"),
        ]
        assert [(e.evidence_id, e.statement) for e in b.evidence] == [
            ("ev_b", "B serves soup"),
            ("ev_shared", "Both branches offer tea"),
        ]
        assert [(f.statement, f.evidence_ids) for f in a.findings] == [
            ("Noodles", ("ev_a",)),
            ("Tea", ("ev_shared",)),
        ]
        assert [(f.statement, f.evidence_ids) for f in b.findings] == [
            ("Soup", ("ev_b",)),
            ("Tea", ("ev_shared",)),
        ]
        assert a.findings[1].supports == (
            ("branch_a", "ev_shared"),
            ("branch_b", "ev_shared"),
        )
        assert a.sources[0].metadata == {"feature_id": "0x1:0x2"}
        assert lookup_evidence(conn, other, "branch_a").sources == ()
        assert lookup_evidence(conn, search, "unknown").evidence == ()
        dump = "\n".join(conn.iterdump())
        assert "SECRET" not in dump


def test_swapped_subject_support_is_rejected():
    data = fixture_collection()
    data["food_claims"][0]["subjects"][0]["evidence_ids"] = ["ev_b"]
    with pytest.raises(
        ValidationError, match="supporting evidence belongs to another place"
    ):
        EvidenceCollection.model_validate_json(json.dumps(data))


def test_unknown_or_expiring_source_is_not_stored(tmp_path):
    data = fixture_collection()
    data["sources"][0]["retention"]["raw_content"] = {
        "mode": "until",
        "until": "2028-01-01T00:00:00Z",
        "basis": "temporary",
    }
    collection = EvidenceCollection.model_validate_json(json.dumps(data))
    settings = DatabaseSettings(database_path=tmp_path / "data.sqlite")
    migrate(settings)
    with session(settings) as conn, transaction(conn):
        search = create_search(conn, issue_session(conn).principal)
        store_evidence(
            conn,
            search,
            collection,
            source_classes={
                "src_shared": "permitted_independent",
                "src_google": "ordinary_google",
            },
        )
        result = lookup_evidence(conn, search, "branch_a")
        assert result.evidence == ()
        assert result.findings == ()
        assert result.sources[0].kind == "google"


def test_duplicate_write_rolls_back_all_changes(tmp_path):
    settings = DatabaseSettings(database_path=tmp_path / "data.sqlite")
    migrate(settings)
    collection = EvidenceCollection.model_validate_json(
        json.dumps(fixture_collection())
    )
    classes = {
        "src_shared": "permitted_independent",
        "src_google": "public_google_reviews",
    }
    with session(settings) as conn:
        with transaction(conn):
            search = create_search(conn, issue_session(conn).principal)
            store_evidence(conn, search, collection, source_classes=classes)
        before = lookup_evidence(conn, search, "branch_a")
        with pytest.raises(sqlite3.IntegrityError), transaction(conn):
            store_evidence(conn, search, collection, source_classes=classes)
        assert lookup_evidence(conn, search, "branch_a") == before
        assert conn.execute("SELECT count(*) FROM evidence_items").fetchone()[0] == 3


def test_google_origin_cannot_be_reclassified_and_place_id_is_not_stored(tmp_path):
    data = fixture_collection()
    data["sources"][1]["origin"]["branch"] = {"place_id": "SECRET_REVIEW"}
    collection = EvidenceCollection.model_validate_json(
        json.dumps(
            {
                **data,
                "evidence": data["evidence"][:3],
                "food_claims": data["food_claims"][:3],
            }
        )
    )
    settings = DatabaseSettings(database_path=tmp_path / "data.sqlite")
    migrate(settings)
    with session(settings) as conn:
        with transaction(conn):
            search = create_search(conn, issue_session(conn).principal)
            with pytest.raises(ValueError, match="mismatched source class"):
                store_evidence(
                    conn,
                    search,
                    collection,
                    source_classes={
                        "src_shared": "unknown_publisher",
                        "src_google": "permitted_independent",
                    },
                )
        with transaction(conn):
            store_evidence(
                conn,
                search,
                collection,
                source_classes={
                    "src_shared": "unknown_publisher",
                    "src_google": "public_google_reviews",
                },
            )
        assert lookup_evidence(conn, search, "branch_a").sources == ()
        assert "SECRET" not in "\n".join(conn.iterdump())
