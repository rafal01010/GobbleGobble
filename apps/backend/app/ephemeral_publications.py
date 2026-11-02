from __future__ import annotations

import fcntl
import json
import os
import re
import socket
import socketserver
import stat
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Self, cast

_KEY = re.compile(r"ver_[0-9a-f]{32}\Z")
_HEADER = struct.Struct("!I")
_MAX_REQUEST = 384 * 1024
_MAX_PAYLOAD = 256 * 1024
_MAX_ITEMS = 32


class PublicationBrokerError(Exception):
    pass


def _receive(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise PublicationBrokerError("incomplete_message")
        chunks.extend(chunk)
    return bytes(chunks)


def _read(sock: socket.socket) -> dict[str, Any]:
    length = _HEADER.unpack(_receive(sock, _HEADER.size))[0]
    if not 0 < length <= _MAX_REQUEST:
        raise PublicationBrokerError("message_too_large")
    try:
        value = json.loads(_receive(sock, length))
    except ValueError, UnicodeDecodeError:
        raise PublicationBrokerError("invalid_message") from None
    if not isinstance(value, dict):
        raise PublicationBrokerError("invalid_message")
    return value


def _write(sock: socket.socket, value: dict[str, Any]) -> None:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(encoded) > _MAX_REQUEST:
        raise PublicationBrokerError("message_too_large")
    sock.sendall(_HEADER.pack(len(encoded)) + encoded)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        sock = self.request
        sock.settimeout(2)
        try:
            response = cast(_Server, self.server).broker._dispatch(_read(sock))
            _write(sock, response)
        except PublicationBrokerError, OSError, ValueError, TypeError:
            try:
                _write(sock, {"status": "error"})
            except PublicationBrokerError, OSError:
                pass


class _Server(socketserver.UnixStreamServer):
    def __init__(self, path: str, broker: PublicationBroker) -> None:
        self.broker = broker
        super().__init__(path, _Handler)

    def service_actions(self) -> None:
        self.broker._expire()


class PublicationBroker:
    """Hold bounded publication overlays in memory behind a private local socket."""

    def __init__(self, path: Path, *, ttl_seconds: float = 300) -> None:
        if not 0 < ttl_seconds <= 3600:
            raise ValueError("invalid_ttl")
        directory = path.parent
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("socket_directory_must_be_private")
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[float, object]] = OrderedDict()
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        self._lock = os.open(directory / "broker.lock", flags, 0o600)
        try:
            lock_info = os.fstat(self._lock)
            if (
                not stat.S_ISREG(lock_info.st_mode)
                or lock_info.st_uid != os.getuid()
                or stat.S_IMODE(lock_info.st_mode) != 0o600
            ):
                raise ValueError("invalid_broker_lock")
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if path.exists():
                existing = path.lstat()
                if (
                    not stat.S_ISSOCK(existing.st_mode)
                    or existing.st_uid != os.getuid()
                ):
                    raise ValueError("invalid_broker_socket")
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(1)
                    try:
                        probe.connect(str(path))
                    except ConnectionRefusedError:
                        path.unlink()
                    else:
                        raise ValueError("broker_already_running")
            self._server = _Server(str(path), self)
            os.chmod(path, 0o600)
        except BaseException:
            os.close(self._lock)
            raise
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
        )
        self._thread.start()

    def _expire(self) -> None:
        now = time.monotonic()
        for item_key, (expires, _) in list(self._items.items()):
            if expires <= now:
                del self._items[item_key]

    def _dispatch(self, message: dict[str, Any]) -> dict[str, Any]:
        self._expire()
        now = time.monotonic()
        op = message.get("op")
        key = message.get("key")
        if not isinstance(key, str) or _KEY.fullmatch(key) is None:
            return {"status": "error"}
        if op == "get" and set(message) == {"op", "key"}:
            item = self._items.get(key)
            return (
                {"status": "missing"}
                if item is None
                else {"status": "ok", "payload": item[1]}
            )
        if op == "put" and set(message) == {"op", "key", "payload"}:
            payload = message["payload"]
            encoded = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode()
            if (
                len(encoded) > _MAX_PAYLOAD
                or key in self._items
                or len(self._items) >= _MAX_ITEMS
            ):
                return {"status": "error"}
            self._items[key] = (now + self.ttl_seconds, payload)
            return {"status": "ok"}
        return {"status": "error"}

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
        self._items.clear()
        self.path.unlink(missing_ok=True)
        os.close(self._lock)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def exchange(path: Path, message: dict[str, Any]) -> dict[str, Any]:
    """Send one bounded message to a broker; never invoke a provider."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect(str(path))
            _write(sock, message)
            return _read(sock)
    except OSError, PublicationBrokerError:
        raise PublicationBrokerError("broker_unavailable") from None
