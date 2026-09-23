"""Snapshot consistency, atomic publication, retention arithmetic, round-trip."""

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from dbfixtures import add_visit, dump, scaffold

from darts.db.artifact import ArtifactError, collapse_wal
from darts.db.backup import (
    Backup,
    create,
    default_backup_dir,
    discover,
    newest_valid,
    prune,
    restore,
    retained,
)
from darts.db.connection import connection, transaction
from darts.db.durability import SIDECARS, verify_file

STAMP = "%Y%m%dT%H%M%SZ"


def stamp_at(*, days: int = 0, hours: int = 0, minutes: int = 0) -> datetime:
    return datetime(2026, 9, 22, 12, 0, tzinfo=UTC) + timedelta(
        days=days, hours=hours, minutes=minutes
    )


def place(directory: Path, stem: str, moments: list[datetime]) -> list[Backup]:
    """Backup-shaped files: retention reads names, so contents are irrelevant."""
    directory.mkdir(parents=True, exist_ok=True)
    made = []
    for moment in moments:
        stamp = moment.strftime(STAMP)
        path = directory / f"{stem}-{stamp}.db"
        path.write_bytes(b"placeholder")
        path.with_name(path.name + ".json").write_text("{}")
        made.append(Backup(stamp, 0, path))
    return made


@pytest.fixture
def played(tmp_path: Path) -> Iterator[Path]:
    """A database holding a real leg: 20 visits and 60 darts."""
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)
        for index in range(20):
            with transaction(conn):
                add_visit(conn, index)
    yield database


def test_backup_defaults_beside_the_database_and_writes_a_manifest(played: Path) -> None:
    result = create(played)

    assert result.backup.path.parent == default_backup_dir(played) == played.parent / "backups"
    assert result.backup.path.name == f"darts-{result.backup.stamp}.db"
    assert verify_file(result.backup.path) is None

    manifest = json.loads(result.backup.manifest_path.read_text())
    assert manifest == result.manifest
    assert manifest["created_at"] == result.backup.stamp
    assert manifest["schema_version"] == 2
    assert manifest["source"] == str(played)
    assert manifest["size_bytes"] == result.backup.path.stat().st_size
    # Counts are read back out of the artifact, not the live database.
    assert manifest["row_counts"]["visits"] == 20
    assert manifest["row_counts"]["darts"] == 60
    assert manifest["row_counts"]["cricket_point_events"] == 0


def test_published_backup_is_one_file_with_no_sidecars(played: Path) -> None:
    result = create(played)

    for suffix in SIDECARS:
        assert not result.backup.path.with_name(result.backup.path.name + suffix).exists()
    leftovers = [p.name for p in result.backup.path.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_backup_taken_during_active_writes_is_consistent(tmp_path: Path) -> None:
    """A torn copy would show darts whose visit is missing; both checks catch it."""
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)

    stop = threading.Event()
    failures: list[BaseException] = []

    def writer() -> None:
        try:
            with connection(database) as conn:
                index = 0
                while not stop.is_set():
                    with transaction(conn):
                        add_visit(conn, index)
                    index += 1
        except BaseException as exc:  # pragma: no cover - reported by the assertion below
            failures.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        results = [create(database, prune_old=False) for _ in range(5)]
    finally:
        stop.set()
        thread.join(timeout=30)
    assert not failures
    assert not thread.is_alive()

    for result in results:
        assert verify_file(result.backup.path) is None
        counts = result.manifest["row_counts"]
        # Each visit and its three darts commit together, so a snapshot that
        # caught a transaction mid-flight would break this ratio.
        assert counts["darts"] == counts["visits"] * 3
    counted = [result.manifest["row_counts"]["visits"] for result in results]
    assert counted == sorted(counted)
    with connection(database) as conn:
        assert conn.execute("SELECT count(*) FROM visits").fetchone()[0] > 0


def test_a_failed_backup_publishes_nothing_and_leaves_no_temporary(
    played: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("darts.db.artifact.verify_file", lambda path: "injected damage")

    with pytest.raises(ArtifactError, match="failed its integrity check"):
        create(played)

    directory = default_backup_dir(played)
    assert list(directory.iterdir()) == []


class _StubbornConnection:
    """PRAGMA journal_mode reports the mode in force, which may be unchanged."""

    def execute(self, sql: str) -> sqlite3.Cursor:
        assert sql == "PRAGMA journal_mode = DELETE"
        return cast(sqlite3.Cursor, _Row())


class _Row:
    def fetchone(self) -> tuple[str]:
        return ("wal",)


def test_collapsing_the_wal_refuses_to_publish_a_split_backup() -> None:
    """Publishing while a WAL still holds pages would lose them silently."""
    with pytest.raises(ArtifactError, match="journal_mode=wal"):
        collapse_wal(cast(sqlite3.Connection, _StubbornConnection()))


def test_a_locked_copy_target_raises_and_leaves_no_temporary(
    played: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever goes wrong mid-copy, the unpublished temporary is cleaned up."""

    def explode(conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("darts.db.artifact.collapse_wal", explode)
    directory = played.parent / "backups"

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        create(played, backup_dir=directory)
    assert list(directory.iterdir()) == []


def test_discover_orders_by_filename_stamp_not_mtime(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    older, newer = place(directory, "darts", [stamp_at(hours=-1), stamp_at()])
    # Restoring or copying rewrites mtime; the name is what stays true.
    newer.path.touch()
    older.path.touch()
    (directory / "darts-not-a-stamp.db").write_bytes(b"")
    (directory / "other-20260922T120000Z.db").write_bytes(b"")

    found = discover(directory, "darts")
    assert [backup.path.name for backup in found] == [older.path.name, newer.path.name]
    assert discover(tmp_path / "absent", "darts") == ()


def test_backups_taken_in_the_same_second_stay_distinct_and_ordered(played: Path) -> None:
    moment = stamp_at()
    first = create(played, now=moment, prune_old=False)
    second = create(played, now=moment, prune_old=False)

    assert first.backup.stamp == second.backup.stamp
    assert (first.backup.ordinal, second.backup.ordinal) == (0, 1)
    assert second.backup.path.name.endswith("-1.db")
    assert discover(default_backup_dir(played), "darts") == (first.backup, second.backup)


def test_hourly_window_keeps_one_backup_per_hour_bucket(tmp_path: Path) -> None:
    """30 hourly backups spanning two days: 24 hour buckets survive, 6 go."""
    directory = tmp_path / "backups"
    backups = place(directory, "darts", [stamp_at(hours=-offset) for offset in range(29, -1, -1)])

    removed = prune(directory, "darts", hourly=24, daily=30)

    assert len(removed) == 6
    assert removed == tuple(backup.path for backup in backups[:6])
    surviving = discover(directory, "darts")
    assert len(surviving) == 24
    assert {backup.hour_bucket for backup in surviving} == {
        backup.hour_bucket for backup in backups[6:]
    }
    # Manifests are pruned with the backups they describe.
    assert not backups[0].manifest_path.exists()


def test_several_backups_inside_one_hour_collapse_to_the_newest(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    burst = place(directory, "darts", [stamp_at(minutes=index * 5) for index in range(10)])
    earlier = place(directory, "darts", [stamp_at(hours=-5)])[0]

    removed = prune(directory, "darts", hourly=24, daily=30)

    assert len(removed) == 9
    assert {backup.path for backup in discover(directory, "darts")} == {
        earlier.path,
        burst[-1].path,
    }


def test_daily_window_preserves_history_the_hourly_window_drops(tmp_path: Path) -> None:
    """One backup a day for 40 days: hourly reaches 24, daily extends it to 30."""
    directory = tmp_path / "backups"
    backups = place(directory, "darts", [stamp_at(days=-offset) for offset in range(39, -1, -1)])

    hourly_only = retained(tuple(backups), hourly=24, daily=0)
    both = retained(tuple(backups), hourly=24, daily=30)

    assert len(hourly_only) == 24
    assert len(both) == 30
    assert both > hourly_only
    assert prune(directory, "darts", hourly=24, daily=30) == tuple(b.path for b in backups[:10])


def test_retention_never_deletes_the_newest_backup(tmp_path: Path) -> None:
    """Even limits of zero cannot leave the Pi with nothing to restore from."""
    directory = tmp_path / "backups"
    backups = place(directory, "darts", [stamp_at(hours=-offset) for offset in range(4, -1, -1)])

    removed = prune(directory, "darts", hourly=0, daily=0)

    assert len(removed) == 4
    assert [backup.path for backup in discover(directory, "darts")] == [backups[-1].path]
    assert prune(directory, "darts") == ()
    assert prune(tmp_path / "absent", "darts") == ()


def test_backup_wipe_restore_round_trips_every_row(played: Path) -> None:
    """#13 views, #14 repositories and #19 stats do not exist yet, so the
    round-trip is verified against the source tables statistics derive from."""
    before = dump(played)
    assert before["schema_version"] == 2
    result = create(played)

    for suffix in ("", *SIDECARS):
        played.with_name(played.name + suffix).unlink(missing_ok=True)
    assert not played.exists()

    replaced = restore(played, result.backup.path)

    assert replaced is None
    assert dump(played) == before
    with connection(played) as conn:
        assert conn.execute("SELECT count(*) FROM darts").fetchone()[0] == 60


def test_restore_keeps_the_database_it_replaces_and_its_sidecars(played: Path) -> None:
    result = create(played)
    with connection(played) as conn:
        with transaction(conn):
            add_visit(conn, 20)
        assert conn.execute("SELECT count(*) FROM visits").fetchone()[0] == 21

    replaced = restore(played, result.backup.path)

    assert replaced is not None
    assert replaced.name.startswith("darts.replaced-")
    # The restored database is the backup, and no stale -wal survived beside it.
    assert dump(played)["visits"] == dump(result.backup.path)["visits"]
    assert not played.with_name(played.name + "-wal").exists()
    # The newer work is preserved rather than destroyed by the restore.
    with connection(replaced) as conn:
        assert conn.execute("SELECT count(*) FROM visits").fetchone()[0] == 21


def test_a_restore_that_cannot_publish_leaves_no_temporary_behind(
    played: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The quarantined original stays put; only the unpublished copy is dropped."""
    result = create(played, prune_old=False)
    genuine = os.replace

    def fail_only_when_publishing(source: object, target: object) -> None:
        # Quarantining the original must still succeed, or the test would be
        # asserting about a failure that happened before the publish step.
        if Path(str(target)) == played:
            raise OSError("no space left on device")
        genuine(str(source), str(target))

    monkeypatch.setattr("darts.db.artifact.os.replace", fail_only_when_publishing)

    with pytest.raises(OSError, match="no space left"):
        restore(played, result.backup.path)

    leftovers = [p.name for p in played.parent.iterdir() if p.name.startswith(".darts-backup-")]
    assert leftovers == []
    assert list(played.parent.glob("darts.replaced-*.db"))


def test_restore_refuses_a_damaged_backup(played: Path) -> None:
    result = create(played)
    damaged = result.backup.path
    data = bytearray(damaged.read_bytes())
    data[4096 * 2 : 4096 * 6] = b"\xde" * (4096 * 4)
    damaged.write_bytes(bytes(data))

    with pytest.raises(ArtifactError, match="refusing to restore a damaged backup"):
        restore(played, damaged)
    assert dump(played)["visits"]


def test_newest_valid_skips_damaged_backups(played: Path) -> None:
    good = create(played, now=stamp_at(hours=-1), prune_old=False)
    newer = create(played, now=stamp_at(), prune_old=False)
    newer.backup.path.write_bytes(b"not a database at all")

    assert newest_valid(played) == good.backup
    assert newest_valid(played, backup_dir=played.parent / "empty") is None


def test_a_backup_without_its_manifest_is_still_restorable(played: Path) -> None:
    """An interrupted run can leave a database with no manifest; never the reverse."""
    result = create(played)
    result.backup.manifest_path.unlink()

    assert newest_valid(played) == result.backup
    assert restore(played, result.backup.path) is not None


def test_backup_result_exposes_the_moment_it_was_taken(played: Path) -> None:
    moment = stamp_at()
    result = create(played, now=moment)

    assert result.backup.taken_at == moment.replace(microsecond=0)
    assert result.backup.hour_bucket == "20260922T12"
    assert result.backup.day_bucket == "20260922"


def test_restore_creates_a_missing_destination_directory(played: Path, tmp_path: Path) -> None:
    result = create(played)
    target = tmp_path / "relocated" / "darts.db"

    assert restore(target, result.backup.path) is None
    assert dump(target) == dump(result.backup.path)


def test_pruning_runs_by_default_after_each_backup(played: Path) -> None:
    create(played, now=stamp_at(minutes=0))
    second = create(played, now=stamp_at(minutes=10))

    # Both land in the same hour and the same day, so only the newest remains.
    assert [b.path for b in discover(default_backup_dir(played), "darts")] == [second.backup.path]


def test_backup_of_a_missing_database_fails_without_creating_one(tmp_path: Path) -> None:
    """A mistyped path must not publish an empty backup that retention then trusts."""
    absent = tmp_path / "nested" / "darts.db"
    with pytest.raises(ArtifactError, match="no database to back up"):
        create(absent)
    assert not absent.exists()
    assert not absent.parent.exists()
