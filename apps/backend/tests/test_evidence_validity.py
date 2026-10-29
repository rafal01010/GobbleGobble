import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.evidence_validity import DerivedArtifact, assess_artifacts
from app.schemas.evidence import EvidenceCollection

NOW = datetime(2026, 10, 29, 4, tzinfo=UTC)


def collection(*, expiry=None, retention=None, permission="allowed"):
    path = (
        Path(__file__).resolve().parents[3]
        / "docs/examples/evidence-contract/accepted_shared_source_scoped_menu_and_price.json"
    )
    data = json.loads(path.read_text())
    for source in data["sources"]:
        for declaration in source["policy"].values():
            declaration["decision"] = permission
        for name in ("raw_content", "derived_content", "identifiers"):
            source["retention"][name] = retention or {
                "mode": "indefinite",
                "basis": "test",
            }
    for item in data["evidence"]:
        item["freshness"]["retrieved_at"] = (NOW - timedelta(days=1)).isoformat()
        item["freshness"]["fresh_until"] = (
            expiry or (NOW + timedelta(days=1)).isoformat()
        )
    return EvidenceCollection.model_validate_json(json.dumps(data))


def assess(value, artifacts, *, action="display", classes=None):
    return assess_artifacts(
        value,
        artifacts,
        source_classes=classes
        if classes is not None
        else {"src_group_menu": "permitted_independent"},
        now=NOW,
        action=action,
    )


def chain():
    return (
        DerivedArtifact(
            "claim", "claim", evidence_ids=("ev_menu_a",), place_id="branch_a"
        ),
        DerivedArtifact("summary", "summary", artifact_ids=("claim",)),
        DerivedArtifact("choice", "choice", artifact_ids=("summary",)),
        DerivedArtifact("result", "result", artifact_ids=("choice",)),
        DerivedArtifact("event", "event", artifact_ids=("result",)),
        DerivedArtifact("trace", "trace", artifact_ids=("result",)),
    )


def test_stale_fact_invalidates_descendants_but_remains_retainable():
    value = collection(expiry=NOW.isoformat())
    result = assess(value, chain())
    assert [item.freshness for item in result] == ["stale"] * 6
    assert [item.retention for item in result] == ["permitted"] * 6
    assert not any(item.allowed for item in result)
    assert all(item.allowed for item in assess(value, chain()[:5], action="store"))


def test_retention_expiry_does_not_make_fresh_fact_stale():
    value = collection(
        retention={"mode": "until", "until": NOW.isoformat(), "basis": "test"}
    )
    result = assess(value, chain()[:5])
    assert [item.freshness for item in result] == ["fresh"] * 5
    assert [item.retention for item in result] == ["expired"] * 5
    assert not any(item.allowed for item in result)
    before = collection(
        retention={
            "mode": "until",
            "until": (NOW + timedelta(days=1)).isoformat(),
            "basis": "test",
        }
    )
    assert assess(before, chain()[:1])[0].allowed
    assert not assess(before, chain()[:1], action="store")[0].allowed


def test_unknown_lineage_source_policy_and_trace_fail_closed():
    value = collection()
    assert all(item.allowed for item in assess(value, chain()[:5]))
    assert not assess(value, chain(), action="trace")[-1].allowed
    assert not assess(value, chain()[:1], classes={})[0].allowed
    assert not assess(collection(permission="unknown"), chain()[:1])[0].allowed
    with pytest.raises(ValueError, match="lineage"):
        assess(value, (DerivedArtifact("missing", "result"),))
    with pytest.raises(ValueError, match="unknown evidence"):
        assess(
            value, (DerivedArtifact("missing", "result", evidence_ids=("ev_missing",)),)
        )
    with pytest.raises(ValueError, match="cyclic"):
        assess(
            value,
            (
                DerivedArtifact("a", "result", artifact_ids=("b",)),
                DerivedArtifact("b", "summary", artifact_ids=("a",)),
            ),
        )
    with pytest.raises(ValueError, match="another place"):
        assess(
            value,
            (
                DerivedArtifact(
                    "wrong", "claim", evidence_ids=("ev_menu_a",), place_id="branch_b"
                ),
            ),
        )


def test_multiple_supports_propagate_stalest_fact():
    data = collection().model_dump(mode="json")
    data["evidence"][1]["freshness"]["fresh_until"] = NOW.isoformat()
    value = EvidenceCollection.model_validate_json(json.dumps(data))
    result = assess(
        value,
        (DerivedArtifact("both", "result", evidence_ids=("ev_menu_a", "ev_menu_b")),),
    )[0]
    assert (result.freshness, result.retention, result.allowed) == (
        "stale",
        "permitted",
        False,
    )


def test_stored_fresh_lookup_keeps_historical_record(tmp_path):
    from app.core.settings import DatabaseSettings
    from app.db.evidence import lookup_evidence, lookup_fresh_evidence, store_evidence
    from app.db.migrations import migrate
    from app.db.ownership import create_search, issue_session
    from app.db.session import session, transaction

    settings = DatabaseSettings(database_path=tmp_path / "fresh.sqlite")
    migrate(settings)
    value = collection(expiry=NOW.isoformat())
    with session(settings) as connection:
        with transaction(connection):
            search = create_search(connection, issue_session(connection).principal)
            store_evidence(
                connection,
                search,
                value,
                source_classes={"src_group_menu": "permitted_independent"},
            )
        old = lookup_evidence(connection, search, "branch_a")
        current = lookup_fresh_evidence(connection, search, "branch_a", now=NOW)
        assert old.findings
        assert current.lookup.findings == ()
        assert current.stale_findings == tuple(item.finding_id for item in old.findings)
        assert current.unknown_findings == ()
        assert lookup_evidence(connection, search, "branch_a") == old


def test_invalid_attribution_cannot_authorize_display():
    data = collection().model_dump(mode="json")
    data["sources"][0]["source_url"] = "invalid"
    value = EvidenceCollection.model_validate_json(json.dumps(data))
    assert not assess(value, chain()[:1])[0].source_use_allowed


def test_google_review_requires_named_author_for_display():
    path = (
        Path(__file__).resolve().parents[3]
        / "docs/examples/evidence-contract/accepted_google_present.json"
    )
    data = json.loads(path.read_text())
    for declaration in data["sources"][0]["policy"].values():
        declaration["decision"] = "allowed"
    data["evidence"][0]["freshness"]["fresh_until"] = (
        NOW + timedelta(days=1)
    ).isoformat()
    value = EvidenceCollection.model_validate_json(json.dumps(data))
    artifact = (DerivedArtifact("review", "claim", evidence_ids=("ev_review_a",)),)
    classes = {"src_google_a": "public_google_reviews"}
    assert assess(value, artifact, classes=classes)[0].allowed
    data["attributions"][0]["name"] = None
    value = EvidenceCollection.model_validate_json(json.dumps(data))
    assert not assess(value, artifact, classes=classes)[0].allowed
    assert not assess(
        value, artifact, classes={"src_google_a": "permitted_independent"}
    )[0].allowed
