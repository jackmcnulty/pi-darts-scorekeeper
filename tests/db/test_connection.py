import sqlite3
from pathlib import Path

import pytest

from darts.db.connection import connect, connection, transaction


def test_every_connection_has_pragmas_and_named_rows(tmp_path: Path) -> None:
    path = tmp_path / "settings.db"
    for _ in range(2):
        with connection(path) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            assert conn.execute("SELECT 7 AS value").fetchone()["value"] == 7
            assert conn.isolation_level is None
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("PRAGMA busy_timeout = 1")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_memory_database_is_rejected() -> None:
    with pytest.raises(ValueError, match="WAL"):
        connect(":memory:")


def test_transaction_commit_rollback_and_nesting(db: sqlite3.Connection) -> None:
    with transaction(db):
        db.execute("INSERT INTO players(display_name) VALUES ('saved')")
    with pytest.raises(RuntimeError), transaction(db):
        db.execute("INSERT INTO players(display_name) VALUES ('rolled back')")
        raise RuntimeError("injected failure")
    assert [r[0] for r in db.execute("SELECT display_name FROM players")] == ["saved"]
    with transaction(db, immediate=False):
        with pytest.raises(ValueError, match="nested"), transaction(db):
            pytest.fail("nested transaction entered")
        assert db.in_transaction
    assert not db.in_transaction


def test_commit_failure_rolls_back(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute("PRAGMA defer_foreign_keys = ON")
        db.execute("INSERT INTO team_members VALUES (999, 999, 0)")
    assert not db.in_transaction
    assert db.execute("SELECT count(*) FROM team_members").fetchone()[0] == 0


def test_connection_closes_after_exception(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), connection(tmp_path / "error.db") as conn:
        raise RuntimeError("injected")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_non_api_connections_keep_thread_affinity(db: sqlite3.Connection) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(db.execute, "SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError, match="same thread"):
            future.result()
