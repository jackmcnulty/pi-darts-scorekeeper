import hashlib
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from darts.db.connection import connection
from darts.db.migrate import MIGRATIONS, MigrationError, discover, migrate
from darts.tools.migrate import main


@pytest.fixture
def migrations(tmp_path: Path) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, target)
    return target


def test_fresh_apply_and_noop(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    path.touch()
    with connection(path) as conn:
        assert migrate(conn) == (1,)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        row = conn.execute("SELECT * FROM schema_migrations").fetchone()
        assert row["name"] == "0001_init.sql"
        assert row["sha256"] == hashlib.sha256((MIGRATIONS / row["name"]).read_bytes()).hexdigest()
        assert row["applied_at"].endswith("Z")
        snapshot = tuple(conn.iterdump())
        changes = conn.total_changes
        assert migrate(conn) == ()
        assert conn.total_changes == changes
        assert tuple(conn.iterdump()) == snapshot


def test_second_migration_order_and_quoted_semicolons(
    db: sqlite3.Connection, migrations: Path
) -> None:
    (migrations / "0002_extend.sql").write_text(
        "-- semicolon ; in a comment\n/* prefix */\n"
        "ALTER TABLE \"players\" ADD COLUMN note TEXT DEFAULT 'a;b';\n"
        "CREATE INDEX players_note ON players(note); -- trailing comment",
        encoding="utf-8",
    )
    assert migrate(db, migrations) == (2,)
    db.execute("INSERT INTO players(display_name) VALUES ('P')")
    assert db.execute("SELECT note FROM players").fetchone()[0] == "a;b"
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert migrate(db, migrations) == ()


def test_applied_file_edit_is_rejected_before_new_work(
    db: sqlite3.Connection, migrations: Path
) -> None:
    path = migrations / "0001_init.sql"
    path.write_bytes(path.read_bytes() + b"\n-- changed\n")
    (migrations / "0002_next.sql").write_text(
        "CREATE TABLE next_step (id INTEGER);", encoding="utf-8"
    )
    with pytest.raises(MigrationError, match="applied migration changed"):
        migrate(db, migrations)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not db.in_transaction
    assert db.execute("SELECT name FROM sqlite_schema WHERE name='next_step'").fetchone() is None


def test_failure_rolls_back_entire_file_but_preserves_previous(
    tmp_path: Path, migrations: Path
) -> None:
    (migrations / "0002_fail.sql").write_text(
        "CREATE TABLE should_rollback (id INTEGER); CREATE TABLE broken (id INTEGER,);",
        encoding="utf-8",
    )
    with connection(tmp_path / "rollback.db") as conn:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn, migrations)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 1
        assert (
            conn.execute("SELECT name FROM sqlite_schema WHERE name='should_rollback'").fetchone()
            is None
        )


def test_failure_in_initial_file_leaves_no_partial_schema(tmp_path: Path, migrations: Path) -> None:
    path = migrations / "0001_init.sql"
    path.write_text(path.read_text() + "\nCREATE TABLE broken (id INTEGER,);", encoding="utf-8")
    with connection(tmp_path / "initial-failure.db") as conn:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn, migrations)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall() == []


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE players;",
        "ALTER TABLE players RENAME TO people;",
        "ALTER TABLE players DROP COLUMN display_name;",
        "DELETE FROM players;",
        "UPDATE players SET display_name='x';",
        "PRAGMA user_version=99;",
        "BEGIN;",
        "COMMIT;",
        "CREATE TABLE missing_semicolon (id INTEGER)",
        "-- comments only",
    ],
)
def test_only_additive_transaction_owned_ddl_is_accepted(
    db: sqlite3.Connection, migrations: Path, sql: str
) -> None:
    (migrations / "0002_bad.sql").write_text(sql, encoding="utf-8")
    with pytest.raises(MigrationError):
        migrate(db, migrations)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not db.in_transaction


@pytest.mark.parametrize("name", ["wrong.sql", "0003_gap.sql", "0001_duplicate.sql"])
def test_invalid_migration_sequences(migrations: Path, name: str) -> None:
    (migrations / name).write_text("CREATE TABLE more (id INTEGER);", encoding="utf-8")
    with pytest.raises(MigrationError):
        discover(migrations)


def test_empty_migration_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(MigrationError):
        discover(tmp_path)


@pytest.mark.parametrize(
    "corruption",
    [
        "PRAGMA user_version=2",
        "DELETE FROM schema_migrations",
        "UPDATE schema_migrations SET version=2",
        "UPDATE schema_migrations SET name='renamed.sql'",
    ],
)
def test_migration_metadata_drift_rejected(db: sqlite3.Connection, corruption: str) -> None:
    db.execute(corruption)
    with pytest.raises(MigrationError):
        migrate(db)


def test_database_newer_than_code_rejected(db: sqlite3.Connection, migrations: Path) -> None:
    second = migrations / "0002_new.sql"
    second.write_text("CREATE TABLE extra (id INTEGER);", encoding="utf-8")
    migrate(db, migrations)
    with pytest.raises(MigrationError, match="available files"):
        migrate(db, MIGRATIONS)


def test_nonzero_version_without_ledger_rejected(tmp_path: Path) -> None:
    with connection(tmp_path / "unknown.db") as conn:
        conn.execute("PRAGMA user_version=1")
        with pytest.raises(MigrationError):
            migrate(conn)


def test_concurrent_startups_serialize(tmp_path: Path) -> None:
    path = tmp_path / "shared.db"
    with connection(path):
        pass
    barrier = Barrier(2)

    def worker() -> tuple[int, ...]:
        with connection(path) as conn:
            barrier.wait(timeout=5)
            return migrate(conn)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert sorted(results) == [(), (1,)]


def test_cli_fresh_noop_and_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "cli.db"
    assert main([str(path)]) == 0
    assert capsys.readouterr().out == "schema version 1; applied 1 migration(s)\n"
    assert main([str(path)]) == 0
    assert capsys.readouterr().out == "schema version 1; applied 0 migration(s)\n"
    assert main([str(tmp_path / "missing" / "cannot.db")]) == 1
    assert "migration failed" in capsys.readouterr().err
