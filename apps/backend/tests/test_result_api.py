import json
from datetime import UTC, datetime, timedelta

import pytest
from test_result_versions import input_data, prepare

from app.api import application
from app.db.leases import claim_run
from app.db.result_versions import publish_result
from app.db.session import session, transaction
from app.schemas.evidence import EvidenceCollection

pytest_plugins = ["test_foundation_api"]


def publish_fixture(database, owned_run):
    result, evidence = input_data()
    now = datetime.now(UTC)
    data = evidence.model_dump(mode="json")
    for item in data["evidence"]:
        item["freshness"]["retrieved_at"] = (now - timedelta(minutes=1)).isoformat()
        item["freshness"]["fresh_until"] = (now + timedelta(hours=1)).isoformat()
    evidence = EvidenceCollection.model_validate_json(json.dumps(data))
    with session(database) as connection:
        prepare(connection, owned_run)
        with transaction(connection):
            connection.execute(
                "UPDATE research_candidates SET name=?, identity_source='geoapify_geography' WHERE search_id=? AND place_id=?",
                ("Example Cafe", owned_run.search_id, "branch_a"),
            )
            claim = claim_run(connection, owned_run.run_id, "api-test", 30)
            publish_result(
                connection,
                claim,
                result=result,
                evidence=evidence,
                source_classes={"src_group": "permitted_independent"},
                now=now,
                partial=True,
            )
    return now


@pytest.mark.asyncio
async def test_read_returns_source_backed_public_result(client, database, owned_run):
    publish_fixture(database, owned_run)
    response = await client.get(
        f"/api/searches/{owned_run.search_id}/runs/{owned_run.run_id}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result_status"] == "available"
    assert body["display_version_id"] == "ver_test"
    assert (
        body["progress_message"]
        == "Your recommendations are ready, but some information is missing."
    )
    assert body["recommendations"] == {
        "primaries": [],
        "runners_up": [],
        "travel_unconfirmed": [
            {
                "name": "Example Cafe",
                "explanation": "A menu choice.",
                "findings": [],
                "review_samples": [],
                "gaps": [],
                "sources": [
                    {
                        "publisher": "Example group",
                        "url": "https://example.invalid/menu",
                        "authors": [],
                    }
                ],
            }
        ],
    }
    assert "source_class" not in response.text
    assert "selection_evidence_ids" not in response.text
    assert "ev_menu_a" not in response.text


@pytest.mark.asyncio
async def test_read_explains_expired_result_without_internal_errors(
    client, database, owned_run, monkeypatch
):
    now = publish_fixture(database, owned_run)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return now + timedelta(hours=2)

    monkeypatch.setattr(application, "datetime", Later)
    response = await client.get(
        f"/api/searches/{owned_run.search_id}/runs/{owned_run.run_id}"
    )
    assert response.status_code == 200
    assert response.json()["result_status"] == "unavailable"
    assert response.json()["recommendations"] is None
    assert (
        response.json()["progress_message"]
        == "These results are no longer available. Try a new search."
    )
    assert "result_source_not_fresh" not in response.text


@pytest.mark.asyncio
async def test_ephemeral_result_requires_owner_and_unexpired_valid_overlay(
    client, database, owned_run, monkeypatch
):
    import httpx

    from app.core.settings import ApiSettings
    from app.db.result_versions import EphemeralResult, publish_ephemeral_result

    version_id = "ver_" + "a" * 32
    with session(database) as connection:
        prepare(connection, owned_run)
        with transaction(connection):
            claim = claim_run(connection, owned_run.run_id, "api-test", 30)
            publish_ephemeral_result(
                connection,
                claim,
                result=EphemeralResult(version_id=version_id, snapshot_id="snap_test"),
            )
    path = application.Path("/tmp/test-publication.sock")
    app = application.create_app(
        api_settings=ApiSettings(), database_settings=database, publication_socket=path
    )
    current_time = datetime.now(UTC)
    payload = {
        "publication": {
            "primaries": [
                {
                    "name": "Example Cafe",
                    "explanation": "Selected reviews may help you decide.",
                    "sources": [],
                    "findings": [],
                }
            ],
            "runners_up": [],
            "travel_unconfirmed": [],
        },
        "expires_at": (current_time + timedelta(minutes=1)).isoformat(),
    }
    requests = []

    def get_overlay(socket_path, request):
        assert socket_path == path
        with session(database) as connection, transaction(connection):
            assert (
                connection.execute("SELECT count(*) FROM result_versions").fetchone()[0]
                == 1
            )
        requests.append(request)
        return {"status": "ok", "payload": payload}

    monkeypatch.setattr(application, "exchange", get_overlay)
    transport = httpx.ASGITransport(app=app)
    url = f"/api/searches/{owned_run.search_id}/runs/{owned_run.run_id}"
    async with httpx.AsyncClient(
        transport=transport, base_url="http://backend.test"
    ) as other:
        response = await other.get(url)
        assert response.status_code == 401
        assert requests == []
    async with httpx.AsyncClient(
        transport=transport, base_url="http://backend.test", cookies=client.cookies
    ) as owner:
        response = await owner.get(url)
        assert response.status_code == 200
        assert response.json()["display_version_id"] == version_id
        assert (
            response.json()["recommendations"]["primaries"][0]["name"] == "Example Cafe"
        )
        assert requests == [{"op": "get", "key": version_id}]
        payload["expires_at"] = (current_time - timedelta(seconds=1)).isoformat()
        expired = await owner.get(url)
        assert expired.json()["result_status"] == "unavailable"
        assert expired.json()["recommendations"] is None
        assert expired.json()["progress_message"] == (
            "These results are no longer available. Try a new search."
        )
