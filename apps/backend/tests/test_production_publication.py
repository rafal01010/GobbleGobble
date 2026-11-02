import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_research_agent import setup_values

from app import production_worker
from app.agents.owner_execution import OwnerOutcome
from app.api.application import create_app
from app.core.settings import ApiSettings
from app.db.events import read_events
from app.db.leases import LeaseLost, claim_run
from app.db.session import session, transaction
from app.ephemeral_publications import PublicationBroker
from app.publication_validation import validate_publication
from app.review_evidence import append_review_evidence
from app.schemas.evidence import EvidenceCollection
from app.schemas.recommendations import RecommendationCollection

pytest_plugins = ["test_foundation_api"]
ORIGIN = "http://frontend.test"


def make_publication(search_id):
    path = Path("../../docs/examples/recommendation-contract/accepted_full_set.json")
    data = json.loads(path.read_text())
    data["search_id"] = search_id
    data["snapshots"] = data["snapshots"][:1]
    snapshot = data["snapshots"][0]
    snapshot["snapshot_id"] = "snap_test"
    snapshot["discovery_leads"] = []
    snapshot["researched_candidates"] = snapshot["researched_candidates"][:1]
    snapshot["researched_candidates"][0]["name"] = "Synthetic Test Cafe"
    _, _, review = setup_values()
    evidence = append_review_evidence(
        EvidenceCollection(place_ids=("branch_a",)), review, now=datetime.now(UTC)
    )
    raw = evidence.model_dump(mode="json")
    sample = raw["review_assessments"][0]["google"]["sample"]
    evidence_id = sample["evidence_ids"][0]
    sample["assessed_evidence_ids"] = [evidence_id]
    sample["assessed_count"] = 1
    raw["review_assessments"][0]["findings"] = [
        {
            "topic": "service",
            "statement": "Service was slow.",
            "polarity": "negative",
            "wording": "paraphrase",
            "subjects": [{"place_id": "branch_a", "evidence_ids": [evidence_id]}],
        },
        {
            "topic": "food",
            "statement": "The noodles were tasty.",
            "polarity": "positive",
            "wording": "paraphrase",
            "subjects": [{"place_id": "branch_a", "evidence_ids": [evidence_id]}],
        },
        {
            "topic": "complaint",
            "statement": "Unapproved finding sentinel.",
            "polarity": "negative",
            "wording": "paraphrase",
            "subjects": [{"place_id": "branch_a", "evidence_ids": [evidence_id]}],
        },
        {
            "topic": "price",
            "statement": "The meal cost 500 yen.",
            "polarity": "neutral",
            "wording": "paraphrase",
            "subjects": [{"place_id": "branch_a", "evidence_ids": [evidence_id]}],
        },
    ]
    snapshot["evidence"] = raw
    version_id = f"ver_{uuid4().hex}"
    data["versions"] = [
        {
            "version_id": version_id,
            "research_snapshot_id": "snap_test",
            "lineage": {"kind": "initial"},
            "primaries": [
                {
                    "place_id": "branch_a",
                    "why_to_go": "Unverified rationale sentinel.",
                    "food_claim_ids": [],
                }
            ],
            "runners_up": [],
            "shortfall_reason": "One candidate.",
            "food_choices": [],
        }
    ]
    collection = RecommendationCollection.model_validate_json(json.dumps(data))
    return validate_publication(
        collection,
        source_classes={raw["sources"][0]["source_id"]: "public_google_reviews"},
        now=datetime.now(UTC),
    )


def prepare_claim(database, owned_run):
    with session(database) as connection, transaction(connection):
        connection.execute(
            "INSERT INTO research_snapshots (search_id, snapshot_id) VALUES (?, ?)",
            (owned_run.search_id, "snap_test"),
        )
        return claim_run(connection, owned_run.run_id, "publication-test", 30)


@pytest.mark.asyncio
async def test_worker_publication_and_authorized_read_expire_without_persistence(
    database, owned_run, client, monkeypatch
):
    publication = make_publication(owned_run.search_id)
    claim = prepare_claim(database, owned_run)
    settings, _, _ = setup_values()
    result = production_worker.ProductionResult(
        claim,
        OwnerOutcome("partial", "finished"),
        publication,
        datetime.now(UTC) + timedelta(minutes=1),
        frozenset(
            finding
            for finding in publication.unverified_draft.snapshots[0]
            .evidence.review_assessments[0]
            .findings
            if finding.statement != "Unapproved finding sentinel."
        ),
    )

    async def execute(*args, **kwargs):
        return result

    monkeypatch.setattr(production_worker, "execute_production_run", execute)
    with tempfile.TemporaryDirectory(prefix="gg-pub-", dir="/tmp") as directory:
        socket_path = Path(directory) / "broker.sock"
        monkeypatch.setenv("GG_PUBLICATION_SOCKET", str(socket_path))
        with PublicationBroker(socket_path, ttl_seconds=0.4):
            await production_worker.execute_and_publish_production_run(
                database, settings, owned_run.run_id, worker_id="publication-test"
            )
            with session(database) as connection, transaction(connection):
                row = connection.execute(
                    "SELECT content_json FROM result_versions WHERE run_id=?",
                    (owned_run.run_id,),
                ).fetchone()
                events = read_events(
                    connection, owned_run.run_id, after_sequence=2, limit=10
                )
                content = row["content_json"]
                assert json.loads(content) == {
                    "kind": "ephemeral",
                    "version_id": publication.unverified_draft.versions[0].version_id,
                    "snapshot_id": "snap_test",
                }
                assert (
                    events.events[0].result_version_id
                    == publication.unverified_draft.versions[0].version_id
                )
                assert "branch_a" not in content
                assert "Service was slow" not in content
                assert "Unverified rationale sentinel" not in content
                assert "Service was slow" not in events.model_dump_json()
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                for table in tables:
                    rows = connection.execute(
                        f'SELECT * FROM "{table["name"]}"'
                    ).fetchall()
                    actual = repr([tuple(row) for row in rows])
                    assert "Service was slow" not in actual
                    assert "Synthetic Test Cafe" not in actual
                    assert "Unverified rationale sentinel" not in actual
            app = create_app(
                api_settings=ApiSettings(trusted_frontend_origins=(ORIGIN,)),
                database_settings=database,
                publication_socket=socket_path,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://backend.test",
                cookies=client.cookies,
            ) as api:
                url = f"/api/searches/{owned_run.search_id}/runs/{owned_run.run_id}"
                response = await api.get(url)
                assert response.status_code == 200
                body = response.json()
                assert body["result_status"] == "available"
                assert (
                    body["display_version_id"]
                    == publication.unverified_draft.versions[0].version_id
                )
                place = body["recommendations"]["travel_unconfirmed"][0]
                assert place["name"] == "Synthetic Test Cafe"
                assert place["findings"][0]["statement"] == "Service was slow."
                assert place["findings"][0]["sources"][0]["publisher"] == "Google Maps"
                assert [
                    (item["topic"], item["statement"]) for item in place["findings"]
                ] == [
                    ("service", "Service was slow."),
                    ("food", "The noodles were tasty."),
                    ("price", "The meal cost 500 yen."),
                ]
                assert place["review_samples"][0]["partial"] is True
                assert (
                    "not a complete review history"
                    in place["review_samples"][0]["qualification"]
                )
                assert "Unverified rationale sentinel" not in response.text
                assert "Unapproved finding sentinel" not in response.text
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://backend.test",
                ) as stranger:
                    await stranger.post("/api/session", headers={"Origin": ORIGIN})
                    assert (await stranger.get(url)).status_code == 404
                await asyncio.sleep(0.55)
                expired = await api.get(url)
                assert expired.json()["result_status"] == "unavailable"
                assert expired.json()["recommendations"] is None


@pytest.mark.asyncio
async def test_lost_lease_leaves_no_visible_result(database, owned_run, monkeypatch):
    publication = make_publication(owned_run.search_id)
    claim = prepare_claim(database, owned_run)
    settings, _, _ = setup_values()
    result = production_worker.ProductionResult(
        claim,
        OwnerOutcome("partial", "finished"),
        publication,
        datetime.now(UTC) + timedelta(minutes=1),
        frozenset(
            finding
            for finding in publication.unverified_draft.snapshots[0]
            .evidence.review_assessments[0]
            .findings
            if finding.statement != "Unapproved finding sentinel."
        ),
    )

    async def execute(*args, **kwargs):
        return result

    monkeypatch.setattr(production_worker, "execute_production_run", execute)
    with session(database) as connection:
        connection.execute(
            "UPDATE run_leases SET expires_at_ms=0 WHERE run_id=?", (owned_run.run_id,)
        )
    with tempfile.TemporaryDirectory(prefix="gg-pub-", dir="/tmp") as directory:
        socket_path = Path(directory) / "broker.sock"
        monkeypatch.setenv("GG_PUBLICATION_SOCKET", str(socket_path))
        with PublicationBroker(socket_path), pytest.raises(LeaseLost):
            await production_worker.execute_and_publish_production_run(
                database, settings, owned_run.run_id, worker_id="publication-test"
            )
    with session(database) as connection:
        assert (
            connection.execute("SELECT count(*) FROM result_versions").fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM run_events WHERE run_id=?", (owned_run.run_id,)
            ).fetchone()[0]
            == 2
        )
