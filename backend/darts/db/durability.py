"""Checkpointing, integrity verification, and quarantine of database files.

Clock and filesystem access belong here rather than in the engine, which the
purity guard keeps free of I/O.
"""

import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import pathname2url

from darts.db.connection import connection

# SQLite keeps a database's uncheckpointed log and shared index beside the file.
SIDECARS = ("-wal", "-shm")

# PRAGMA integrity_check stops at 100 problems; one line is enough to log.
_MAX_REPORTED = 5

# Structural damage is reported through these messages. Everything else a
# database error can mean -- a permissions failure, an exhausted disk, a locked
# file, an unsupported migration history -- is an environmental problem, and
# must never cause a healthy database to be quarantined and replaced.
_CORRUPTION = (
    "database disk image is malformed",
    "file is not a database",
    "file is encrypted or is not a database",
    "malformed database schema",
    "database corruption",
)


@dataclass(frozen=True)
class CheckpointResult:
    """What SQLite itself reported, including a refusal it does not raise."""

    busy: bool
    wal_pages: int
    checkpointed_pages: int

    @property
    def truncated(self) -> bool:
        return not self.busy


def timestamp(now: datetime | None = None) -> str:
    """A filename-safe UTC stamp, matching the repository's UTC-everywhere rule."""
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def read_only_uri(path: Path) -> str:
    """Open a file for inspection without upgrading its journal mode.

    Opening a backup read-write would rewrite it to WAL, changing the very
    artifact being checked.
    """
    return f"file:{pathname2url(str(path.resolve()))}?mode=ro"


def checkpoint_truncate(conn: sqlite3.Connection) -> CheckpointResult:
    """Fold the WAL back into the database and truncate it to zero length.

    A busy result is returned rather than raised: SQLite reports busy=1 without
    error when another connection holds a read lock, so a caller that ignored
    the result would report a clean shutdown it did not achieve.
    """
    busy, wal_pages, checkpointed = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return CheckpointResult(bool(busy), int(wal_pages), int(checkpointed))


def integrity_report(conn: sqlite3.Connection) -> str | None:
    """Run both checks #12 requires; None means the database is sound."""
    problems = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
    if problems != ["ok"]:
        return "integrity_check: " + "; ".join(problems[:_MAX_REPORTED])
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        return f"foreign_key_check: {len(violations)} violation(s)"
    return None


def is_corruption(exc: BaseException) -> bool:
    """Distinguish a damaged file from a database that is merely unavailable."""
    return isinstance(exc, sqlite3.DatabaseError) and any(
        marker in str(exc).lower() for marker in _CORRUPTION
    )


def _guarded(check: Callable[[], str | None]) -> str | None:
    """Report corruption as a message; let every other failure propagate."""
    try:
        return check()
    except sqlite3.DatabaseError as exc:
        if is_corruption(exc):
            return str(exc)
        raise


def verify_live(path: Path) -> str | None:
    """Check the database SQLite will actually serve, with the usual PRAGMAs.

    A file damaged badly enough fails while opening, before any check can run,
    so the open is inside the guarded block too.
    """

    def check() -> str | None:
        with connection(path) as conn:
            return integrity_report(conn)

    return _guarded(check)


def verify_file(path: Path) -> str | None:
    """Check a stored backup read-only, leaving the file exactly as it was."""
    if not path.is_file():
        return f"missing: {path}"

    def check() -> str | None:
        with closing(sqlite3.connect(read_only_uri(path), uri=True)) as conn:
            return integrity_report(conn)

    return _guarded(check)


def unused_name(path: Path, label: str, stamp: str) -> Path:
    """A free `<stem>.<label>-<stamp>.db` beside `path`, never an existing file."""
    ordinal = 0
    while True:
        distinguisher = "" if ordinal == 0 else f"-{ordinal}"
        candidate = path.with_name(f"{path.stem}.{label}-{stamp}{distinguisher}{path.suffix}")
        if not candidate.exists():
            return candidate
        ordinal += 1


def quarantine(path: Path, *, label: str = "corrupt", now: datetime | None = None) -> Path:
    """Move a database and its sidecars aside under a timestamped name.

    The -wal and -shm files travel with the main file. Leaving them behind
    would let SQLite replay a stale log over whatever replaces the database,
    and deleting them would discard committed history that has not been
    checkpointed yet -- exactly the history a damaged database most needs.
    Nothing is ever removed, only renamed.
    """
    target = unused_name(path, label, timestamp(now))
    os.replace(path, target)
    for suffix in SIDECARS:
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            os.replace(sidecar, target.with_name(target.name + suffix))
    return target
