"""Real temporary files: in-memory SQLite cannot test WAL durability settings."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from darts.db.connection import connection
from darts.db.migrate import migrate


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with connection(tmp_path / "test.db") as conn:
        migrate(conn)
        yield conn


@pytest.fixture
def populated(db: sqlite3.Connection) -> sqlite3.Connection:
    for player in (1, 2, 3):
        db.execute("INSERT INTO players(id, display_name) VALUES (?, ?)", (player, f"P{player}"))
    db.execute("""INSERT INTO matches(id, config_json, game_type, variant, best_of)
                  VALUES (1, '{}', 'cricket', 'cutthroat', 3)""")
    for player in (1, 2, 3):
        db.execute(
            "INSERT INTO teams(id, match_id, team_index, is_solo) VALUES (?, 1, ?, 1)",
            (player * 10, player - 1),
        )
        db.execute("INSERT INTO team_members VALUES (?, ?, 0)", (player * 10, player))
    db.execute("INSERT INTO legs(id, match_id, leg_index, starting_team_id) VALUES (100, 1, 0, 10)")
    db.execute("""INSERT INTO visits(id, leg_id, match_id, team_id, player_id, visit_index,
                  team_visit_index, score_before, score_after)
                  VALUES (1000, 100, 1, 10, 1, 0, 0, 0, 0)""")
    return db
