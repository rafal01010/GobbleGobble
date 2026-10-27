#!/usr/bin/env python3
"""Manage local API and explicit diagnostic worker processes."""

from __future__ import annotations

import argparse
import fcntl
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "apps" / "backend"
FRONTEND = ROOT / "apps" / "frontend"
RUNTIME = ROOT / "data" / "runtime" / "processes"
PYTHON = BACKEND / ".venv" / "bin" / "python"


def paths(name: str) -> tuple[Path, Path, Path]:
    return (
        RUNTIME / f"{name}.sock",
        RUNTIME / f"{name}.owner",
        RUNTIME / f"{name}.log",
    )


def contact(path: Path, command: str) -> dict[str, object] | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(8)
            client.connect(str(path))
            client.sendall(command.encode("ascii") + b"\n")
            data = bytearray()
            while len(data) < 4096:
                chunk = client.recv(4096 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            result = json.loads(data)
            return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


def command_for(name: str) -> list[str]:
    if name == "api":
        return [
            str(PYTHON),
            "-m",
            "uvicorn",
            "app.api.application:create_app_from_environment",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
    if name == "frontend":
        node = shutil.which("node")
        if node is None:
            raise FileNotFoundError("node missing")
        return [
            node,
            str(FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"),
            "dev",
            "--host",
            "127.0.0.1",
            "--port",
            "5173",
            "--strictPort",
        ]
    return [
        str(PYTHON),
        "-m",
        "app.worker",
        "diagnostic",
        "--run-id",
        name.removeprefix("worker-"),
    ]


def supervise(name: str) -> int:
    socket_path, marker, log = paths(name)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    child: subprocess.Popen[bytes] | None = None
    bound = False
    try:
        listener.bind(str(socket_path))
        bound = True
        os.chmod(socket_path, 0o600)
        listener.listen(4)
        listener.settimeout(0.2)
        with log.open("ab", buffering=0) as output:
            child = subprocess.Popen(
                command_for(name),
                cwd=FRONTEND if name == "frontend" else BACKEND,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            while child.poll() is None:
                try:
                    client, _ = listener.accept()
                except TimeoutError:
                    continue
                with client:
                    client.settimeout(2)
                    try:
                        command = client.recv(64).decode("ascii").strip()
                        if command == "stop":
                            child.terminate()
                            try:
                                child.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                child.kill()
                                child.wait()
                            client.sendall(json.dumps({"state": "stopped"}).encode())
                            break
                        if command == "status":
                            client.sendall(
                                json.dumps(
                                    {"state": "running", "pid": child.pid}
                                ).encode()
                            )
                    except (OSError, UnicodeError):
                        continue
        return 0
    except OSError:
        return 1
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        listener.close()
        if bound:
            socket_path.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)


def manage(action: str, name: str) -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return manage_locked(action, name)


def manage_locked(action: str, name: str, started: list[str] | None = None) -> int:
    socket_path, marker, log = paths(name)
    if action == "start":
        if marker.exists() or socket_path.exists():
            status = contact(socket_path, "status")
            if status and status.get("state") == "running":
                print(f"{name} already running (pid {status['pid']})")
                return 0
            print(
                f"{name} has stale or unreachable ownership files; inspect them before recovery",
                file=sys.stderr,
            )
            return 1
        if name == "frontend" and not frontend_available():
            print(
                "frontend requires supported Node and installed dependencies",
                file=sys.stderr,
            )
            return 1
        if name != "frontend" and not PYTHON.is_file():
            print(
                "backend environment missing; run scripts/local/init-backend.sh",
                file=sys.stderr,
            )
            return 1
        marker.touch(mode=0o600, exist_ok=False)
        if started is not None:
            started.append(name)
        try:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_supervise", name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            marker.unlink(missing_ok=True)
            print(f"could not start {name} supervisor", file=sys.stderr)
            return 1
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = contact(socket_path, "status")
            if status and status.get("state") == "running":
                print(f"{name} started (pid {status['pid']}); log: {log}")
                return 0
            if not marker.exists():
                break
            time.sleep(0.05)
        print(
            f"{name} did not start; inspect {log} and ownership files",
            file=sys.stderr,
        )
        return 1
    if not marker.exists() and not socket_path.exists():
        print(f"{name} not running")
        return 0 if action == "stop" else 1
    result = contact(socket_path, action)
    if result is None:
        print(
            f"{name} has stale or unreachable ownership files; inspect them before recovery",
            file=sys.stderr,
        )
        return 1
    if action == "status":
        print(f"{name} {result['state']} (pid {result['pid']}); log: {log}")
    else:
        deadline = time.monotonic() + 2
        while (marker.exists() or socket_path.exists()) and time.monotonic() < deadline:
            time.sleep(0.02)
        if marker.exists() or socket_path.exists():
            print(
                f"{name} stopped but ownership cleanup is incomplete", file=sys.stderr
            )
            return 1
        print(f"{name} {result['state']}")
    return 0


def frontend_available() -> bool:
    node = shutil.which("node")
    vite = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    if node is None or not vite.is_file():
        return False
    try:
        result = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=3, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return False
    version = re.fullmatch(r"v(\d+)\.(\d+)\.\d+\s*", result.stdout)
    if version is None:
        return False
    major, minor = int(version[1]), int(version[2])
    return major >= 24 or (major == 22 and minor >= 17)


def listener_owned(pid: int, port: int) -> bool:
    try:
        result = subprocess.run(
            [
                "lsof",
                "-nP",
                "-a",
                "-p",
                str(pid),
                f"-iTCP:{port}",
                "-sTCP:LISTEN",
                "-t",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return result.returncode == 0 and str(pid) in result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return False


def ready(name: str, port: int, path: str) -> bool:
    status = contact(paths(name)[0], "status")
    if not status or status.get("state") != "running":
        return False
    pid = status.get("pid")
    if not isinstance(pid, int) or not listener_owned(pid, port):
        return False
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read(65536)
        if response.status != 200:
            return False
        valid = (
            json.loads(body) == {"status": "ready"}
            if name == "api"
            else b"GobbleGobble" in body
        )
        current = contact(paths(name)[0], "status")
        return bool(
            valid
            and current
            and current.get("pid") == pid
            and listener_owned(pid, port)
        )
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def wait_ready(name: str, port: int, path: str) -> bool:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if ready(name, port, path):
            return True
        if contact(paths(name)[0], "status") is None:
            break
        time.sleep(0.2)
    print(
        f"{name} did not become ready on 127.0.0.1:{port}; inspect {paths(name)[2]}",
        file=sys.stderr,
    )
    return False


def app_start() -> int:
    if shutil.which("lsof") is None:
        print("lsof is required to verify listening process ownership", file=sys.stderr)
        return 1
    if not frontend_available():
        print(
            "frontend requires supported Node and installed dependencies",
            file=sys.stderr,
        )
        return 1
    started: list[str] = []
    for name, port, path in (("api", 8000, "/ready"), ("frontend", 5173, "/")):
        if manage_locked("start", name, started) != 0 or not wait_ready(
            name, port, path
        ):
            for created in reversed(started):
                manage_locked("stop", created)
            return 1
    print("app ready: http://127.0.0.1:5173/ (diagnostic: /diagnostic)")
    return 0


def app_stop() -> int:
    names = {
        path.name.removesuffix(".owner") for path in RUNTIME.glob("worker-*.owner")
    }
    names.update(
        path.name.removesuffix(".sock") for path in RUNTIME.glob("worker-*.sock")
    )
    workers = sorted(
        name for name in names if re.fullmatch(r"worker-[0-9a-f]{32}", name)
    )
    failed = False
    for name in ("frontend", *workers, "api"):
        if manage_locked("stop", name) != 0:
            failed = True
    return 1 if failed else 0


def app(action: str) -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "start":
            return app_start()
        if action == "stop":
            return app_stop()
        if action == "restart":
            return app_start() if app_stop() == 0 else 1
        results = [manage_locked("status", name) for name in ("api", "frontend")]
        return 0 if all(result == 0 for result in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage the local API or one diagnostic worker."
    )
    parser.add_argument(
        "service", choices=("api", "frontend", "worker", "app", "_supervise")
    )
    parser.add_argument(
        "action", help="start, status, stop, or internal supervisor identity"
    )
    parser.add_argument(
        "run_id", nargs="?", help="32-character run ID for worker commands"
    )
    args = parser.parse_args()
    if args.service == "_supervise":
        if args.run_id is not None or not re.fullmatch(
            r"api|frontend|worker-[0-9a-f]{32}", args.action
        ):
            parser.error("invalid supervisor identity")
        return supervise(args.action)
    if args.service == "app":
        if args.run_id is not None or args.action not in (
            "start",
            "status",
            "stop",
            "restart",
        ):
            parser.error("app requires start, status, stop, or restart and no run ID")
        return app(args.action)
    if args.action not in ("start", "status", "stop"):
        parser.error("action must be start, status, or stop")
    if args.service in ("api", "frontend"):
        if args.run_id is not None:
            parser.error("service takes no run ID")
        name = args.service
    else:
        if args.run_id is None or not re.fullmatch(r"[0-9a-f]{32}", args.run_id):
            parser.error("worker requires a 32-character lowercase hexadecimal run ID")
        name = f"worker-{args.run_id}"
    return manage(args.action, name)


if __name__ == "__main__":
    raise SystemExit(main())
