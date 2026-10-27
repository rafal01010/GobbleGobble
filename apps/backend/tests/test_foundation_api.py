import json
from collections.abc import AsyncIterator
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.application import create_app
from app.core.settings import ApiSettings, DatabaseSettings
from app.db.events import read_events
from app.db.migrations import migrate
from app.db.ownership import issue_session
from app.db.session import session, transaction
from app.schemas.lifecycle import InsufficientCoverageOutcome, RunSnapshot
from app.worker import (
    CheckpointRecords,
    WorkerJob,
    WorkerStep,
    diagnostic_job,
    execute_run,
)

ORIGIN = "http://frontend.test"
EXAMPLES = Path(__file__).resolve().parents[3] / "docs/examples/lifecycle-contract"


@pytest.fixture
def database(tmp_path: Path) -> DatabaseSettings:
    settings = DatabaseSettings(database_path=tmp_path / "api.sqlite3")
    migrate(settings)
    return settings


@pytest.fixture
def app(database: DatabaseSettings) -> FastAPI:
    return create_app(
        api_settings=ApiSettings(trusted_frontend_origins=(ORIGIN,)),
        database_settings=database,
    )


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://backend.test",
    ) as opened:
        yield opened


@pytest.fixture
def start_body() -> dict[str, Any]:
    return json.loads((EXAMPLES / "accepted_start.json").read_text())


@pytest_asyncio.fixture
async def owned_run(
    client: httpx.AsyncClient, start_body: dict[str, Any]
) -> RunSnapshot:
    bootstrap = await client.post("/api/session", headers={"Origin": ORIGIN})
    assert bootstrap.status_code == 204
    response = await client.post(
        "/api/runs", headers={"Origin": ORIGIN}, json=start_body
    )
    assert response.status_code == 202
    assert response.json()["command"]["disposition"] == "accepted"
    return RunSnapshot.model_validate_json(
        json.dumps(response.json()["command"]["run"])
    )


@pytest_asyncio.fixture
async def terminal_run(
    database: DatabaseSettings, owned_run: RunSnapshot
) -> RunSnapshot:
    return execute_run(
        database,
        owned_run.run_id,
        diagnostic_job(delay_seconds=0),
        worker_id="api-test",
    )


@pytest.mark.asyncio
async def test_session_bootstrap_commits_cookie_and_reuses_identity(
    client: httpx.AsyncClient, database: DatabaseSettings
) -> None:
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    first = await client.post("/api/session", headers={"Origin": ORIGIN})
    assert first.status_code == 204
    assert first.content == b""
    cookie = SimpleCookie()
    cookie.load(first.headers["set-cookie"])
    credential = cookie["gg_session"]
    assert len(credential.value) == 43
    assert credential["httponly"] is True
    assert credential["samesite"] == "lax"
    assert credential["path"] == "/"
    assert credential["secure"] == ""
    second = await client.post("/api/session", headers={"Origin": ORIGIN})
    assert second.status_code == 204
    assert "set-cookie" not in second.headers
    assert second.headers["cache-control"] == "no-store"
    with session(database) as opened:
        assert (
            opened.execute("SELECT count(*) FROM anonymous_sessions").fetchone()[0] == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scheme", "configured_secure", "expected_secure"),
    [("http", False, False), ("https", False, True), ("http", True, True)],
)
async def test_session_secure_cookie_policy(
    database: DatabaseSettings,
    scheme: str,
    configured_secure: bool,
    expected_secure: bool,
) -> None:
    app = create_app(
        api_settings=ApiSettings(
            trusted_frontend_origins=(ORIGIN,), session_cookie_secure=configured_secure
        ),
        database_settings=database,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"{scheme}://backend.test"
    ) as client:
        response = await client.post("/api/session", headers={"Origin": ORIGIN})
    assert response.status_code == 204
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert bool(cookie["gg_session"]["secure"]) is expected_secure
    assert cookie["gg_session"]["httponly"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["", "invalid-cookie", "A" * 43])
async def test_invalid_supplied_cookie_never_bootstraps_a_replacement(
    client: httpx.AsyncClient, database: DatabaseSettings, credential: str
) -> None:
    response = await client.post(
        "/api/session", headers={"Origin": ORIGIN, "Cookie": f"gg_session={credential}"}
    )
    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "invalid_session",
            "retryable": False,
        }
    }
    assert "set-cookie" not in response.headers
    with session(database) as opened:
        assert (
            opened.execute("SELECT count(*) FROM anonymous_sessions").fetchone()[0] == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origins",
    [
        [],
        [("Origin", "null")],
        [("Origin", "http://untrusted.test")],
        [("Origin", ORIGIN), ("Origin", ORIGIN)],
    ],
    ids=["missing", "null", "untrusted", "duplicate"],
)
async def test_mutation_origin_rejection_precedes_body_parsing_and_storage(
    client: httpx.AsyncClient,
    database: DatabaseSettings,
    origins: list[tuple[str, str]],
) -> None:
    response = await client.post(
        "/api/runs", headers=origins, content=b'{"private-body":'
    )
    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "origin_not_allowed",
            "retryable": False,
        }
    }
    assert response.headers["cache-control"] == "no-store"
    with session(database) as opened:
        assert opened.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_origin_boundary_covers_every_mutation_method(
    client: httpx.AsyncClient, method: str
) -> None:
    response = await client.request(method, "/api/runs", content=b"private-body")
    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "origin_not_allowed",
            "retryable": False,
        }
    }


@pytest.mark.asyncio
async def test_approved_cors_preflight_allows_credentialed_event_reconnect(
    client: httpx.AsyncClient,
) -> None:
    response = await client.options(
        "/api/runs",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, Last-Event-ID",
        },
    )
    assert response.status_code == 200
    assert response.text == "OK"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "last-event-id" in response.headers["access-control-allow-headers"].lower()
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [
            ("Origin", "http://untrusted.test"),
            ("Access-Control-Request-Method", "POST"),
        ],
        [("Origin", ORIGIN), ("Access-Control-Request-Method", "DELETE")],
        [
            ("Origin", ORIGIN),
            ("Access-Control-Request-Method", "POST"),
            ("Access-Control-Request-Headers", "X-Private"),
        ],
        [
            ("Origin", ORIGIN),
            ("Origin", ORIGIN),
            ("Access-Control-Request-Method", "POST"),
        ],
    ],
    ids=["origin", "method", "header", "duplicate-origin"],
)
async def test_rejected_preflight_uses_the_fixed_error_envelope(
    client: httpx.AsyncClient, headers: list[tuple[str, str]]
) -> None:
    response = await client.options("/api/runs", headers=headers)
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "cors_not_allowed",
            "retryable": False,
        }
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_duplicate_admission_replays_and_conflict_preserves_the_original(
    client: httpx.AsyncClient,
    database: DatabaseSettings,
    owned_run: RunSnapshot,
    start_body: dict[str, Any],
) -> None:
    replay = await client.post("/api/runs", headers={"Origin": ORIGIN}, json=start_body)
    assert replay.status_code == 202
    assert replay.json()["command"]["disposition"] == "replayed"
    assert replay.json()["command"]["run"]["run_id"] == owned_run.run_id
    start_body["request"]["travel"]["minutes"] = 22
    conflict = await client.post(
        "/api/runs", headers={"Origin": ORIGIN}, json=start_body
    )
    assert conflict.status_code == 409
    assert conflict.json() == {
        "error": {
            "code": "command_conflict",
            "message": "command_conflict",
            "retryable": False,
        }
    }
    status = await client.get(
        f"/api/searches/{owned_run.search_id}/runs/{owned_run.run_id}"
    )
    assert status.status_code == 200
    assert status.json() == {
        "run": owned_run.model_dump(mode="json"),
        "recommendations": None,
        "display_version_id": None,
    }
    with session(database) as opened:
        assert opened.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert opened.execute("SELECT count(*) FROM run_events").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_invalid_contract_does_not_echo_input_or_admit_work(
    client: httpx.AsyncClient,
    database: DatabaseSettings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = await client.post(
        "/api/runs",
        headers={"Origin": ORIGIN},
        json={"private-body": "sensitive-raw-input"},
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "invalid_request",
            "retryable": False,
        }
    }
    assert "sensitive-raw-input" not in response.text + caplog.text
    with session(database) as opened:
        assert opened.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/events"], ids=["status", "events"])
@pytest.mark.parametrize(
    "case", ["unknown-search", "unknown-run", "foreign", "same-owner-mismatch"]
)
async def test_status_and_events_conceal_unknown_foreign_and_mismatched_pairs(
    client: httpx.AsyncClient,
    database: DatabaseSettings,
    owned_run: RunSnapshot,
    start_body: dict[str, Any],
    suffix: str,
    case: str,
) -> None:
    search_id, run_id = owned_run.search_id, owned_run.run_id
    headers = {}
    if case == "unknown-search":
        search_id = "unknown-search"
    elif case == "unknown-run":
        run_id = "unknown-run"
    elif case == "foreign":
        with session(database) as opened, transaction(opened):
            other = issue_session(opened)
        headers["Cookie"] = f"gg_session={other.credential}"
    else:
        second = await client.post(
            "/api/runs",
            headers={"Origin": ORIGIN},
            json={**start_body, "command_id": "second-command"},
        )
        assert second.status_code == 202
        run_id = second.json()["command"]["run"]["run_id"]
    response = await client.get(
        f"/api/searches/{search_id}/runs/{run_id}{suffix}", headers=headers
    )
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "not_found", "retryable": False}
    }
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/events"], ids=["status", "events"])
@pytest.mark.parametrize("credential", [None, "", "invalid-cookie"])
async def test_status_and_events_require_a_valid_session(
    client: httpx.AsyncClient,
    owned_run: RunSnapshot,
    suffix: str,
    credential: str | None,
) -> None:
    client.cookies.clear()
    headers = {} if credential is None else {"Cookie": f"gg_session={credential}"}
    response = await client.get(
        f"/api/searches/{owned_run.search_id}/runs/{owned_run.run_id}{suffix}",
        headers=headers,
    )
    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "invalid_session",
            "retryable": False,
        }
    }


@pytest.mark.asyncio
async def test_missing_database_readiness_returns_safe_retryable_failure(
    tmp_path: Path,
) -> None:
    database = DatabaseSettings(
        database_path=tmp_path / "missing-private-database.sqlite3"
    )
    app = create_app(
        api_settings=ApiSettings(trusted_frontend_origins=(ORIGIN,)),
        database_settings=database,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://backend.test",
    ) as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "unavailable",
            "message": "storage_unavailable",
            "retryable": True,
        }
    }
    assert str(tmp_path) not in response.text
    assert not database.database_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/events"], ids=["status", "events"])
async def test_corrupt_storage_fails_before_headers_without_leaking_raw_data(
    client: httpx.AsyncClient,
    database: DatabaseSettings,
    owned_run: RunSnapshot,
    caplog: pytest.LogCaptureFixture,
    suffix: str,
) -> None:
    with session(database) as opened:
        opened.execute(
            "UPDATE runs SET snapshot_json = ? WHERE run_id = ?",
            ('{"private": "sensitive-storage-body"}', owned_run.run_id),
        )
    response = await client.get(
        f"/api/searches/{owned_run.search_id}/runs/{owned_run.run_id}{suffix}"
    )
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "unavailable",
            "message": "storage_unavailable",
            "retryable": True,
        }
    }
    assert response.headers["content-type"] == "application/json"
    assert "sensitive-storage-body" not in response.text + caplog.text
    assert str(database.database_path) not in response.text + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("queries", "headers"),
    [
        ([("after_sequence", "")], []),
        ([("after_sequence", "-1")], []),
        ([("after_sequence", "+1")], []),
        ([("after_sequence", " 1")], []),
        ([("after_sequence", "1.5")], []),
        ([("after_sequence", "١")], []),
        ([("after_sequence", "0" * 20)], []),
        ([("after_sequence", "9223372036854775808")], []),
        ([("after_sequence", "1"), ("after_sequence", "2")], []),
        ([("after_sequence", "6")], []),
        ([], [("Last-Event-ID", "")]),
        ([], [("Last-Event-ID", "-1")]),
        ([], [("Last-Event-ID", "0" * 20)]),
        ([], [("Last-Event-ID", "9223372036854775808")]),
        ([], [("Last-Event-ID", "1"), ("Last-Event-ID", "2")]),
        ([], [("Last-Event-ID", "6")]),
        ([("after_sequence", "invalid")], [("Last-Event-ID", "5")]),
    ],
)
async def test_invalid_event_cursors_return_json_before_stream_headers(
    client: httpx.AsyncClient,
    terminal_run: RunSnapshot,
    queries: list[tuple[str, str]],
    headers: list[tuple[str, str]],
) -> None:
    response = await client.get(
        f"/api/searches/{terminal_run.search_id}/runs/{terminal_run.run_id}/events",
        params=tuple(queries),
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "invalid_request",
            "retryable": False,
        }
    }
    assert response.headers["content-type"] == "application/json"
    assert "x-accel-buffering" not in response.headers


@pytest.mark.asyncio
async def test_terminal_event_replay_matches_persisted_envelopes(
    client: httpx.AsyncClient, database: DatabaseSettings, terminal_run: RunSnapshot
) -> None:
    response = await client.get(
        f"/api/searches/{terminal_run.search_id}/runs/{terminal_run.run_id}/events"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.startswith(": connected\n\nid: 1\n")
    frames = [
        frame for frame in response.text.split("\n\n") if frame.startswith("id: ")
    ]
    payloads = [json.loads(frame.split("\ndata: ", 1)[1]) for frame in frames]
    assert [
        (payload["sequence"], payload["payload"]["kind"]) for payload in payloads
    ] == [
        (1, "queued"),
        (2, "progress"),
        (3, "progress"),
        (4, "progress"),
        (5, "completed"),
    ]
    assert [frame.split("\n", 1)[0] for frame in frames] == [
        "id: 1",
        "id: 2",
        "id: 3",
        "id: 4",
        "id: 5",
    ]
    with session(database) as opened, transaction(opened):
        stored = read_events(opened, terminal_run.run_id, after_sequence=0, limit=10)
    assert payloads == [event.model_dump(mode="json") for event in stored.events]
    assert payloads[-1]["payload"]["outcome"] == {
        "kind": "insufficient_coverage",
        "gaps": ["Diagnostic lifecycle check only. No place research was performed."],
    }


@pytest.mark.asyncio
async def test_event_reconnect_header_overrides_the_query_and_caught_up_returns_204(
    client: httpx.AsyncClient, terminal_run: RunSnapshot
) -> None:
    url = f"/api/searches/{terminal_run.search_id}/runs/{terminal_run.run_id}/events"
    replay = await client.get(
        url, params={"after_sequence": "1"}, headers={"Last-Event-ID": "3"}
    )
    assert replay.status_code == 200
    frames = [frame for frame in replay.text.split("\n\n") if frame.startswith("id: ")]
    assert [frame.split("\n", 1)[0] for frame in frames] == ["id: 4", "id: 5"]
    for queries, headers in (
        ({"after_sequence": "5"}, {}),
        ({"after_sequence": "0"}, {"Last-Event-ID": "5"}),
    ):
        caught_up = await client.get(url, params=queries, headers=headers)
        assert caught_up.status_code == 204
        assert caught_up.content == b""
        assert caught_up.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_terminal_replay_drains_multiple_batches_for_independent_cursors(
    client: httpx.AsyncClient, database: DatabaseSettings, owned_run: RunSnapshot
) -> None:
    job = WorkerJob(
        steps=tuple(WorkerStep("researching", CheckpointRecords) for _ in range(101)),
        outcome=InsufficientCoverageOutcome(
            gaps=("Controlled multi-batch replay only.",)
        ),
    )
    completed = execute_run(database, owned_run.run_id, job, worker_id="batch-worker")
    assert completed.last_event_sequence == 104
    url = f"/api/searches/{owned_run.search_id}/runs/{owned_run.run_id}/events"
    first = await client.get(url, params={"after_sequence": "0"})
    second = await client.get(url, params={"after_sequence": "98"})
    assert first.status_code == second.status_code == 200
    first_payloads = [
        json.loads(frame.split("\ndata: ", 1)[1])
        for frame in first.text.split("\n\n")
        if frame.startswith("id: ")
    ]
    second_payloads = [
        json.loads(frame.split("\ndata: ", 1)[1])
        for frame in second.text.split("\n\n")
        if frame.startswith("id: ")
    ]
    assert [payload["sequence"] for payload in first_payloads] == list(range(1, 105))
    assert [payload["sequence"] for payload in second_payloads] == [
        99,
        100,
        101,
        102,
        103,
        104,
    ]
    assert second_payloads == first_payloads[98:]
    assert second_payloads[-1]["payload"] == {
        "kind": "completed",
        "outcome": {
            "kind": "insufficient_coverage",
            "gaps": ["Controlled multi-batch replay only."],
        },
    }
    with session(database) as opened, transaction(opened):
        persisted = read_events(opened, owned_run.run_id, after_sequence=0, limit=1000)
    assert first_payloads == [
        event.model_dump(mode="json") for event in persisted.events
    ]
