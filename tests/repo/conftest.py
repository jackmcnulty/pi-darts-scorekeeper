"""A migrated database on real disk, and the players and match most tests start from.

Same shape as `tests/db/conftest.py`, and for the same reason: `connect`
refuses an in-memory database because it cannot use WAL.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from repofixtures import X01_501

from darts.db.connection import connection, transaction
from darts.db.migrate import migrate
from darts.repo.matches import CreatedMatch, TeamSpec, create_match
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
def doubles(db: sqlite3.Connection, players: list[int]) -> CreatedMatch:
    """A 2v2 501 match: Ana and Cal as Reds, Ben and Dee as Blues."""
    teams = (
        TeamSpec(player_ids=(players[0], players[2]), name="Reds"),
        TeamSpec(player_ids=(players[1], players[3]), name="Blues"),
    )
    with transaction(db):
        return create_match(db, X01_501, teams)
