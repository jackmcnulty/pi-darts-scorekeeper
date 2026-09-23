"""The published snapshot: fixed names, atomic publication, honest manifest.

What is *not* here is the copying itself. `darts.db.artifact` owns that and
`tests/db/test_backup.py` has proved it since #12; repeating those assertions
would only prove that this module calls the right function, which is exactly
what these tests establish by checking the properties the artifact carries.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from seed import build

from darts.config import Settings
from darts.db.artifact import ArtifactError
from darts.db.connection import connection, transaction
from darts.db.durability import SIDECARS, read_only_uri
from darts.repo.players import create_player
from darts.services import snapshot


@pytest.fixture
def played(tmp_path: Path) -> Path:
    """A real database with real darts in it."""
    database = tmp_path / "darts.db"
    build(database).close()
    return database


@pytest.fixture
def snapshots(tmp_path: Path) -> Path:
    return tmp_path / "snapshots"


def read_manifest(directory: Path) -> dict[str, object]:
    return dict(json.loads(snapshot.manifest_path(directory).read_text(encoding="utf-8")))


# --- the fixed names --------------------------------------------------------


def test_it_publishes_one_fixed_filename_and_one_fixed_manifest(
    played: Path, snapshots: Path
) -> None:
    """A path that can be written into a Samba config and stay correct."""
    result = snapshot.create(played, snapshots)

    assert result.path == snapshots / "darts-latest.db"
    assert result.manifest_path == snapshots / "snapshot.json"
    assert sorted(path.name for path in snapshots.iterdir()) == [
        "darts-latest.db",
        "snapshot.json",
    ]


def test_running_it_twice_replaces_rather_than_accumulates(played: Path, snapshots: Path) -> None:
    """No rotation and no retention: that is what `darts.db.backup` is for."""
    snapshot.create(played, snapshots)
    snapshot.create(played, snapshots)

    assert len(list(snapshots.iterdir())) == 2


def test_it_creates_the_snapshot_directory(played: Path, tmp_path: Path) -> None:
    nested = tmp_path / "not" / "yet" / "there"
    result = snapshot.create(played, nested)
    assert result.path.is_file()


def test_the_default_directory_is_the_one_settings_resolves(tmp_path: Path) -> None:
    """`config` spells the default out rather than importing a service; they must agree."""
    database = tmp_path / "var" / "darts.db"
    settings = Settings.from_env({"DARTS_DB_PATH": str(database)})
    assert settings.snapshot_dir == snapshot.default_snapshot_dir(database).resolve()


def test_snapshotting_a_database_that_is_not_there_refuses(tmp_path: Path) -> None:
    """sqlite3 would create one, and a mistyped path would publish an empty snapshot."""
    with pytest.raises(FileNotFoundError, match="no database to snapshot"):
        snapshot.create(tmp_path / "absent.db", tmp_path / "snapshots")
    assert not (tmp_path / "snapshots").exists()


# --- what the artifact is ---------------------------------------------------


def test_the_snapshot_is_one_self_contained_file_with_no_sidecars(
    played: Path, snapshots: Path
) -> None:
    """The DB Browser / DuckDB criterion, as the property those tools need."""
    result = snapshot.create(played, snapshots)

    for suffix in SIDECARS:
        assert not result.path.with_name(result.path.name + suffix).exists()
    with closing(sqlite3.connect(read_only_uri(result.path), uri=True)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
        assert [row[0] for row in conn.execute("PRAGMA integrity_check")] == ["ok"]


def test_the_snapshot_opens_read_only_without_a_lock_error(played: Path, snapshots: Path) -> None:
    """Opening it must not upgrade it to WAL, which a read-write open would."""
    result = snapshot.create(played, snapshots)
    before = result.path.read_bytes()

    with closing(sqlite3.connect(read_only_uri(result.path), uri=True)) as conn:
        assert conn.execute("SELECT count(*) FROM darts").fetchone()[0] > 0

    assert result.path.read_bytes() == before


def test_user_version_is_preserved(played: Path, snapshots: Path) -> None:
    result = snapshot.create(played, snapshots)

    with closing(sqlite3.connect(read_only_uri(played), uri=True)) as live:
        live_version = live.execute("PRAGMA user_version").fetchone()[0]
    with closing(sqlite3.connect(read_only_uri(result.path), uri=True)) as copy:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == live_version
    assert live_version > 0
    assert result.schema_version == live_version


def test_the_views_travel_with_the_snapshot(played: Path, snapshots: Path) -> None:
    """A copy without the query surface would be a database nothing can query."""
    result = snapshot.create(played, snapshots)
    with closing(sqlite3.connect(read_only_uri(result.path), uri=True)) as conn:
        views = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'view'")
        }
        assert views == {"v_darts", "v_leg_players", "v_match_players", "v_visits"}
        assert conn.execute("SELECT count(*) FROM v_darts").fetchone()[0] > 0


# --- the manifest -----------------------------------------------------------


def test_the_manifest_row_counts_match_the_snapshots_actual_contents(
    played: Path, snapshots: Path
) -> None:
    """Read back out of the published file, table by table, not off the live one."""
    result = snapshot.create(played, snapshots)
    manifest = read_manifest(snapshots)
    assert manifest == result.manifest

    with closing(sqlite3.connect(read_only_uri(result.path), uri=True)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert set(result.row_counts) == tables
        for table, count in result.row_counts.items():
            actual = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            assert actual == count, table
    assert result.row_counts["darts"] > 0


def test_the_manifest_carries_the_timestamp_version_and_size(played: Path, snapshots: Path) -> None:
    moment = datetime(2026, 9, 23, 10, 15, tzinfo=UTC)
    result = snapshot.create(played, snapshots, now=moment)

    assert result.created_at == "20260923T101500Z"
    assert result.schema_version == 2
    assert result.size_bytes == result.path.stat().st_size
    assert result.manifest["snapshot"] == "darts-latest.db"
    assert result.manifest["source"] == str(played)


def test_the_manifest_describes_the_snapshot_not_the_database_written_since(
    played: Path, snapshots: Path
) -> None:
    """Rows written after the copy must not appear in its counts."""
    result = snapshot.create(played, snapshots)
    counted = result.row_counts["players"]

    with connection(played) as conn, transaction(conn):
        create_player(conn, "Gus")

    with closing(sqlite3.connect(read_only_uri(result.path), uri=True)) as copy:
        assert copy.execute("SELECT count(*) FROM players").fetchone()[0] == counted
    with connection(played) as live:
        assert live.execute("SELECT count(*) FROM players").fetchone()[0] == counted + 1


def test_the_manifest_is_valid_json_with_sorted_keys(played: Path, snapshots: Path) -> None:
    """It is read by hand and by #31; a stable key order keeps a diff readable."""
    snapshot.create(played, snapshots)
    text = snapshot.manifest_path(snapshots).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert list(json.loads(text)) == sorted(json.loads(text))


# --- atomicity --------------------------------------------------------------


def test_the_temporary_is_written_in_the_destination_directory(
    played: Path, snapshots: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So publishing is a same-filesystem rename, which is what makes it atomic."""
    seen: list[Path] = []
    genuine = os.replace

    def record(source: object, target: object) -> None:
        seen.append(Path(str(source)))
        genuine(str(source), str(target))

    monkeypatch.setattr("darts.db.artifact.os.replace", record)
    snapshot.create(played, snapshots)

    assert seen, "nothing was published by rename"
    assert seen[0].parent == snapshots
    assert seen[0].name.startswith(".darts-snapshot-")


def test_a_failure_before_publication_leaves_the_previous_snapshot_intact(
    played: Path, snapshots: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader on the share keeps the good file rather than getting a broken one."""
    good = snapshot.create(played, snapshots)
    published = good.path.read_bytes()

    monkeypatch.setattr("darts.db.artifact.verify_file", lambda path: "injected damage")
    with pytest.raises(ArtifactError, match="failed its integrity check"):
        snapshot.create(played, snapshots)

    assert good.path.read_bytes() == published
    assert read_manifest(snapshots) == good.manifest
    leftovers = [p.name for p in snapshots.iterdir() if p.name.startswith(".darts-snapshot-")]
    assert leftovers == []


def test_a_failure_to_publish_leaves_no_temporary_behind(
    played: Path, snapshots: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(source: object, target: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr("darts.db.artifact.os.replace", full)
    with pytest.raises(OSError, match="no space left"):
        snapshot.create(played, snapshots)

    assert list(snapshots.iterdir()) == []


def test_a_reader_never_observes_a_partial_snapshot(played: Path, snapshots: Path) -> None:
    """Every state the published path is ever in is a whole, valid database.

    `os.replace` is atomic, so the only two states are "the old file" and "the
    new one". This checks that directly by looking at the path at the instant
    the rename happens, which is the only instant at which a torn file could
    exist.
    """
    genuine = os.replace
    observations: list[tuple[bool, int]] = []
    target = snapshot.snapshot_path(snapshots)

    def look(source: object, destination: object) -> None:
        if target.exists():
            with closing(sqlite3.connect(read_only_uri(target), uri=True)) as conn:
                rows = int(conn.execute("SELECT count(*) FROM darts").fetchone()[0])
                ok = [row[0] for row in conn.execute("PRAGMA integrity_check")] == ["ok"]
            observations.append((ok, rows))
        genuine(str(source), str(destination))

    first = snapshot.create(played, snapshots)
    import darts.db.artifact as artifact

    original = artifact.os.replace
    artifact.os.replace = look  # type: ignore[assignment]
    try:
        snapshot.create(played, snapshots)
    finally:
        artifact.os.replace = original  # type: ignore[assignment]

    assert observations, "the second run never reached the rename"
    for ok, rows in observations:
        assert ok
        assert rows == first.row_counts["darts"]


# --- the copy the download streams ------------------------------------------


def test_copy_of_produces_an_unpublished_temporary_the_caller_owns(
    played: Path, snapshots: Path
) -> None:
    """`GET /api/export/db` streams this, and must not touch the shared file."""
    published = snapshot.create(played, snapshots)
    before = published.path.read_bytes()

    copy = snapshot.copy_of(played, snapshots, prefix=".darts-download-")
    try:
        assert copy.parent == snapshots
        assert copy.name.startswith(".darts-download-")
        assert copy != published.path
        assert published.path.read_bytes() == before
        with closing(sqlite3.connect(read_only_uri(copy), uri=True)) as conn:
            assert [row[0] for row in conn.execute("PRAGMA integrity_check")] == ["ok"]
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
    finally:
        copy.unlink(missing_ok=True)


def test_two_copies_do_not_collide(played: Path, snapshots: Path) -> None:
    """Two people downloading at once is one household's worth of concurrency."""
    first = snapshot.copy_of(played, snapshots)
    second = snapshot.copy_of(played, snapshots)
    try:
        assert first != second
        assert first.read_bytes() == second.read_bytes()
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)
