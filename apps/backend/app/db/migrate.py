import argparse
import sqlite3
import sys
from collections.abc import Sequence

from app.core.settings import ConfigurationError, load_database_settings
from app.db.migrations import MigrationError, migrate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply local SQLite migrations without provider configuration."
    )
    parser.parse_args(argv)
    try:
        settings = load_database_settings()
        applied = migrate(settings)
    except ConfigurationError:
        print("database_configuration_error", file=sys.stderr)
        return 1
    except MigrationError:
        print("database_migration_history_error", file=sys.stderr)
        return 1
    except OSError:
        print("database_filesystem_error", file=sys.stderr)
        return 1
    except sqlite3.Error:
        print("database_sqlite_error", file=sys.stderr)
        return 1
    if applied:
        print("database_migrations_applied " + ",".join(map(str, applied)))
    else:
        print("database_schema_current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
