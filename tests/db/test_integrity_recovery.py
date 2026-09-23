"""Boot detection, quarantine, automatic restore, and the degraded fallback."""

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from dbfixtures import SCHEMA_VERSION, add_visit, dump, scaffold

from darts.db.backup import create
from darts.db.connection import connection, transaction
from darts.db.durability import SIDECARS, verify_live
from darts.db.recovery import DatabaseState, check_and_recover


def page_size(path: Path) -> int:
    return int.from_bytes(path.read_bytes()[16:18], "big") or 65536


def corrupt(path: Path) -> None:
    """Overwrite four pages in the middle of the file, leaving the header intact.

    Most interior damage is severe enough that SQLite refuses to open the file
    at all, so this exercises the path where corruption surfaces as an
    exception. test_damage_that_integrity_check_reports_rather_than_raising
    covers the quieter case where the PRAGMA is what notices.
    """
    data = bytearray(path.read_bytes())
    page = page_size(path)
    start, end = page * 2, min(page * 6, len(data))
    assert end > start, "the database is too small to corrupt meaningfully"
    data[start:end] = b"\xde" * (end - start)
    path.write_bytes(bytes(data))


def corrupt_page(path: Path, index: int) -> None:
    data = bytearray(path.read_bytes())
    page = page_size(path)
    data[index * page : (index + 1) * page] = b"\x7f" * page
    path.write_bytes(bytes(data))


def visit_count(database: Path) -> int:
    with connection(database) as conn:
        return int(conn.execute("SELECT count(*) FROM visits").fetchone()[0])


@pytest.fixture
def played(tmp_path: Path) -> Iterator[Path]:
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)
        for index in range(20):
            with transaction(conn):
                add_visit(conn, index)
    yield database


def test_a_sound_database_boots_untouched(played: Path) -> None:
    before = dump(played)

    status = check_and_recover(played)

    assert status.state is DatabaseState.HEALTHY
    assert not status.degraded and not status.auto_restored
    assert status.quarantined_to is None and status.detail is None
    assert dump(played) == before
    assert list(played.parent.glob("*.corrupt-*")) == []


def test_a_missing_database_is_created_rather_than_reported_as_damage(tmp_path: Path) -> None:
    database = tmp_path / "fresh" / "darts.db"

    status = check_and_recover(database)

    assert status.state is DatabaseState.HEALTHY
    assert status.detail == "created a new database"
    assert not status.auto_restored
    assert visit_count(database) == 0
    assert dump(database)["schema_version"] == SCHEMA_VERSION


def test_corruption_is_quarantined_and_the_newest_good_backup_restored(
    played: Path, caplog: pytest.LogCaptureFixture
) -> None:
    backup = create(played, prune_old=False)
    assert backup.manifest["row_counts"]["visits"] == 20
    with connection(played) as conn:
        for index in range(20, 30):
            with transaction(conn):
                add_visit(conn, index)
    assert visit_count(played) == 30
    corrupt(played)
    damaged_bytes = played.read_bytes()

    with caplog.at_level(logging.ERROR):
        status = check_and_recover(played)

    assert status.state is DatabaseState.RESTORED
    assert status.auto_restored and not status.degraded
    assert status.restored_from == backup.backup.path
    assert status.detail is not None

    # Moved aside, never deleted, and still byte-identical to what failed, so
    # the 10 visits that were never backed up remain forensically available.
    assert status.quarantined_to is not None
    assert status.quarantined_to.name.startswith("darts.corrupt-")
    assert status.quarantined_to.read_bytes() == damaged_bytes

    assert visit_count(played) == 20
    assert dump(played) == dump(backup.backup.path)
    assert "failed its boot check" in caplog.text
    assert "restored" in caplog.text


def test_recovery_skips_a_backup_that_is_itself_damaged(played: Path) -> None:
    older = create(played, prune_old=False)
    with connection(played) as conn, transaction(conn):
        add_visit(conn, 20)
    newer = create(played, prune_old=False)
    corrupt(newer.backup.path)
    corrupt(played)

    status = check_and_recover(played)

    assert status.restored_from == older.backup.path
    assert visit_count(played) == 20


def test_no_valid_backup_starts_empty_and_degraded_rather_than_crash_looping(
    played: Path, caplog: pytest.LogCaptureFixture
) -> None:
    corrupt(played)

    with caplog.at_level(logging.ERROR):
        status = check_and_recover(played)

    assert status.state is DatabaseState.DEGRADED
    assert status.degraded and not status.auto_restored
    assert status.restored_from is None
    assert status.quarantined_to is not None and status.quarantined_to.exists()
    # Usable immediately: empty, but migrated and serviceable.
    assert visit_count(played) == 0
    assert dump(played)["schema_version"] == SCHEMA_VERSION
    assert "starting empty and DEGRADED" in caplog.text


def test_degraded_when_every_backup_is_damaged(played: Path) -> None:
    backup = create(played, prune_old=False)
    corrupt(backup.backup.path)
    corrupt(played)

    status = check_and_recover(played)

    assert status.state is DatabaseState.DEGRADED
    assert visit_count(played) == 0


def test_a_database_damaged_in_its_header_is_recovered_too(played: Path) -> None:
    """This one fails while opening, before any integrity query can run."""
    backup = create(played, prune_old=False)
    played.write_bytes(b"this is not a database" * 64)

    status = check_and_recover(played)

    assert status.state is DatabaseState.RESTORED
    assert status.detail is not None and "not a database" in status.detail
    assert visit_count(played) == 20
    assert dump(played) == dump(backup.backup.path)


def test_damage_that_integrity_check_reports_rather_than_raising(
    played: Path, tmp_path: Path
) -> None:
    """Some damage still opens cleanly, and only the PRAGMA notices it.

    The page that behaves this way depends on how SQLite laid the file out, so
    it is searched for rather than hard-coded.
    """
    backup = create(played, prune_old=False)
    pristine = played.read_bytes()
    reported = None
    for index in range(1, len(pristine) // page_size(played)):
        probe = tmp_path / f"probe-{index}.db"
        probe.write_bytes(pristine)
        corrupt_page(probe, index)
        try:
            problem = verify_live(probe)
        except sqlite3.DatabaseError:  # pragma: no cover - depends on the layout
            continue
        if problem is not None and problem.startswith("integrity_check:"):
            reported = index
            break
    assert reported is not None, "no single-page damage produced a reported failure"

    corrupt_page(played, reported)
    status = check_and_recover(played)

    assert status.state is DatabaseState.RESTORED
    assert status.detail is not None and status.detail.startswith("integrity_check:")
    assert dump(played) == dump(backup.backup.path)


def test_foreign_key_violations_trigger_recovery(played: Path) -> None:
    backup = create(played, prune_old=False)
    orphan = sqlite3.connect(played)
    orphan.execute("PRAGMA foreign_keys = OFF")
    orphan.execute("INSERT INTO team_members VALUES (999, 998, 0)")
    orphan.commit()
    orphan.close()

    status = check_and_recover(played)

    assert status.state is DatabaseState.RESTORED
    assert status.detail is not None and status.detail.startswith("foreign_key_check")
    assert dump(played) == dump(backup.backup.path)


def test_stale_sidecars_never_outlive_the_database_they_belonged_to(played: Path) -> None:
    """A -wal left behind would be replayed over the restored file."""
    backup = create(played, prune_old=False)
    with connection(played) as conn:
        with transaction(conn):
            add_visit(conn, 20)
        assert played.with_name(played.name + "-wal").stat().st_size > 0
        corrupt(played)
        status = check_and_recover(played)

    assert status.state is DatabaseState.RESTORED
    assert status.quarantined_to is not None
    # The log went with the file it described, and is still there to inspect.
    assert status.quarantined_to.with_name(status.quarantined_to.name + "-wal").exists()
    for suffix in SIDECARS:
        sidecar = played.with_name(played.name + suffix)
        assert not sidecar.exists() or sidecar.stat().st_size == 0
    assert dump(played) == dump(backup.backup.path)


def test_environmental_failures_never_replace_a_database(tmp_path: Path) -> None:
    """A path that cannot be opened is not damage, and must not be quarantined."""
    directory = tmp_path / "darts.db"
    directory.mkdir()
    (tmp_path / "backups").mkdir()

    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        check_and_recover(directory)

    assert directory.is_dir()
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_recovery_reports_when_it_ran(played: Path) -> None:
    status = check_and_recover(played)
    assert status.checked_at.endswith("Z")
    assert len(status.checked_at) == 20
