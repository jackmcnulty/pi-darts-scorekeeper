"""Recording and withdrawing a winner, on a leg and on a match.

Both setters move `winner_team_id` and `completed_at` together, in both
directions, because #15's undo has to be able to unwin a leg it just won.
"""

import sqlite3

import pytest
from repofixtures import X01_501, count

from darts.db.connection import transaction
from darts.repo.darts import darts_for_leg
from darts.repo.errors import NotFoundError
from darts.repo.legs import create_leg, delete_leg, get_leg, legs_for_match, set_leg_winner
from darts.repo.matches import CreatedMatch, TeamSpec, create_match, get_match, set_match_winner


def test_setting_a_leg_winner_stamps_the_completion(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    set_leg_winner(db, doubles.leg_id, doubles.team_ids[1])

    leg = get_leg(db, doubles.leg_id)
    assert leg.winner_team_id == doubles.team_ids[1]
    assert leg.completed_at is not None


def test_clearing_a_leg_winner_clears_the_completion(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    set_leg_winner(db, doubles.leg_id, doubles.team_ids[0])

    set_leg_winner(db, doubles.leg_id, None)

    leg = get_leg(db, doubles.leg_id)
    assert leg.winner_team_id is None
    assert leg.completed_at is None


def test_set_leg_winner_rejects_an_unknown_leg(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no leg with id 404"):
        set_leg_winner(db, 404, None)


def test_a_leg_cannot_be_won_by_another_matchs_team(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    """The composite foreign key, reached through the setter."""
    with transaction(db):
        other = create_match(db, X01_501, (TeamSpec((players[1],)), TeamSpec((players[3],))))
    with pytest.raises(sqlite3.IntegrityError):
        set_leg_winner(db, doubles.leg_id, other.team_ids[0])


def test_setting_a_match_winner_stamps_the_completion(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    set_match_winner(db, doubles.match_id, doubles.team_ids[0])

    match = get_match(db, doubles.match_id)
    assert match.winner_team_id == doubles.team_ids[0]
    assert match.completed_at is not None


def test_clearing_a_match_winner_clears_the_completion(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    set_match_winner(db, doubles.match_id, doubles.team_ids[0])

    set_match_winner(db, doubles.match_id, None)

    match = get_match(db, doubles.match_id)
    assert match.winner_team_id is None
    assert match.completed_at is None


def test_set_match_winner_rejects_an_unknown_match(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no match with id 404"):
        set_match_winner(db, 404, None)


def test_deleting_a_leg_takes_its_play_with_it(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    with transaction(db):
        second = create_leg(
            db, match_id=doubles.match_id, leg_index=1, starting_team_id=doubles.team_ids[1]
        )

    delete_leg(db, second)

    assert [leg.id for leg in legs_for_match(db, doubles.match_id)] == [doubles.leg_id]
    assert darts_for_leg(db, second) == []
    assert count(db, "legs") == 1
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_delete_leg_rejects_an_unknown_leg(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no leg with id 404"):
        delete_leg(db, 404)
