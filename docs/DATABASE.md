# SQLite storage reference

GG-005.01 defines local SQLite connections and explicit migrations in `apps/backend/app/db`. Migration 1 creates the migration ledger. GG-005.02 appends migration 2 for [anonymous sessions and search ownership](SEARCH_ACCESS.md). GG-005.03 appends migration 3 for [queued runs and owner-scoped command idempotency](RUN_ADMISSION.md). GG-005.04 appends migration 4 for [worker attempts and current leases](WORKER_LEASES.md). Events and result tables belong to later GG-005 children.

## Configuration

`load_database_settings()` in `app/core/settings.py` returns frozen `DatabaseSettings`. It reads `GG_DATABASE_PATH` from `apps/backend/.env` and then the process environment. It needs no mode, provider, model, credential, or network access. The [settings reference](SETTINGS.md#independent-local-database-settings) defines shared dotenv admission and errors.

The default database path is `<repository>/data/runtime/gobblegobble.sqlite3`. Relative paths resolve against the repository root regardless of the working directory. Blank paths, colon syntax including `:memory:` and URI names, and NUL characters are invalid. Paths can name absolute files. A directory fails when SQLite opens it. Loading settings does not create storage.

`data/runtime/` and standard `.sqlite3` journal, WAL, and shared-memory sidecars are ignored by Git. Custom database base files elsewhere inside the repository are not automatically ignored. Configuring an absolute path outside the repository makes that location the operator's responsibility.

## Connections and transactions

`app.db.session.session(settings)` opens an existing file using an encoded SQLite file URI with `mode=rw`. A missing database fails instead of creating an unmigrated file. Only `migrate()` creates the database directory and file. Imports have no storage side effects.

Each session owns a fresh connection in the calling thread. It uses `autocommit=True`, `timeout=5.0`, `check_same_thread=True`, and `sqlite3.Row`. It enables and checks foreign-key enforcement before yielding the connection. Python's default type conversion remains disabled. No pool or global connection exists. Sessions always close their connection and roll back an unfinished transaction before closing.

`transaction(connection)` rejects nesting, executes `BEGIN IMMEDIATE`, and executes SQL `COMMIT` on success. An escaping exception, including commit failure, causes SQL `ROLLBACK` while the connection still has an active transaction. Cleanup preserves the original exception. Connection `commit()` and `rollback()` are ineffective with `autocommit=True`, so the helper uses SQL commands. The [Python sqlite3 transaction reference](https://docs.python.org/3.14/library/sqlite3.html#transaction-control) documents this distinction.

Repository operations share the caller's connection and transaction boundary. They bind values through SQL placeholders. A transaction must contain only short local database operations. Finish the transaction before provider calls, model calls, sleeps, user interaction, or streaming. Close read cursors before returning. The helper cannot prevent callers from performing external work, so this remains a caller contract.

A future async handler must run each complete synchronous database operation in one worker thread. A connection cannot cross an `await` or move between threads. The [SQLite connection reference](https://docs.python.org/3.14/library/sqlite3.html#sqlite3.connect) defines the timeout, URI, and thread options. This task enables no WAL mode and adds no automatic retries beyond SQLite's five-second lock timeout.

Example usage for a later repository is:

```python
from app.core.settings import load_database_settings
from app.db.session import session, transaction

settings = load_database_settings()
with session(settings) as connection:
	with transaction(connection):
		connection.execute(statement, parameters).close()
```

`statement` and `parameters` come from the repository operation. The caller completes all external work after leaving these contexts.

## Migration history

`app/db/migrations.py` owns the ordered immutable `MIGRATIONS` tuple. Each frozen `Migration` holds its integer version, name, and tuple of individual SQL statements. Its SHA-256 checksum covers JSON containing the version, name, and exact statement strings. Versions start at 1 and remain contiguous. Later changes append records. Applied names and SQL must never change.

Migration 1, `create_migration_ledger`, creates `schema_migrations` with these columns:

| Column | Definition |
|---|---|
| `version` | `INTEGER PRIMARY KEY` |
| `name` | `TEXT NOT NULL` |
| `checksum` | `TEXT NOT NULL` |

The ledger contains no timestamp. Migration acquires `BEGIN IMMEDIATE` before reading schema or history. An empty database gets the ledger and its first row in the same transaction. An existing ledger must have the expected columns and an exact nonempty prefix of the manifest's versions, names, and checksums. The runner rejects missing versions, unknown future versions, altered history, an invalid ledger, or a nonempty unversioned schema. It never stamps, resets, deletes, or downgrades an unknown database.

Migration 2, `create_anonymous_search_ownership`, creates `anonymous_sessions`, `search_ownership`, and `search_ownership_owner_idx`. The [search access reference](SEARCH_ACCESS.md#ownership-model) records their columns and constraints. Migration 3, `create_queued_run_admission`, adds `runs`, `run_commands`, the composite ownership parent index, and the unique start-command index. The [admission reference](RUN_ADMISSION.md#storage-and-privacy) records their fields and constraints. Composite foreign keys enforce command owner-to-search consistency and run-to-search membership. Migration 4, `create_worker_attempts_and_leases`, adds immutable attempt history and one current lease row per run. The [worker lease reference](WORKER_LEASES.md#storage) records the columns and constraints. A fresh database applies migrations 1 through 4 together. Earlier-version databases retain their ledger rows and apply only their pending suffix.

The runner executes each statement individually and inserts the corresponding ledger row after its statements succeed. It commits all pending migrations together. A failure rolls back the pending SQL and ledger changes. Directory or empty-file creation can survive failure, and a retry can initialize that empty file. Concurrent migration processes serialize through the write lock before checking pending work. A valid current database applies no statements and returns an empty tuple.

Migration entries must not include transaction commands, `VACUUM`, journal-mode changes, or other statements incompatible with the outer atomic transaction. The runner does not split SQL or use `executescript()`. History checks detect edited migration records. They do not attest to manual changes to future application tables.

## Command and exit codes

After backend environment initialization, the wrapper resolves the backend directory from its own location:

```sh
./scripts/local/migrate-db.sh
```

The equivalent command from `apps/backend` is:

```sh
.venv/bin/python -m app.db.migrate
```

Both commands use `GG_DATABASE_PATH` and accept only the standard `-h` and `--help` options. They do not start HTTP services or load provider settings. A successful first migration prints `database_migrations_applied 1,2,3,4`. An upgrade from version 1 prints `database_migrations_applied 2,3,4`. An upgrade from version 2 prints `database_migrations_applied 3,4`. An upgrade from version 3 prints `database_migrations_applied 4`. A current database prints `database_schema_current`. These are the current manifest's expected outputs; database execution remains at GG-007.

| Exit code | Meaning |
|---|---|
| `0` | The pending migrations committed, or the schema was current. Help also exits successfully. |
| `1` | Configuration, filesystem, SQLite, or migration-history failure. |
| `2` | An unsupported command argument. |

Operational errors print one fixed code to stderr. The codes are `database_configuration_error`, `database_filesystem_error`, `database_sqlite_error`, and `database_migration_history_error`. They omit configured paths, submitted values, credentials, and SQL text. Library callers receive `ConfigurationError`, `MigrationError`, or the underlying filesystem or SQLite exception. Shell failures to find the initialized interpreter retain the shell's exit code.

## Verification boundary

GG-005.01 implementation checks cover imports, credential-free settings and dotenv projection, syntax, targeted lint, CLI help, and invalid arguments. They do not execute migrations or inspect a database schema. GG-007 must migrate an empty database, inspect the ledger and the schema expected from the then-current migration manifest, repeat the migration, check path independence, exercise invalid history and concurrent attempts, and prove failed migrations leave no committed partial schema or ledger changes. Broad backend checks remain at GG-007. No live calls ran for this child, and GG-007 does not authorize provider or model calls.

GG-005.03 adds caller-owned queued admission and current-snapshot replay. Its pure checks do not execute SQL. GG-007 must prove owner-scoped identical and conflicting commands, constraints, concurrent admission, rollback, restart, and replay after progress. See [expected admission observations](RUN_ADMISSION.md#expected-observations-and-deferred-verification).

GG-005.04 adds fresh SQLite millisecond expiry fences for claim, renewal, and running-stage mutation. Its pure checks execute no SQL. GG-007 owns migration, duplicate claim, expiry, stale ownership, rollback, and restart proof. See [worker lease transitions and verification](WORKER_LEASES.md).
