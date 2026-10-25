import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from uuid import uuid4


class InvalidSession(Exception):
    def __init__(self) -> None:
        super().__init__("invalid_session")


class SearchNotFound(Exception):
    def __init__(self) -> None:
        super().__init__("not_found")


@dataclass(frozen=True)
class SessionPrincipal:
    _session_id: str = field(repr=False)


@dataclass(frozen=True)
class IssuedSession:
    credential: str = field(repr=False)
    principal: SessionPrincipal = field(repr=False)


@dataclass(frozen=True)
class OwnedSearch:
    search_id: str = field(repr=False)


def _credential_digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 43
        or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None
    ):
        raise InvalidSession()
    return hashlib.sha256(
        b"gobblegobble-anonymous-v1\0" + value.encode("ascii")
    ).hexdigest()


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise sqlite3.ProgrammingError("ownership writes require a transaction")


def issue_session(connection: sqlite3.Connection) -> IssuedSession:
    """Issue a credential only for an explicitly absent browser credential."""
    _require_transaction(connection)
    credential = secrets.token_urlsafe(32)
    principal = SessionPrincipal(uuid4().hex)
    connection.execute(
        "INSERT INTO anonymous_sessions (session_id, credential_digest) VALUES (?, ?)",
        (principal._session_id, _credential_digest(credential)),
    ).close()
    return IssuedSession(credential, principal)


def resolve_session(
    connection: sqlite3.Connection, credential: object
) -> SessionPrincipal:
    """Resolve a supplied credential, denying malformed and unknown values alike."""
    digest = _credential_digest(credential)
    cursor = connection.execute(
        "SELECT session_id FROM anonymous_sessions WHERE credential_digest = ?",
        (digest,),
    )
    try:
        row = cursor.fetchone()
    finally:
        cursor.close()
    if row is None:
        raise InvalidSession()
    return SessionPrincipal(row["session_id"])


def create_search(
    connection: sqlite3.Connection, principal: SessionPrincipal
) -> OwnedSearch:
    """Bind a new server-generated search identity to the resolved session."""
    _require_transaction(connection)
    search = OwnedSearch(uuid4().hex)
    cursor = connection.execute(
        "INSERT INTO search_ownership (search_id, owner_session_id) "
        "SELECT ?, session_id FROM anonymous_sessions WHERE session_id = ?",
        (search.search_id, principal._session_id),
    )
    try:
        inserted = cursor.rowcount
    finally:
        cursor.close()
    if inserted != 1:
        raise InvalidSession()
    return search


def require_search_access(
    connection: sqlite3.Connection, credential: object, search_id: str
) -> OwnedSearch:
    """Resolve browser identity and authorize this search, never a supplied run ID."""
    principal = resolve_session(connection, credential)
    cursor = connection.execute(
        "SELECT search_id FROM search_ownership "
        "WHERE search_id = ? AND owner_session_id = ?",
        (search_id, principal._session_id),
    )
    try:
        row = cursor.fetchone()
    finally:
        cursor.close()
    if row is None:
        raise SearchNotFound()
    return OwnedSearch(row["search_id"])
