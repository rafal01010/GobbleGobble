import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress

from app.core.settings import DatabaseSettings


@contextmanager
def _session(
    settings: DatabaseSettings, *, create: bool = False
) -> Iterator[sqlite3.Connection]:
    mode = "rwc" if create else "rw"
    connection = sqlite3.connect(
        f"{settings.database_path.as_uri()}?mode={mode}",
        uri=True,
        autocommit=True,
        timeout=5.0,
        check_same_thread=True,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON").close()
        cursor = connection.execute("PRAGMA foreign_keys")
        try:
            enabled = cursor.fetchone()[0]
        finally:
            cursor.close()
        if enabled != 1:
            raise sqlite3.OperationalError("foreign key enforcement unavailable")
        yield connection
    finally:
        try:
            if connection.in_transaction:
                with suppress(sqlite3.Error):
                    connection.execute("ROLLBACK").close()
        finally:
            connection.close()


@contextmanager
def session(settings: DatabaseSettings) -> Iterator[sqlite3.Connection]:
    """Open an existing database in the calling thread without starting a transaction."""
    with _session(settings) as connection:
        yield connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Own one short write transaction. Complete all external work outside this block."""
    if connection.in_transaction:
        raise sqlite3.ProgrammingError("nested transactions are not supported")
    connection.execute("BEGIN IMMEDIATE").close()
    try:
        yield
        connection.execute("COMMIT").close()
    except BaseException:
        if connection.in_transaction:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK").close()
        raise
