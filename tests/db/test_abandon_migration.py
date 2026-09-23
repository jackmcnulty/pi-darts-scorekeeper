"""Upgrade an existing v1 database without rewriting history."""

import sqlite3
from pathlib import Path

import pytest
from dbfixtures import migrations_upto

from darts.db.connection import connection
from darts.db.migrate import migrate


def test_upgrade_preserves_existing_matches(tmp_path: Path) -> None:
    # Two partial directories rather than the real one: this test is about
    # 0002 exactly, and running every later migration too would make it fail
    # every time one is added.
    v1 = migrations_upto(tmp_path, 1)
    v2 = migrations_upto(tmp_path, 2)
    with connection(tmp_path / "existing.db") as conn:
        assert migrate(conn, v1) == (1,)
        conn.execute("INSERT INTO players(display_name) VALUES ('Ana')")
        conn.execute(
            "INSERT INTO matches(config_json, game_type, variant, best_of) "
            "VALUES ('{}', 'cricket', 'standard', 1)"
        )
        before = tuple(conn.execute("SELECT * FROM matches").fetchone())
        assert migrate(conn, v2) == (2,)
        after = tuple(conn.execute("SELECT * FROM matches").fetchone())
        assert after == (*before, None)
        assert conn.execute("SELECT display_name FROM players").fetchone()[0] == "Ana"
        conn.execute("UPDATE matches SET abandoned_at = '2026-09-23T00:00:00.000Z'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE matches SET completed_at = '2026-09-23T00:00:00.000Z'")
        assert migrate(conn, v2) == ()
