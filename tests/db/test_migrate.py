import hashlib
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from darts.db.connection import connection
from darts.db.migrate import MIGRATIONS, MigrationError, discover, migrate
from darts.db.views import view_names
from darts.tools.migrate import main

#: The versions this repository's own migrations define, read rather than
#: written down. Every "the database is at version N" assertion below is about
#: the runner, not about which migration happens to be last, and #22 adding
#: 0003 should not mean editing a dozen literal 2s to find that out.
ALL_VERSIONS = tuple(m.version for m in discover(MIGRATIONS))
LATEST = ALL_VERSIONS[-1]
#: The filename prefix a test's own extra migration has to use to be next.
NEXT = f"{LATEST + 1:04d}"


@pytest.fixture
def migrations(tmp_path: Path) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, target)
    return target


def test_fresh_apply_and_noop(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    path.touch()
    with connection(path) as conn:
        assert migrate(conn) == ALL_VERSIONS
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
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
    (migrations / f"{NEXT}_extend.sql").write_text(
        "-- semicolon ; in a comment\n/* prefix */\n"
        "ALTER TABLE \"players\" ADD COLUMN note TEXT DEFAULT 'a;b';\n"
        "CREATE INDEX players_note ON players(note); -- trailing comment",
        encoding="utf-8",
    )
    assert migrate(db, migrations) == (LATEST + 1,)
    db.execute("INSERT INTO players(display_name) VALUES ('P')")
    assert db.execute("SELECT note FROM players").fetchone()[0] == "a;b"
    assert db.execute("PRAGMA user_version").fetchone()[0] == LATEST + 1
    assert migrate(db, migrations) == ()


def test_applied_file_edit_is_rejected_before_new_work(
    db: sqlite3.Connection, migrations: Path
) -> None:
    path = migrations / "0001_init.sql"
    path.write_bytes(path.read_bytes() + b"\n-- changed\n")
    (migrations / f"{NEXT}_next.sql").write_text(
        "CREATE TABLE next_step (id INTEGER);", encoding="utf-8"
    )
    with pytest.raises(MigrationError, match="applied migration changed"):
        migrate(db, migrations)
    assert db.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert not db.in_transaction
    assert db.execute("SELECT name FROM sqlite_schema WHERE name='next_step'").fetchone() is None


def test_failure_rolls_back_entire_file_but_preserves_previous(
    tmp_path: Path, migrations: Path
) -> None:
    (migrations / f"{NEXT}_fail.sql").write_text(
        "CREATE TABLE should_rollback (id INTEGER); CREATE TABLE broken (id INTEGER,);",
        encoding="utf-8",
    )
    with connection(tmp_path / "rollback.db") as conn:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn, migrations)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
        count = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
        assert count == len(ALL_VERSIONS)
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
    (migrations / f"{NEXT}_bad.sql").write_text(sql, encoding="utf-8")
    with pytest.raises(MigrationError):
        migrate(db, migrations)
    assert db.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert not db.in_transaction


@pytest.mark.parametrize("name", ["wrong.sql", f"{LATEST + 2:04d}_gap.sql", "0001_duplicate.sql"])
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
        f"PRAGMA user_version={LATEST + 1}",
        "DELETE FROM schema_migrations",
        "UPDATE schema_migrations SET version=version+10",
        "UPDATE schema_migrations SET name='renamed.sql' WHERE version=1",
    ],
)
def test_migration_metadata_drift_rejected(db: sqlite3.Connection, corruption: str) -> None:
    db.execute(corruption)
    with pytest.raises(MigrationError):
        migrate(db)


def test_database_newer_than_code_rejected(db: sqlite3.Connection, migrations: Path) -> None:
    second = migrations / f"{NEXT}_new.sql"
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
    """Two boots at once apply every migration exactly once, between them.

    Not "one of them does all the work". `migrate` takes the writer lock per
    *file*, so a caller that loses the race for 0001 may well win it for 0002
    and 0003, and which of them gets which is a property of the scheduler.
    What the runner actually guarantees -- because `_verify_applied` runs
    inside the lock -- is that no file is applied twice and none is skipped,
    which is what a Pi power-cycled into a double start needs.

    That distinction was invisible while there were two migrations and a
    narrow window. #22's third made it show up on CI.
    """
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

    # Between them, each migration exactly once...
    assert sorted(version for result in results for version in result) == list(ALL_VERSIONS)
    # ...and neither of them applied one out of order.
    for result in results:
        assert list(result) == sorted(result)

    with connection(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
        ledger = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert [row[0] for row in ledger] == list(ALL_VERSIONS)


def test_cli_fresh_noop_and_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "cli.db"
    assert main([str(path)]) == 0
    # The view count comes from views.sql, which #19 grew; the point of this
    # assertion is the migration count and the exit status, not that number.
    with connection(path) as conn:
        views = len(view_names(conn))
    version, applied = LATEST, len(ALL_VERSIONS)
    expected = f"schema version {version}; applied {applied} migration(s); {views} view(s)\n"
    assert capsys.readouterr().out == expected
    assert main([str(path)]) == 0
    expected = f"schema version {version}; applied 0 migration(s); {views} view(s)\n"
    assert capsys.readouterr().out == expected
    assert main([str(tmp_path / "missing" / "cannot.db")]) == 1
    assert "migration failed" in capsys.readouterr().err
