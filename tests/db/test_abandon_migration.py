"""Upgrade an existing v1 database without rewriting history."""

import shutil
import sqlite3
from pathlib import Path

import pytest

from darts.db.connection import connection
from darts.db.migrate import MIGRATIONS, migrate


def test_upgrade_preserves_existing_matches(tmp_path: Path) -> None:
    old = tmp_path / "v1"
    old.mkdir()
    shutil.copyfile(MIGRATIONS / "0001_init.sql", old / "0001_init.sql")
    with connection(tmp_path / "existing.db") as conn:
        assert migrate(conn, old) == (1,)
        conn.execute("INSERT INTO players(display_name) VALUES ('Ana')")
        conn.execute(
            "INSERT INTO matches(config_json, game_type, variant, best_of) "
            "VALUES ('{}', 'cricket', 'standard', 1)"
        )
        before = tuple(conn.execute("SELECT * FROM matches").fetchone())
        assert migrate(conn) == (2,)
        after = tuple(conn.execute("SELECT * FROM matches").fetchone())
        assert after == (*before, None)
        assert conn.execute("SELECT display_name FROM players").fetchone()[0] == "Ana"
        conn.execute("UPDATE matches SET abandoned_at = '2026-09-23T00:00:00.000Z'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE matches SET completed_at = '2026-09-23T00:00:00.000Z'")
        assert migrate(conn) == ()
