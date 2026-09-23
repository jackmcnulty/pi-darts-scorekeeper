"""Publish darts-latest.db and its manifest into the snapshot directory."""

import argparse
import sqlite3
import sys
from pathlib import Path

from darts.services.snapshot import create, default_snapshot_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darts-snapshot", description=__doc__)
    parser.add_argument("database", type=Path, help="live database file")
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=None,
        help="destination directory (default: a snapshots/ directory beside the database)",
    )
    args = parser.parse_args(argv)
    directory = (
        args.snapshot_dir if args.snapshot_dir is not None else default_snapshot_dir(args.database)
    )
    try:
        result = create(args.database, directory)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1
    rows = sum(result.row_counts.values())
    print(
        f"wrote {result.path} (schema version {result.schema_version}, "
        f"{rows} row(s), {result.size_bytes} bytes); manifest {result.manifest_path}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
