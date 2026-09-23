"""Take a consistent, atomically published backup of a chosen database."""

import argparse
import sqlite3
import sys
from pathlib import Path

from darts.db.backup import DEFAULT_DAILY, DEFAULT_HOURLY, create


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darts-backup", description=__doc__)
    parser.add_argument("database", type=Path, help="live database file")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="destination directory (default: a backups/ directory beside the database)",
    )
    parser.add_argument(
        "--hourly", type=int, default=DEFAULT_HOURLY, help="hour buckets to keep (default: 24)"
    )
    parser.add_argument(
        "--daily", type=int, default=DEFAULT_DAILY, help="day buckets to keep (default: 30)"
    )
    parser.add_argument("--no-prune", action="store_true", help="keep every existing backup")
    args = parser.parse_args(argv)
    try:
        result = create(
            args.database,
            backup_dir=args.backup_dir,
            hourly=args.hourly,
            daily=args.daily,
            prune_old=not args.no_prune,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    rows = sum(result.manifest["row_counts"].values())
    print(
        f"wrote {result.backup.path} (schema version {result.manifest['schema_version']}, "
        f"{rows} row(s)); pruned {len(result.pruned)}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
