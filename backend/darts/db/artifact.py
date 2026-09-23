"""Producing, publishing and describing a self-contained copy of a database.

Two callers want the same four guarantees and differ only in where the copy
goes and what it is called. `darts.db.backup` writes a timestamped, rotated
history; `darts.services.snapshot` republishes one fixed filename for #30's
Samba share. The guarantees live here so neither has to restate them, and so
that a fix to one is a fix to both:

* the copy is taken through `Connection.backup()`, which reads pages inside a
  read transaction, so an artifact taken mid-game is a point-in-time image
  rather than the torn file `cp` would produce;
* its WAL is collapsed, so what is published is one self-contained file. A
  `-wal` sidecar beside it would hold committed pages that `os.replace` does not
  move, and the artifact would silently lose them;
* it is written to a temporary file **in the destination directory** and
  published with `os.replace`, so publishing is a same-filesystem rename and a
  reader sees either the previous artifact or the new one, never a partial;
* its manifest is read back out of the *finished* artifact, so the row counts
  describe what was actually written rather than what the live database held
  while it was being written.
"""

import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from darts.db.durability import SIDECARS, read_only_uri, verify_file


class ArtifactError(ValueError):
    """A database copy could not be produced, or could not safely be used."""


def discard(temporary: Path) -> None:
    """Remove an unpublished temporary copy and anything SQLite left beside it."""
    for suffix in ("", *SIDECARS):
        temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)


def collapse_wal(conn: sqlite3.Connection) -> None:
    """Leave the copy as one self-contained file.

    A WAL sidecar beside a published artifact would hold committed pages that
    os.replace does not move, so the artifact would silently lose them.
    """
    mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    if mode != "delete":
        raise ArtifactError(f"could not collapse the copy's WAL (journal_mode={mode})")


def copy_into_temp(
    source: sqlite3.Connection, directory: Path, *, prefix: str = ".darts-artifact-"
) -> Path:
    """Copy a database through sqlite3's backup API into a fresh temporary file.

    The backup API copies pages inside a read transaction, so a copy taken while
    a game is in progress is a point-in-time image. The temporary lives in the
    destination directory so publishing it is a same-filesystem rename.
    """
    handle, name = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".db")
    os.close(handle)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary, isolation_level=None)) as copy:
            source.backup(copy)
            collapse_wal(copy)
    except BaseException:
        discard(temporary)
        raise
    return temporary


def publish(temporary: Path, target: Path) -> None:
    """Rename a finished temporary into place, leaving nothing behind if it fails."""
    try:
        os.replace(temporary, target)
    except BaseException:
        discard(temporary)
        raise


def describe(artifact: Path, *, source: Path, created_at: str) -> dict[str, Any]:
    """Build a manifest from the finished copy, not from the live database.

    The manifest has to describe the artifact somebody will later restore or
    open, which is why the counts are read back out of it. Callers add their own
    name for the file; everything else about it is here.
    """
    problem = verify_file(artifact)
    if problem is not None:
        raise ArtifactError(f"the copy failed its integrity check: {problem}")
    with closing(sqlite3.connect(read_only_uri(artifact), uri=True)) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        names = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        # Table names come from the database's own schema, never from a caller.
        counts = {
            name: int(conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
            for name in names
        }
    return {
        "source": str(source),
        "created_at": created_at,
        "schema_version": version,
        "size_bytes": artifact.stat().st_size,
        "row_counts": counts,
    }


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish a manifest the same way its artifact was published: atomically."""
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=".darts-manifest-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(name, path)
