"""Crash durability, clean-shutdown checkpointing, and quarantine.

The SIGKILL tests prove that an abruptly killed *process* leaves a sound
database with every acknowledged commit intact. They say nothing about losing
power mid-write to the SD card itself, which no test on this hardware-free CI
can reach; `synchronous = FULL` from #11 is what addresses that.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from dbfixtures import add_visit, scaffold

from darts.db.connection import connection, transaction
from darts.db.durability import (
    checkpoint_truncate,
    integrity_report,
    is_corruption,
    quarantine,
    unused_name,
    verify_file,
    verify_live,
)

WRITER = Path(__file__).with_name("sigkill_writer.py")
COMMITS = 12


def sidecar(database: Path, suffix: str) -> Path:
    return database.with_name(database.name + suffix)


def test_clean_shutdown_truncates_the_wal(tmp_path: Path) -> None:
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)
        with transaction(conn):
            add_visit(conn, 0)
        wal = sidecar(database, "-wal")
        assert wal.stat().st_size > 0
        result = checkpoint_truncate(conn)
        assert result.truncated and not result.busy
        assert wal.stat().st_size == 0

    # SQLite removes the log when the last connection closes, so a clean
    # shutdown leaves either no WAL or an empty one -- never a populated one.
    assert not wal.exists() or wal.stat().st_size == 0
    assert verify_live(database) is None


def test_checkpoint_reports_a_busy_result_instead_of_raising(tmp_path: Path) -> None:
    database = tmp_path / "darts.db"
    with connection(database) as writer, connection(database) as reader:
        scaffold(writer)
        with transaction(writer):
            add_visit(writer, 0)
        # A held read transaction blocks truncation; SQLite reports that in the
        # result row rather than as an error, which is exactly what must not be
        # mistaken for a successful shutdown checkpoint.
        writer.execute("PRAGMA busy_timeout = 0")
        reader.execute("PRAGMA busy_timeout = 0")
        with transaction(reader, immediate=False):
            reader.execute("SELECT count(*) FROM darts").fetchone()
            result = checkpoint_truncate(writer)
        assert result.busy
        assert not result.truncated
        assert sidecar(database, "-wal").stat().st_size > 0


def test_sigkill_during_writes_keeps_every_acknowledged_commit(tmp_path: Path) -> None:
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)

    process = subprocess.Popen(
        [sys.executable, str(WRITER), str(database), str(COMMITS)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    acknowledged = 0
    for line in process.stdout:
        kind, index = line.split()
        if kind == "committed":
            acknowledged = int(index) + 1
        else:
            in_flight = int(index)
            break
    else:  # pragma: no cover - the child only exits early if it crashed
        pytest.fail("the writer never reached its in-flight transaction")

    process.kill()
    assert process.wait() == -9
    process.stdout.close()

    assert acknowledged == COMMITS
    assert verify_live(database) is None
    with connection(database) as conn:
        visits = [row[0] for row in conn.execute("SELECT id FROM visits ORDER BY id")]
        darts = conn.execute("SELECT count(*) FROM darts").fetchone()[0]
    assert visits == [1000 + index for index in range(acknowledged)]
    assert darts == acknowledged * 3
    # The uncommitted visit left no trace: no half-written visit, no orphan dart.
    assert 1000 + in_flight not in visits


def test_sigkill_leaves_a_recoverable_wal_that_replays_on_reopen(tmp_path: Path) -> None:
    """Killing a process mid-WAL must not need the WAL to be gone to be safe."""
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)

    process = subprocess.Popen(
        [sys.executable, str(WRITER), str(database), "3"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    for line in process.stdout:
        if line.startswith("in-flight"):
            break
    process.kill()
    process.wait()
    process.stdout.close()

    # The log still holds committed frames at this point; reopening replays it.
    assert sidecar(database, "-wal").exists()
    with connection(database) as conn:
        assert integrity_report(conn) is None
        assert conn.execute("SELECT count(*) FROM visits").fetchone()[0] == 3
        assert checkpoint_truncate(conn).truncated


def test_quarantine_takes_the_sidecars_and_deletes_nothing(tmp_path: Path) -> None:
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)
        with transaction(conn):
            add_visit(conn, 0)
        wal_bytes = sidecar(database, "-wal").read_bytes()
        assert wal_bytes
        moved = quarantine(database)

    assert not database.exists()
    assert not sidecar(database, "-wal").exists()
    assert not sidecar(database, "-shm").exists()
    assert moved.name.startswith("darts.corrupt-") and moved.suffix == ".db"
    assert moved.exists()
    # The uncheckpointed log is preserved alongside, not thrown away.
    assert sidecar(moved, "-wal").read_bytes() == wal_bytes


def test_quarantine_never_overwrites_an_earlier_quarantine(tmp_path: Path) -> None:
    database = tmp_path / "darts.db"
    stamp = "20260922T101530Z"
    first = unused_name(database, "corrupt", stamp)
    first.write_bytes(b"earlier")
    second = unused_name(database, "corrupt", stamp)

    assert first.name == "darts.corrupt-20260922T101530Z.db"
    assert second.name == "darts.corrupt-20260922T101530Z-1.db"
    assert first.read_bytes() == b"earlier"


def test_only_structural_damage_counts_as_corruption() -> None:
    assert is_corruption(sqlite3.DatabaseError("database disk image is malformed"))
    assert is_corruption(sqlite3.DatabaseError("file is not a database"))
    # A database that is merely unavailable must never be replaced.
    assert not is_corruption(sqlite3.OperationalError("unable to open database file"))
    assert not is_corruption(sqlite3.OperationalError("database is locked"))
    assert not is_corruption(sqlite3.OperationalError("disk I/O error"))
    assert not is_corruption(OSError("permission denied"))


def test_verifying_a_backup_does_not_modify_it(tmp_path: Path) -> None:
    """A read-write open would rewrite the journal mode of the file being checked."""
    stored = tmp_path / "stored.db"
    raw = sqlite3.connect(stored)
    raw.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    raw.commit()
    raw.close()
    before = stored.read_bytes()

    assert verify_file(stored) is None
    assert stored.read_bytes() == before
    assert not sidecar(stored, "-wal").exists()


def test_verify_file_reports_a_missing_file_rather_than_creating_one(tmp_path: Path) -> None:
    absent = tmp_path / "absent.db"
    assert verify_file(absent) == f"missing: {absent}"
    assert not absent.exists()


def test_verify_live_propagates_failures_that_are_not_corruption(tmp_path: Path) -> None:
    """A directory stands in for any unopenable path: never treated as damage."""
    directory = tmp_path / "darts.db"
    directory.mkdir()
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        verify_live(directory)


def test_verify_file_propagates_failures_that_are_not_corruption(tmp_path: Path) -> None:
    unreadable = tmp_path / "unreadable.db"
    unreadable.write_bytes(b"")
    unreadable.chmod(0o000)
    try:
        with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
            verify_file(unreadable)
    finally:
        unreadable.chmod(0o600)


def test_foreign_key_violations_fail_the_boot_check(tmp_path: Path) -> None:
    """integrity_check alone would pass this database; #12 requires both checks."""
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)
    orphan = sqlite3.connect(database)
    orphan.execute("PRAGMA foreign_keys = OFF")
    orphan.execute("INSERT INTO team_members VALUES (999, 998, 0)")
    orphan.commit()
    orphan.close()

    problem = verify_live(database)
    assert problem is not None
    # The orphan row breaks both of team_members' references, to teams and players.
    assert problem == "foreign_key_check: 2 violation(s)"
