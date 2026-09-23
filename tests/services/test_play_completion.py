"""Finishing a leg, opening the next one, and finishing the match.

The dart that wins the deciding leg has to land the leg's winner, the match's
winner and its own row in one transaction -- there is no second request coming
to tidy up, and a power cut between them would leave a match nobody can finish.
"""

import sqlite3

import pytest
from servicefixtures import SCRIPT_301, X01_301, make_match, throw_into_match, throw_labels

from darts.db.connection import connection
from darts.engine.rotation import StartRule
from darts.engine.throws import Throw
from darts.repo.config import GameConfig
from darts.repo.darts import darts_for_leg
from darts.repo.legs import get_leg, legs_for_match
from darts.repo.legstate import leg_state_for
from darts.repo.matches import CreatedMatch, get_match
from darts.services import play
from darts.services.errors import MatchCompleteError


def test_winning_a_leg_opens_the_next_one(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    state = throw_labels(db, solo.leg_id, SCRIPT_301)

    legs = legs_for_match(db, solo.match_id)
    assert len(legs) == 2
    assert legs[0].winner_team_id == solo.team_ids[0]
    assert legs[0].completed_at is not None
    assert legs[1].leg_index == 1
    assert legs[1].winner_team_id is None
    assert state.active_leg_id == legs[1].id
    assert state.current_leg.leg_id == solo.leg_id
    assert state.current_leg.is_complete is True


def test_the_next_legs_starter_follows_the_start_rule(
    db: sqlite3.Connection, players: list[int]
) -> None:
    """Alternate, so leg 1 starts with the team that did not start leg 0."""
    created = make_match(db, X01_301, ((players[0],), (players[1],)))

    throw_labels(db, created.leg_id, SCRIPT_301)

    legs = legs_for_match(db, created.match_id)
    assert legs[0].starting_team_id == created.team_ids[0]
    assert legs[1].starting_team_id == created.team_ids[1]


def test_loser_starts_reads_the_previous_legs_winner(
    db: sqlite3.Connection, players: list[int]
) -> None:
    config = GameConfig(
        game_type="x01",
        best_of=3,
        start_score=301,
        in_rule="straight",
        out_rule="double",
        start_rule=StartRule.LOSER_STARTS,
    )
    created = make_match(db, config, ((players[0],), (players[1],)))

    throw_labels(db, created.leg_id, SCRIPT_301)

    legs = legs_for_match(db, created.match_id)
    assert legs[0].winner_team_id == created.team_ids[0]
    assert legs[1].starting_team_id == created.team_ids[1]


def test_the_deciding_dart_completes_leg_and_match_together(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    """Committed as one transaction: a second connection sees both or neither."""
    throw_into_match(db, solo.leg_id, SCRIPT_301 * 2 + SCRIPT_301[:-1])
    decider = play.state(db, solo.leg_id).active_leg_id
    assert decider is not None
    assert get_match(db, solo.match_id).winner_team_id is None

    state = play.throw(
        db, leg_id=decider, dart=Throw.parse(SCRIPT_301[-1]), client_dart_id="the-one"
    )

    assert state.is_complete is True
    assert state.winner_team_id == solo.team_ids[0]
    assert state.legs_won == (2, 1)
    assert state.active_leg_id is None
    assert get_leg(db, decider).winner_team_id == solo.team_ids[0]
    match = get_match(db, solo.match_id)
    assert match.winner_team_id == solo.team_ids[0]
    assert match.completed_at is not None
    assert len(legs_for_match(db, solo.match_id)) == 3


def test_a_won_match_is_visible_to_another_connection(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    throw_into_match(db, solo.leg_id, SCRIPT_301 * 3)
    path = db.execute("PRAGMA database_list").fetchone()["file"]

    with connection(path) as other:
        match = get_match(other, solo.match_id)
        legs = legs_for_match(other, solo.match_id)

    assert match.winner_team_id == solo.team_ids[0]
    assert [leg.winner_team_id is not None for leg in legs] == [True, True, True]


def test_a_won_match_opens_no_further_leg_and_takes_no_darts(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    state = throw_into_match(db, solo.leg_id, SCRIPT_301 * 3)
    legs = legs_for_match(db, solo.match_id)
    assert len(legs) == 3

    with pytest.raises(MatchCompleteError, match="already won"):
        play.throw(db, leg_id=legs[-1].id, dart=Throw.parse("T20"), client_dart_id="too-late")

    assert len(legs_for_match(db, solo.match_id)) == 3
    assert len(darts_for_leg(db, legs[-1].id)) == len(SCRIPT_301)
    assert state.active_leg_id is None


def test_a_finished_leg_keeps_no_cache_rows(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    """#13's invariant: the caches resume an interrupted leg and nothing else."""
    throw_labels(db, solo.leg_id, SCRIPT_301)

    assert leg_state_for(db, solo.leg_id) == []
    legs = legs_for_match(db, solo.match_id)
    assert leg_state_for(db, legs[1].id) == []


def test_a_failed_dart_leaves_no_partial_write(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    """The whole point of one dart, one transaction."""
    throw_labels(db, solo.leg_id, ["T20", "T20"])
    before = play.state(db, solo.leg_id)

    with pytest.raises(ValueError, match="segment must be"):
        play.throw(db, leg_id=solo.leg_id, dart=Throw(99, 1), client_dart_id="bad")

    assert db.in_transaction is False
    assert play.state(db, solo.leg_id) == before
