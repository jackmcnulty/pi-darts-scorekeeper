"""Legs: what `create_match` opens, and reading them back in playing order."""

import sqlite3

import pytest
from repofixtures import X01_501

from darts.db.connection import transaction
from darts.repo.errors import NotFoundError
from darts.repo.legs import create_leg, get_leg, legs_for_match
from darts.repo.matches import CreatedMatch, TeamSpec, create_match


def test_get_leg_returns_what_create_match_opened(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    leg = get_leg(db, doubles.leg_id)
    assert leg.id == doubles.leg_id
    assert leg.match_id == doubles.match_id
    assert leg.leg_index == 0
    assert leg.starting_team_id == doubles.team_ids[0]
    assert leg.started_at
    assert leg.winner_team_id is None
    assert leg.completed_at is None


def test_get_missing_leg_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no leg with id 99"):
        get_leg(db, 99)


def test_legs_come_back_in_playing_order(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    """Inserted out of order so that ORDER BY leg_index is what is being tested."""
    with transaction(db):
        for leg_index in (2, 1):
            create_leg(
                db,
                match_id=doubles.match_id,
                leg_index=leg_index,
                starting_team_id=doubles.team_ids[leg_index % 2],
            )

    legs = legs_for_match(db, doubles.match_id)
    assert [leg.leg_index for leg in legs] == [0, 1, 2]
    assert legs[0].id == doubles.leg_id


def test_legs_of_an_unknown_match_are_empty(db: sqlite3.Connection) -> None:
    assert legs_for_match(db, 99) == []


def test_a_leg_cannot_start_with_a_team_from_another_match(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    """The composite FK is why starting_team_id is an id and not an index."""
    with transaction(db):
        other = create_match(db, X01_501, [TeamSpec((players[0],)), TeamSpec((players[1],))])

    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        create_leg(db, match_id=doubles.match_id, leg_index=1, starting_team_id=other.team_ids[0])


def test_leg_index_is_unique_within_a_match(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        create_leg(db, match_id=doubles.match_id, leg_index=0, starting_team_id=doubles.team_ids[0])
