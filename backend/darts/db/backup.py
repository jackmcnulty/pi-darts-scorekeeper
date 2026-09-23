"""Consistent backups, atomic publication, bucketed retention, and restore.

The copying, publishing and describing are `darts.db.artifact`'s, shared with
the snapshot service #20 added. What is this module's own is the *history*: a
timestamped name, a manifest beside each one, retention over UTC buckets, and
restoring one over the live database.
"""

import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from darts.db.artifact import (
    ArtifactError,
    copy_into_temp,
    describe,
    discard,
    publish,
    write_json,
)
from darts.db.connection import connection
from darts.db.durability import quarantine, read_only_uri, timestamp, verify_file

DEFAULT_HOURLY = 24
DEFAULT_DAILY = 30

#: Temporary copies are written in the backup directory itself, so they need a
#: prefix `discover` will not mistake for a published backup.
_TEMP_PREFIX = ".darts-backup-"

_SUFFIX = re.compile(r"\A(\d{8}T\d{6}Z)(?:-(\d+))?\.db\Z")


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

    `darts.config.Settings` takes this as its default for `backup_dir`, so the
    server and the CLIs agree on where backups live. `DARTS_BACKUP_DIR` is the
    override for the server; `--backup-dir` is the override for the CLIs.
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


def _manifest(temporary: Path, database: Path, target: Backup) -> dict[str, Any]:
    """The shared description of the finished copy, under this backup's own name."""
    return {
        "backup": target.path.name,
        **describe(temporary, source=database, created_at=target.stamp),
    }


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
        raise ArtifactError(f"no database to back up at {database}")
    directory = backup_dir if backup_dir is not None else default_backup_dir(database)
    directory.mkdir(parents=True, exist_ok=True)
    target = _unused_backup(directory, database.stem, timestamp(now))
    with connection(database) as source:
        temporary = copy_into_temp(source, directory, prefix=_TEMP_PREFIX)
    try:
        manifest = _manifest(temporary, database, target)
    except BaseException:
        discard(temporary)
        raise
    publish(temporary, target.path)
    write_json(target.manifest_path, manifest)
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
        raise ArtifactError(f"refusing to restore a damaged backup {source}: {problem}")
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(read_only_uri(source), uri=True)) as origin:
        temporary = copy_into_temp(origin, database.parent, prefix=_TEMP_PREFIX)
    replaced = quarantine(database, label="replaced", now=now) if database.exists() else None
    publish(temporary, database)
    return replaced
