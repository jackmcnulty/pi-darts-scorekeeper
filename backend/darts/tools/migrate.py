"""Apply packaged SQLite migrations and views to an explicitly selected file."""

import argparse
import sqlite3
import sys
from pathlib import Path

from darts.db.connection import connection
from darts.db.migrate import migrate
from darts.db.views import install_views


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darts-migrate", description=__doc__)
    parser.add_argument("database", type=Path, help="database file (parent directory must exist)")
    args = parser.parse_args(argv)
    try:
        with connection(args.database) as conn:
            applied = migrate(conn)
            # Views are rebuilt on every run, migrations or not: they are not
            # in the ledger, so this is the only thing that keeps them current.
            views = install_views(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    print(f"schema version {version}; applied {len(applied)} migration(s); {len(views)} view(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
