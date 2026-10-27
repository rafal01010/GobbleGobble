"""Exercise the installed local stack using controlled jobs and fresh storage."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
PROCESS = ROOT / "scripts/local/backend-process.py"
RUNTIME = ROOT / "data/runtime/processes"
ORIGIN = "http://127.0.0.1:5173"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new evidence directory")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("output directory must not exist; preserve previous attempts")
    if any(RUNTIME.glob("*.owner")) or any(RUNTIME.glob("*.sock")):
        parser.error("stop supervised processes and inspect stale ownership first")
    for port in (8000, 5173):
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                parser.error(
                    f"port {port} is occupied; existing processes were untouched"
                )
    output.mkdir(parents=True)
    database = output / "foundation.sqlite3"
    env = dict(
        os.environ,
        GG_DATABASE_PATH=str(database),
        GG_TRUSTED_FRONTEND_ORIGINS='["http://127.0.0.1:5173"]',
        GG_SESSION_COOKIE_SECURE="false",
    )
    report: dict = {
        "mode": "controlled_local_only",
        "database": str(database),
        "origin": ORIGIN,
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "python": sys.version,
        "commands": [],
        "events": [],
        "timing_basis": "event occurred_at to client receipt; upper bound on commit to receipt",
    }

    def command(*parts: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            parts,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        report["commands"].append(
            {
                "argv": list(parts),
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        print(result.stdout, end="", flush=True)
        assert result.returncode == expected, result.stderr or result.stdout
        return result

    def manage(*parts: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        return command(sys.executable, str(PROCESS), *parts, expected=expected)

    def pids() -> tuple[int, int]:
        text = manage("app", "status").stdout
        found = tuple(int(pid) for pid in re.findall(r"running \(pid (\d+)\)", text))
        assert len(found) == 2, text
        return found

    def persisted(run: dict) -> dict:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM runs WHERE run_id = ?", (run["run_id"],)
            ).fetchone()
        assert row is not None
        return json.loads(row[0])

    def admit(client: httpx.Client) -> tuple[dict, dict]:
        body = {
            "command_id": f"foundation-{uuid4().hex}",
            "request": {
                "origin": {
                    "kind": "chosen",
                    "location": {
                        "coordinates": {"latitude": 0, "longitude": 0},
                        "timezone": "UTC",
                        "provider_refs": [],
                    },
                },
                "travel": {"mode": "walk", "minutes": 15},
                "eat_now_at": datetime.now(UTC).isoformat(),
            },
        }
        before = time.monotonic()
        response = client.post("/api/runs", json=body)
        elapsed = time.monotonic() - before
        assert response.status_code == 202, response.text
        run = response.json()["command"]["run"]
        assert run["state"] == {"status": "queued"}
        assert run["last_event_sequence"] == 1
        assert elapsed < 1, f"admission took {elapsed:.3f}s"
        report.setdefault("admission_seconds", []).append(elapsed)
        return body, run

    def events(
        client: httpx.Client,
        run: dict,
        after: int,
        until: int,
        start_worker: bool = False,
    ) -> list[dict]:
        path = f"/api/searches/{run['search_id']}/runs/{run['run_id']}/events"
        found = []
        with client.stream("GET", path, params={"after_sequence": after}) as response:
            assert response.status_code == 200, response.status_code
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                assert event["sequence"] == after + len(found) + 1
                assert event["run_id"] == run["run_id"]
                received = datetime.now(UTC)
                lag = (
                    received - datetime.fromisoformat(event["occurred_at"])
                ).total_seconds()
                report["events"].append({"event": event, "delivery_seconds": lag})
                if start_worker and not found:
                    manage("worker", "start", run["run_id"])
                    start_worker = False
                found.append(event)
                if event["sequence"] >= until:
                    break
        return found

    started = False
    try:
        command(str(ROOT / "scripts/local/migrate-db.sh"))
        command(str(ROOT / "scripts/local/migrate-db.sh"))
        with sqlite3.connect(database) as connection:
            report["migrations"] = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        started = True
        manage("api", "start")
        existing_api = manage("api", "status").stdout
        with socket.socket() as unrelated_listener:
            unrelated_listener.bind(("127.0.0.1", 5173))
            unrelated_listener.listen()
            manage("app", "start", expected=1)
            assert manage("api", "status").stdout == existing_api
            assert unrelated_listener.getsockname() == ("127.0.0.1", 5173)
            manage("frontend", "status", expected=1)
        report["failed_start_preserved_api_and_unrelated_listener"] = True
        manage("app", "start")
        initial = pids()
        manage("app", "start")
        assert pids() == initial
        with httpx.Client(
            base_url=ORIGIN, headers={"Origin": ORIGIN}, timeout=10, trust_env=False
        ) as client:
            assert client.post("/api/session").status_code == 204
            body, run = admit(client)
            replay = client.post("/api/runs", json=body)
            assert replay.status_code == 202
            assert replay.json()["command"]["disposition"] == "replayed"
            assert replay.json()["command"]["run"] == run
            conflict = json.loads(json.dumps(body))
            conflict["request"]["travel"]["minutes"] = 20
            assert client.post("/api/runs", json=conflict).status_code == 409
            bad = client.post("/api/runs", json={"invalid": "private-input-sentinel"})
            assert bad.status_code == 422
            assert bad.json() == {
                "error": {
                    "code": "invalid_request",
                    "message": "invalid_request",
                    "retryable": False,
                }
            }
            read_path = f"/api/searches/{run['search_id']}/runs/{run['run_id']}"
            assert client.get(read_path).json()["recommendations"] is None
            _, interrupted = admit(client)
            mismatch = client.get(
                f"/api/searches/{interrupted['search_id']}/runs/{run['run_id']}"
            )
            unknown = client.get(f"/api/searches/{run['search_id']}/runs/{uuid4().hex}")
            assert mismatch.status_code == unknown.status_code == 404
            assert mismatch.json() == unknown.json()
            assert (
                client.get(
                    f"/api/searches/{interrupted['search_id']}/runs/{run['run_id']}/events"
                ).status_code
                == 404
            )
            report["paired_access"] = {
                "mismatched": mismatch.json(),
                "unknown": unknown.json(),
            }
            with httpx.Client(
                base_url=ORIGIN, headers={"Origin": ORIGIN}, trust_env=False
            ) as foreign:
                assert foreign.post("/api/session").status_code == 204
                assert foreign.get(read_path).status_code == 404
            assert (
                client.post(
                    "/api/runs",
                    json=body,
                    headers={"Origin": "http://untrusted.invalid"},
                ).status_code
                == 403
            )
            idle = events(client, run, 0, 1)
            assert idle[0]["payload"] == {"kind": "queued"}
            assert persisted(run)["state"] == {"status": "queued"}
            report["idle_disconnect_preserved_run"] = True
            first = events(client, run, 0, 3, start_worker=True)
            assert [event["payload"]["kind"] for event in first] == [
                "queued",
                "progress",
                "progress",
            ]
            suffix = events(client, run, 3, 5)
            assert [event["sequence"] for event in suffix] == [4, 5]
            assert suffix[-1]["payload"]["outcome"]["kind"] == "insufficient_coverage"
            assert persisted(run)["state"]["status"] == "completed"
            path = read_path + "/events"
            assert client.get(path, params={"after_sequence": 5}).status_code == 204
            assert client.get(path, params={"after_sequence": 6}).status_code == 422
            assert client.get(path, params={"after_sequence": -1}).status_code == 422
            replay = client.get(
                path, params={"after_sequence": 0}, headers={"Last-Event-ID": "3"}
            )
            assert [
                line for line in replay.text.splitlines() if line.startswith("id:")
            ] == ["id: 4", "id: 5"]
            for measurement in report["events"]:
                if measurement["event"]["sequence"] > 1:
                    assert 0 <= measurement["delivery_seconds"] < 2
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.worker",
                    "diagnostic",
                    "--run-id",
                    run["run_id"],
                ],
                cwd=ROOT / "apps/backend",
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert (
                result.returncode == 5
                and result.stderr == "diagnostic_run_not_claimable\n"
            )
            report["duplicate_worker"] = {
                "exit": result.returncode,
                "stderr": result.stderr,
            }
            _, slow = admit(client)
            slow_path = (
                f"/api/searches/{slow['search_id']}/runs/{slow['run_id']}/events"
            )
            slow_events = []
            with client.stream("GET", slow_path) as response:
                assert response.status_code == 200
                lines = response.iter_lines()
                for line in lines:
                    if line.startswith("data: "):
                        slow_events.append(json.loads(line[6:]))
                        break
                assert slow_events[0]["payload"] == {"kind": "queued"}
                manage("worker", "start", slow["run_id"])
                deadline = time.monotonic() + 8
                while persisted(slow)["state"]["status"] != "completed":
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
                report["worker_completed_while_reader_paused"] = True
                for line in lines:
                    if line.startswith("data: "):
                        slow_events.append(json.loads(line[6:]))
            assert [event["sequence"] for event in slow_events] == [1, 2, 3, 4, 5]
            report["paused_reader_events"] = slow_events
            manage("worker", "start", interrupted["run_id"])
            deadline = time.monotonic() + 5
            while persisted(interrupted)["state"]["status"] != "running":
                assert time.monotonic() < deadline
                time.sleep(0.02)
            manage("app", "restart")
            restarted = pids()
            assert set(restarted).isdisjoint(initial)
            snapshot = persisted(interrupted)
            assert snapshot["state"]["status"] == "running"
            recovering_from = snapshot["last_event_sequence"]
            assert client.get(read_path).json()["run"]["state"]["status"] == "completed"
            with sqlite3.connect(database) as connection:
                expires = connection.execute(
                    "SELECT expires_at_ms FROM run_leases WHERE run_id = ?",
                    (interrupted["run_id"],),
                ).fetchone()[0]
            print("Waiting for the interrupted diagnostic lease to expire.", flush=True)
            deadline = time.monotonic() + 35
            while time.time() * 1000 <= expires:
                assert time.monotonic() < deadline
                time.sleep(0.25)
            manage("worker", "start", interrupted["run_id"])
            recovered = events(
                client, interrupted, recovering_from, recovering_from + 4
            )
            assert recovered[0]["payload"] == {
                "kind": "progress",
                "stage": "recovering",
            }
            assert recovered[-1]["payload"]["kind"] == "completed"
            assert recovered[0]["attempt_id"] != snapshot["attempt_id"]
            report["recovery"] = {"before": snapshot, "after": persisted(interrupted)}
            with sqlite3.connect(database) as connection:
                report["persisted_events"] = [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT event_json FROM run_events ORDER BY run_id, sequence"
                    )
                ]
            streamed = {
                (entry["event"]["run_id"], entry["event"]["sequence"]): entry["event"]
                for entry in report["events"]
            }
            durable = {
                (event["run_id"], event["sequence"]): event
                for event in report["persisted_events"]
            }
            assert all(durable[key] == event for key, event in streamed.items())
            assert len(streamed) == 9
            assert all(
                durable[(event["run_id"], event["sequence"])] == event
                for event in slow_events
            )
            report["streamed_events_equal_persisted"] = True
            report["processes"] = {"initial": initial, "restarted": restarted}
        manage("app", "stop")
        manage("app", "stop")
        manage("app", "status", expected=1)
        stale_id = uuid4().hex
        stale_marker = RUNTIME / f"worker-{stale_id}.owner"
        stale_marker.touch(exist_ok=False)
        try:
            manage("worker", "start", stale_id, expected=1)
            assert stale_marker.is_file()
        finally:
            stale_marker.unlink()
        report["stale_ownership_refused"] = True
        assert not list(RUNTIME.glob("*.owner")) and not list(RUNTIME.glob("*.sock"))
        for port in (8000, 5173):
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0
        started = False
        report["result"] = "passed"
        print(
            "PASS controlled local foundation; restaurant recommendations remain unimplemented."
        )
        return 0
    except Exception as error:
        report["result"] = "failed"
        report["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            if started:
                try:
                    manage("app", "stop")
                except Exception as error:
                    report["cleanup_failure"] = f"{type(error).__name__}: {error}"
                    raise
        finally:
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
