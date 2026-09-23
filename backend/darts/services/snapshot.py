"""One fixed-name, self-contained copy of the database, republished in place.

This is `darts.db.backup` with the history taken out. A backup is a growing,
timestamped, pruned archive that an operator restores from; a snapshot is a
single file at a path other machines can be pointed at once and keep reading
from -- `//darts/snapshots/darts-latest.db` on #30's Samba share, and whatever
#31 pulls off the Pi. So the name never changes, nothing is retained and nothing
is pruned; each run simply replaces what the last one published.

Everything that makes the artifact trustworthy is `darts.db.artifact`'s and is
shared with backups rather than restated here: the copy goes through
`Connection.backup()` inside a read transaction, its WAL is collapsed so the
published file needs no sidecar, it is written in the destination directory and
renamed into place with `os.replace`, and its manifest is read back out of the
finished file.

The database is published before its manifest, the same ordering backups use: an
interrupted run can leave a snapshot whose `snapshot.json` still describes the
previous one, but never a manifest promising a snapshot that is not there. The
manifest carries `created_at`, so a reader can always tell which it has.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from darts.db.artifact import copy_into_temp, describe, discard, publish, write_json
from darts.db.connection import connection
from darts.db.durability import timestamp

#: The published artifact. Fixed, because the whole point is a path that can be
#: written into a Samba config, a cron job or a bookmark and stay correct.
SNAPSHOT_NAME = "darts-latest.db"

#: Its manifest, beside it under an equally fixed name.
MANIFEST_NAME = "snapshot.json"

#: Temporaries live in the snapshot directory so publishing is a rename. The
#: leading dot keeps a half-written copy out of a directory listing and out of
#: the way of anything globbing for `*.db`.
_TEMP_PREFIX = ".darts-snapshot-"


@dataclass(frozen=True)
class SnapshotResult:
    """Where the snapshot and its manifest went, and what the manifest says."""

    path: Path
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def schema_version(self) -> int:
        return int(self.manifest["schema_version"])

    @property
    def size_bytes(self) -> int:
        return int(self.manifest["size_bytes"])

    @property
    def created_at(self) -> str:
        return str(self.manifest["created_at"])

    @property
    def row_counts(self) -> dict[str, int]:
        return {str(name): int(count) for name, count in self.manifest["row_counts"].items()}


def default_snapshot_dir(database: Path) -> Path:
    """Snapshots sit beside the database, so relocating one relocates both.

    `darts.config.Settings` resolves `snapshot_dir` the same way -- it spells the
    default out rather than importing a service, so `tests/services/test_snapshot.py`
    asserts the two agree instead of trusting that they do. `DARTS_SNAPSHOT_DIR`
    is the override for the server; `--snapshot-dir` is the override for the CLI.
    """
    return database.parent / "snapshots"


def snapshot_path(snapshot_dir: Path) -> Path:
    return snapshot_dir / SNAPSHOT_NAME


def manifest_path(snapshot_dir: Path) -> Path:
    return snapshot_dir / MANIFEST_NAME


def copy_of(database: Path, directory: Path, *, prefix: str = _TEMP_PREFIX) -> Path:
    """A fresh point-in-time copy of `database` as an unpublished temporary file.

    This is what `GET /api/export/db` streams. It deliberately does not touch
    the published snapshot: a download must never be able to leave the shared
    file half-written, and a caller who is streaming a copy must be free to
    delete it when the client goes away.

    The caller owns the returned file and must remove it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with connection(database) as source:
        return copy_into_temp(source, directory, prefix=prefix)


def create(database: Path, snapshot_dir: Path, *, now: datetime | None = None) -> SnapshotResult:
    """Publish `darts-latest.db` and `snapshot.json`, replacing any previous pair.

    Safe to call while a game is in progress, and safe to call twice: the second
    run overwrites the first by rename, so a reader holding the old file keeps
    reading it and a reader opening the path afterwards gets the new one.
    """
    if not database.is_file():
        # As in backups: sqlite3 would create one, and a mistyped path would
        # publish an empty "snapshot" over a good one.
        raise FileNotFoundError(f"no database to snapshot at {database}")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target = snapshot_path(snapshot_dir)

    temporary = copy_of(database, snapshot_dir)
    try:
        manifest = {
            "snapshot": SNAPSHOT_NAME,
            **describe(temporary, source=database, created_at=timestamp(now)),
        }
    except BaseException:
        discard(temporary)
        raise
    publish(temporary, target)

    manifest_target = manifest_path(snapshot_dir)
    write_json(manifest_target, manifest)
    return SnapshotResult(target, manifest_target, manifest)
