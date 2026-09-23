"""Upgrade a database full of players to v3 without touching any of them."""

import sqlite3
from pathlib import Path

import pytest
from dbfixtures import migrations_upto

from darts.db.connection import connection
from darts.db.migrate import migrate


def test_upgrade_preserves_existing_players(tmp_path: Path) -> None:
    v2 = migrations_upto(tmp_path, 2)
    v3 = migrations_upto(tmp_path, 3)
    with connection(tmp_path / "existing.db") as conn:
        assert migrate(conn, v2) == (1, 2)
        conn.execute("INSERT INTO players(display_name) VALUES ('Ana')")
        before = tuple(conn.execute("SELECT * FROM players").fetchone())

        assert migrate(conn, v3) == (3,)

        # The two new columns append; nothing already there moves or changes.
        after = tuple(conn.execute("SELECT * FROM players").fetchone())
        assert after == (*before, None, None)
        row = conn.execute("SELECT display_name, short_name, accent_index FROM players").fetchone()
        assert tuple(row) == ("Ana", None, None)
        assert migrate(conn, v3) == ()


def test_upgraded_columns_enforce_their_checks(tmp_path: Path) -> None:
    with connection(tmp_path / "fresh.db") as conn:
        migrate(conn, migrations_upto(tmp_path, 3))
        conn.execute("INSERT INTO players(display_name) VALUES ('Ana')")

        # A short name is stored already trimmed, so the column cannot hold two
        # spellings of the same label -- " JM " and "JM" would compare and sort
        # as different strings in every screen that shows one.
        for bad in (" JM", "JM ", "", "TooLongName"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE players SET short_name = ?", (bad,))
        conn.execute("UPDATE players SET short_name = 'JM'")

        for bad_accent in (0, 9, -1):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE players SET accent_index = ?", (bad_accent,))
        conn.execute("UPDATE players SET accent_index = 8")

        # NULL stays legal in both: that is what every pre-v3 player holds.
        conn.execute("UPDATE players SET short_name = NULL, accent_index = NULL")
