import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from app.ephemeral_publications import (
    PublicationBroker,
    PublicationBrokerError,
    exchange,
)

KEY = "ver_" + "a" * 32


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="gg") as directory:
        yield Path(directory)


def test_socket_delivers_and_expires_without_file_content(socket_dir: Path):
    os.chmod(socket_dir, 0o700)
    path = socket_dir / "publication.sock"
    with PublicationBroker(path, ttl_seconds=0.05):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert exchange(path, {"op": "get", "key": KEY}) == {"status": "missing"}
        payload = {"findings": ["ephemeral review finding"]}
        assert exchange(path, {"op": "put", "key": KEY, "payload": payload}) == {
            "status": "ok"
        }
        assert exchange(path, {"op": "get", "key": KEY}) == {
            "status": "ok",
            "payload": payload,
        }
        assert exchange(path, {"op": "put", "key": KEY, "payload": {}}) == {
            "status": "error"
        }
        time.sleep(0.07)
        assert exchange(path, {"op": "get", "key": KEY}) == {"status": "missing"}
    assert not path.exists()
    assert [item.name for item in socket_dir.iterdir()] == ["broker.lock"]
    with pytest.raises(PublicationBrokerError):
        exchange(path, {"op": "get", "key": KEY})


def test_broker_bounds_and_private_directory(socket_dir: Path):
    path = socket_dir / "publication.sock"
    os.chmod(socket_dir, 0o755)
    with pytest.raises(ValueError, match="private"):
        PublicationBroker(path)
    os.chmod(socket_dir, 0o700)
    with PublicationBroker(path):
        assert exchange(path, {"op": "put", "key": "bad", "payload": {}}) == {
            "status": "error"
        }
        assert exchange(
            path, {"op": "put", "key": KEY, "payload": "x" * (256 * 1024 + 1)}
        ) == {"status": "error"}
        for number in range(32):
            key = f"ver_{number:032x}"
            assert exchange(path, {"op": "put", "key": key, "payload": number}) == {
                "status": "ok"
            }
        assert exchange(path, {"op": "put", "key": KEY, "payload": {}}) == {
            "status": "error"
        }


def test_separate_process_can_submit(socket_dir: Path):
    path = socket_dir / "p.sock"
    with PublicationBroker(path):
        code = (
            "from pathlib import Path; from app.ephemeral_publications import exchange; "
            f"result = exchange(Path({str(path)!r}), "
            f"{{'op': 'put', 'key': {KEY!r}, 'payload': {{'text': 'from worker'}}}}); "
            "assert result == {'status': 'ok'}"
        )
        subprocess.run([sys.executable, "-c", code], check=True)
        assert exchange(path, {"op": "get", "key": KEY}) == {
            "status": "ok",
            "payload": {"text": "from worker"},
        }


def test_api_lifespan_and_restart(socket_dir: Path):
    import socket

    from fastapi.testclient import TestClient

    from app.api.application import create_app
    from app.core.settings import ApiSettings, DatabaseSettings

    path = socket_dir / "p.sock"
    with socket.socket(socket.AF_UNIX) as stale:
        stale.bind(str(path))
    app = create_app(
        api_settings=ApiSettings(),
        database_settings=DatabaseSettings(database_path=socket_dir / "db.sqlite3"),
        publication_socket=path,
    )
    with TestClient(app):
        assert exchange(path, {"op": "put", "key": KEY, "payload": {"x": 1}}) == {
            "status": "ok"
        }
        with pytest.raises(OSError):
            PublicationBroker(path)
    assert not path.exists()
    with TestClient(app):
        assert exchange(path, {"op": "get", "key": KEY}) == {"status": "missing"}
