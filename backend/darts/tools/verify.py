"""Recompute every derived value from the raw darts and report any drift."""

import argparse
import sqlite3
import sys
from pathlib import Path

from darts.db.connection import connection
from darts.services.verify import counts, verify_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darts-verify", description=__doc__)
    parser.add_argument("database", type=Path, help="database file to check")
    parser.add_argument("--quiet", action="store_true", help="print nothing when everything agrees")
    args = parser.parse_args(argv)
    try:
        with connection(args.database) as conn:
            matches, legs = counts(conn)
            drifts = verify_database(conn)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        return 1
    for drift in drifts:
        print(drift, file=sys.stderr)
    if drifts:
        print(f"{len(drifts)} drifted value(s) across {matches} match(es)", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"no drift: {legs} leg(s) across {matches} match(es) agree with their darts")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
