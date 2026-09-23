"""A migrated database on real disk, and the players every service test starts from.

Same shape as `tests/repo/conftest.py`, and for the same reason: `connect`
refuses an in-memory database because it cannot use WAL.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from servicefixtures import X01_301, make_match

from darts.db.connection import connection
from darts.db.migrate import migrate
from darts.repo.matches import CreatedMatch
from darts.repo.players import create_player


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with connection(tmp_path / "test.db") as conn:
        migrate(conn)
        yield conn


@pytest.fixture
def players(db: sqlite3.Connection) -> list[int]:
    """Four active players, in creation order."""
    return [create_player(db, name).id for name in ("Ana", "Ben", "Cal", "Dee")]


@pytest.fixture
def solo(db: sqlite3.Connection, players: list[int]) -> CreatedMatch:
    """Ana against Ben at 301, straight in and double out."""
    return make_match(db, X01_301, ((players[0],), (players[1],)))
