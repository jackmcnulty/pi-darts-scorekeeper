"""Consistent backups, atomic publication, bucketed retention, and restore."""

import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from darts.db.connection import connection
from darts.db.durability import (
    SIDECARS,
    quarantine,
    read_only_uri,
    timestamp,
    verify_file,
)

DEFAULT_HOURLY = 24
DEFAULT_DAILY = 30

_SUFFIX = re.compile(r"\A(\d{8}T\d{6}Z)(?:-(\d+))?\.db\Z")


class BackupError(ValueError):
    """A backup could not be taken, or could not safely be restored."""


@dataclass(frozen=True, order=True)
class Backup:
    """Ordered by the stamp in its own filename, so sorting is chronological."""

    stamp: str
    ordinal: int
    path: Path = field(compare=False)

    @property
    def taken_at(self) -> datetime:
        return datetime.strptime(self.stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)

    @property
    def manifest_path(self) -> Path:
        return self.path.with_name(self.path.name + ".json")

    @property
    def hour_bucket(self) -> str:
        return self.stamp[:11]

    @property
    def day_bucket(self) -> str:
        return self.stamp[:8]


@dataclass(frozen=True)
class BackupResult:
    backup: Backup
    manifest: dict[str, Any]
    pruned: tuple[Path, ...]


def default_backup_dir(database: Path) -> Path:
    """Backups sit beside the database, so relocating one relocates both.

    #16 owns the Settings object that will make this configurable; until then
    the CLI's --backup-dir is the override.
    """
    return database.parent / "backups"


def discover(backup_dir: Path, stem: str) -> tuple[Backup, ...]:
    """Every parseable backup for `stem`, oldest first.

    Order comes from the timestamp in the filename, never from mtime: restoring
    or copying a backup rewrites mtime, while the name is what the manifest
    agrees with.
    """
    if not backup_dir.is_dir():
        return ()
    found = []
    for path in backup_dir.glob(f"{stem}-*.db"):
        match = _SUFFIX.fullmatch(path.name[len(stem) + 1 :])
        if match is not None:
            found.append(Backup(match[1], int(match[2] or 0), path))
    return tuple(sorted(found))


def newest_valid(database: Path, *, backup_dir: Path | None = None) -> Backup | None:
    """The newest backup that itself passes integrity_check.

    A damaged backup is skipped rather than allowed to block recovery, which is
    the whole point of keeping more than one.
    """
    directory = backup_dir if backup_dir is not None else default_backup_dir(database)
    for backup in reversed(discover(directory, database.stem)):
        if verify_file(backup.path) is None:
            return backup
    return None


def _discard(temporary: Path) -> None:
    """Remove an unpublished temporary copy and anything SQLite left beside it."""
    for suffix in ("", *SIDECARS):
        temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)


def _collapse_wal(conn: sqlite3.Connection) -> None:
    """Leave the copy as one self-contained file.

    A WAL sidecar beside a published backup would hold committed pages that
    os.replace does not move, so the backup would silently lose them.
    """
    mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    if mode != "delete":
        raise BackupError(f"could not collapse the backup WAL (journal_mode={mode})")


def _copy_into_temp(source: sqlite3.Connection, directory: Path) -> Path:
    """Copy a database through sqlite3's backup API into a fresh temporary file.

    The backup API copies pages inside a read transaction, so a snapshot taken
    while a game is in progress is a point-in-time image. `cp` would capture a
    torn file. The temporary lives in the destination directory so publishing
    it is a same-filesystem rename.
    """
    handle, name = tempfile.mkstemp(dir=directory, prefix=".darts-backup-", suffix=".db")
    os.close(handle)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary, isolation_level=None)) as copy:
            source.backup(copy)
            _collapse_wal(copy)
    except BaseException:
        _discard(temporary)
        raise
    return temporary


def _describe(snapshot: Path, database: Path, target: Backup) -> dict[str, Any]:
    """Build the manifest from the finished copy, not from the live database.

    The manifest has to describe the artifact an operator will later restore,
    which is why the counts are read back out of it.
    """
    problem = verify_file(snapshot)
    if problem is not None:
        raise BackupError(f"the snapshot failed its integrity check: {problem}")
    with closing(sqlite3.connect(read_only_uri(snapshot), uri=True)) as conn:
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
        "backup": target.path.name,
        "source": str(database),
        "created_at": target.stamp,
        "schema_version": version,
        "size_bytes": snapshot.stat().st_size,
        "row_counts": counts,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=".darts-manifest-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(name, path)


def _unused_backup(directory: Path, stem: str, stamp: str) -> Backup:
    """Two backups inside one second stay distinguishable and orderable."""
    ordinal = 0
    while True:
        distinguisher = "" if ordinal == 0 else f"-{ordinal}"
        candidate = directory / f"{stem}-{stamp}{distinguisher}.db"
        if not candidate.exists():
            return Backup(stamp, ordinal, candidate)
        ordinal += 1


def create(
    database: Path,
    *,
    backup_dir: Path | None = None,
    now: datetime | None = None,
    hourly: int = DEFAULT_HOURLY,
    daily: int = DEFAULT_DAILY,
    prune_old: bool = True,
) -> BackupResult:
    """Take a backup and publish it atomically, then apply retention.

    The database file is renamed into place before its manifest is written, so
    an interrupted run can leave a backup without a manifest -- restorable, and
    reported as such -- but never a manifest promising a backup that is absent.
    """
    if not database.is_file():
        # sqlite3 would happily create one, and a mistyped path would then
        # publish an empty "backup" and let retention prune the real ones.
        raise BackupError(f"no database to back up at {database}")
    directory = backup_dir if backup_dir is not None else default_backup_dir(database)
    directory.mkdir(parents=True, exist_ok=True)
    target = _unused_backup(directory, database.stem, timestamp(now))
    with connection(database) as source:
        temporary = _copy_into_temp(source, directory)
    try:
        manifest = _describe(temporary, database, target)
        os.replace(temporary, target.path)
    except BaseException:
        _discard(temporary)
        raise
    _write_json(target.manifest_path, manifest)
    pruned = prune(directory, database.stem, hourly=hourly, daily=daily) if prune_old else ()
    return BackupResult(target, manifest, pruned)


def _newest_per_bucket(
    backups: tuple[Backup, ...], key: Callable[[Backup], str], limit: int
) -> set[Backup]:
    if limit <= 0:
        return set()
    newest: dict[str, Backup] = {}
    for backup in backups:  # ascending, so the last write into a bucket wins
        newest[key(backup)] = backup
    return {newest[bucket] for bucket in sorted(newest, reverse=True)[:limit]}


def retained(
    backups: tuple[Backup, ...], *, hourly: int = DEFAULT_HOURLY, daily: int = DEFAULT_DAILY
) -> set[Backup]:
    """Grandfather-father-son retention over UTC buckets.

    Backups are grouped by UTC hour and, separately, by UTC day. The newest
    backup in each of the `hourly` most recent hour buckets is kept, as is the
    newest in each of the `daily` most recent day buckets, and the two sets are
    unioned. The windows therefore overlap rather than compete: a burst of
    backups this afternoon cannot evict last week's dailies, and an idle day
    costs an hour bucket rather than a day of history.

    The newest backup is always kept, whatever the limits are set to.
    """
    if not backups:
        return set()
    keep = _newest_per_bucket(backups, lambda backup: backup.hour_bucket, hourly)
    keep |= _newest_per_bucket(backups, lambda backup: backup.day_bucket, daily)
    keep.add(backups[-1])
    return keep


def prune(
    backup_dir: Path,
    stem: str,
    *,
    hourly: int = DEFAULT_HOURLY,
    daily: int = DEFAULT_DAILY,
) -> tuple[Path, ...]:
    """Delete the backups retention does not keep, manifests included."""
    backups = discover(backup_dir, stem)
    keep = retained(backups, hourly=hourly, daily=daily)
    removed = []
    for backup in backups:
        if backup in keep:
            continue
        backup.path.unlink()
        backup.manifest_path.unlink(missing_ok=True)
        removed.append(backup.path)
    return tuple(removed)


def restore(database: Path, source: Path, *, now: datetime | None = None) -> Path | None:
    """Publish a backup as the live database, preserving whatever it replaces.

    The existing database is moved aside with its sidecars rather than deleted.
    That keeps an operator's data recoverable after a mistaken restore, and it
    guarantees no stale -wal is left for SQLite to replay over the restored
    file. Returns where the previous database went, or None if there was none.
    """
    problem = verify_file(source)
    if problem is not None:
        raise BackupError(f"refusing to restore a damaged backup {source}: {problem}")
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(read_only_uri(source), uri=True)) as origin:
        temporary = _copy_into_temp(origin, database.parent)
    replaced = quarantine(database, label="replaced", now=now) if database.exists() else None
    try:
        os.replace(temporary, database)
    except BaseException:
        _discard(temporary)
        raise
    return replaced
