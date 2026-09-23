"""Restore a named or newest-valid backup over a chosen database."""

import argparse
import sqlite3
import sys
from pathlib import Path

from darts.db.backup import newest_valid, restore


def _confirmed(database: Path) -> bool:
    """Refuse to be destructive in the dark.

    With no terminal attached there is nobody to ask, so an unattended caller
    has to say --force rather than have consent assumed for it.
    """
    if not sys.stdin.isatty():
        print(f"{database} exists; re-run with --force to overwrite it", file=sys.stderr)
        return False
    return input(f"Overwrite {database}? [y/N] ").strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darts-restore", description=__doc__)
    parser.add_argument("database", type=Path, help="database file to replace")
    parser.add_argument(
        "--from",
        dest="source",
        type=Path,
        default=None,
        help="backup to restore (default: the newest backup that passes integrity_check)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="directory to search (default: a backups/ directory beside the database)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite without confirmation")
    args = parser.parse_args(argv)

    source = args.source
    if source is None:
        candidate = newest_valid(args.database, backup_dir=args.backup_dir)
        if candidate is None:
            print(f"no valid backup found for {args.database}", file=sys.stderr)
            return 1
        source = candidate.path
    if args.database.exists() and not args.force and not _confirmed(args.database):
        print("aborted", file=sys.stderr)
        return 1
    try:
        replaced = restore(args.database, source)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    kept = f"; previous database kept at {replaced}" if replaced is not None else ""
    print(f"restored {args.database} from {source}{kept}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
