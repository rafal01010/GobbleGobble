import hashlib
import json
import sqlite3
from dataclasses import dataclass

from app.core.settings import DatabaseSettings
from app.db.session import _session, transaction


class MigrationError(Exception):
    """The migration manifest or durable history does not match this backend."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = json.dumps(
            [self.version, self.name, self.statements],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="create_migration_ledger",
        statements=(
            (
                "CREATE TABLE schema_migrations ("
                "version INTEGER PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "checksum TEXT NOT NULL)"
            ),
        ),
    ),
    Migration(
        version=2,
        name="create_anonymous_search_ownership",
        statements=(
            (
                "CREATE TABLE anonymous_sessions ("
                "session_id TEXT PRIMARY KEY NOT NULL, "
                "credential_digest TEXT NOT NULL UNIQUE "
                "CHECK(length(credential_digest) = 64))"
            ),
            (
                "CREATE TABLE search_ownership ("
                "search_id TEXT PRIMARY KEY NOT NULL, "
                "owner_session_id TEXT NOT NULL "
                "REFERENCES anonymous_sessions(session_id))"
            ),
            "CREATE INDEX search_ownership_owner_idx ON search_ownership(owner_session_id)",
        ),
    ),
    Migration(
        version=3,
        name="create_queued_run_admission",
        statements=(
            (
                "CREATE UNIQUE INDEX search_ownership_identity_owner_idx "
                "ON search_ownership(search_id, owner_session_id)"
            ),
            (
                "CREATE TABLE runs ("
                "run_id TEXT PRIMARY KEY NOT NULL, "
                "search_id TEXT NOT NULL REFERENCES search_ownership(search_id), "
                "snapshot_json TEXT NOT NULL, "
                "UNIQUE(search_id, run_id))"
            ),
            (
                "CREATE TABLE run_commands ("
                "owner_session_id TEXT NOT NULL, "
                "command_id TEXT NOT NULL, "
                "command_kind TEXT NOT NULL, "
                "canonical_payload_json TEXT NOT NULL, "
                "search_id TEXT NOT NULL, "
                "run_id TEXT NOT NULL, "
                "PRIMARY KEY(owner_session_id, command_id), "
                "FOREIGN KEY(search_id, owner_session_id) "
                "REFERENCES search_ownership(search_id, owner_session_id), "
                "FOREIGN KEY(search_id, run_id) REFERENCES runs(search_id, run_id))"
            ),
            (
                "CREATE UNIQUE INDEX run_commands_start_run_idx "
                "ON run_commands(run_id) WHERE command_kind = 'start'"
            ),
        ),
    ),
    Migration(
        version=4,
        name="create_worker_attempts_and_leases",
        statements=(
            (
                "CREATE TABLE run_attempts ("
                "attempt_id TEXT PRIMARY KEY NOT NULL, "
                "run_id TEXT NOT NULL REFERENCES runs(run_id), "
                "claimed_at_ms INTEGER NOT NULL "
                "CHECK(typeof(claimed_at_ms) = 'integer' AND claimed_at_ms >= 0), "
                "UNIQUE(run_id, attempt_id))"
            ),
            (
                "CREATE TABLE run_leases ("
                "run_id TEXT PRIMARY KEY NOT NULL REFERENCES runs(run_id), "
                "attempt_id TEXT NOT NULL, "
                "worker_id TEXT NOT NULL CHECK(length(trim(worker_id)) > 0), "
                "credential_digest TEXT NOT NULL "
                "CHECK(length(credential_digest) = 64 "
                "AND credential_digest NOT GLOB '*[^0-9a-f]*'), "
                "expires_at_ms INTEGER NOT NULL "
                "CHECK(typeof(expires_at_ms) = 'integer' AND expires_at_ms >= 0), "
                "FOREIGN KEY(run_id, attempt_id) "
                "REFERENCES run_attempts(run_id, attempt_id))"
            ),
        ),
    ),
)


def _applied_count(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        "SELECT type, name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'"
    )
    try:
        objects = {(row["type"], row["name"]) for row in cursor.fetchall()}
    finally:
        cursor.close()
    if ("table", "schema_migrations") not in objects:
        if objects:
            raise MigrationError("unversioned_schema")
        return 0

    cursor = connection.execute("PRAGMA table_info(schema_migrations)")
    try:
        columns = tuple(
            (row["name"], row["type"], row["notnull"], row["dflt_value"], row["pk"])
            for row in cursor.fetchall()
        )
    finally:
        cursor.close()
    if columns != (
        ("version", "INTEGER", 0, None, 1),
        ("name", "TEXT", 1, None, 0),
        ("checksum", "TEXT", 1, None, 0),
    ):
        raise MigrationError("invalid_ledger")

    cursor = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    )
    try:
        history = tuple(tuple(row) for row in cursor.fetchall())
    finally:
        cursor.close()
    expected = tuple(
        (migration.version, migration.name, migration.checksum)
        for migration in MIGRATIONS
    )
    if (
        not history
        or len(history) > len(expected)
        or history != expected[: len(history)]
    ):
        raise MigrationError("history_mismatch")
    return len(history)


def migrate(settings: DatabaseSettings) -> tuple[int, ...]:
    """Apply the pending manifest atomically after acquiring the SQLite write lock."""
    if tuple(migration.version for migration in MIGRATIONS) != tuple(
        range(1, len(MIGRATIONS) + 1)
    ):
        raise MigrationError("invalid_manifest")
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with _session(settings, create=True) as connection, transaction(connection):
        pending = MIGRATIONS[_applied_count(connection) :]
        for migration in pending:
            for statement in migration.statements:
                connection.execute(statement).close()
            connection.execute(
                "INSERT INTO schema_migrations (version, name, checksum) "
                "VALUES (?, ?, ?)",
                (migration.version, migration.name, migration.checksum),
            ).close()
    return tuple(migration.version for migration in pending)
