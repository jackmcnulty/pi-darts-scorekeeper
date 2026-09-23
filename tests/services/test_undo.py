"""Undo: one dart deleted outright, and everything that followed from it withdrawn.

There is no `is_undone` flag and no redo. The row goes, its cricket rows go by
cascade, an emptied visit goes because the schema will not remove it, and the
caches, the leg's winner and the match's winner are rewritten from what is
left. The one thing undo must *not* touch is a bust, which is real play.
"""

import sqlite3

import pytest
from servicefixtures import (
    SCRIPT_301,
    SCRIPT_BUST,
    X01_301,
    cricket,
    make_match,
    throw_into_match,
    throw_labels,
)

from darts.engine.throws import Throw
from darts.repo.darts import cricket_effects_for_leg, darts_for_leg, point_awards_for_leg
from darts.repo.legs import get_leg, legs_for_match
from darts.repo.legstate import leg_state_for
from darts.repo.matches import CreatedMatch, get_match
from darts.repo.visits import visits_for_leg
from darts.services import play
from darts.services.errors import LegCompleteError, NothingToUndoError


def _fk_check(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute("PRAGMA foreign_key_check").fetchall()


def test_undo_mid_visit_removes_one_dart_and_keeps_the_visit(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    throw_labels(db, solo.leg_id, ["T20", "T19"])

    state = play.undo(db, solo.leg_id)

    assert len(darts_for_leg(db, solo.leg_id)) == 1
    assert len(visits_for_leg(db, solo.leg_id)) == 1
    assert state.current_leg.teams[0].remaining == 241
    assert state.current_leg.darts_left == 2


def test_undoing_the_first_dart_of_a_visit_removes_the_visit(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    throw_labels(db, solo.leg_id, ["T20", "T20", "T20", "T19"])
    assert len(visits_for_leg(db, solo.leg_id)) == 2

    state = play.undo(db, solo.leg_id)

    assert len(visits_for_leg(db, solo.leg_id)) == 1
    assert len(darts_for_leg(db, solo.leg_id)) == 3
    assert state.current_leg.next_thrower is not None
    assert state.current_leg.next_thrower.team_id == solo.team_ids[1]
    assert _fk_check(db) == []


def test_undo_to_an_empty_leg_leaves_nothing_behind(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    throw_labels(db, solo.leg_id, ["T20"])

    state = play.undo(db, solo.leg_id)

    assert darts_for_leg(db, solo.leg_id) == []
    assert visits_for_leg(db, solo.leg_id) == []
    assert leg_state_for(db, solo.leg_id) == []
    assert state.current_leg.current_visit is None
    assert state.current_leg.previous_visit is None
    assert state.current_leg.teams[0].remaining == 301
    assert state.current_leg.darts_left == 3


def test_undo_on_an_empty_leg_is_refused(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    with pytest.raises(NothingToUndoError, match="no darts to undo"):
        play.undo(db, solo.leg_id)


def test_undo_across_a_bust_restores_the_pre_bust_score(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    """The offending dart goes; the dart it voided counts again."""
    throw_labels(db, solo.leg_id, SCRIPT_BUST)
    busted = visits_for_leg(db, solo.leg_id)[-1]
    assert busted.is_bust is True

    state = play.undo(db, solo.leg_id)

    visit = visits_for_leg(db, solo.leg_id)[-1]
    assert visit.id == busted.id
    assert visit.is_bust is False
    assert visit.is_complete is False
    assert visit.score_after == 61
    darts = [d for d in darts_for_leg(db, solo.leg_id) if d.visit_id == visit.id]
    assert [(d.counted, d.caused_bust) for d in darts] == [(True, False)]
    assert state.current_leg.teams[0].remaining == 61
    assert state.current_leg.darts_left == 2


def test_undo_across_a_cutthroat_point_event_takes_the_points_back(
    db: sqlite3.Connection, players: list[int]
) -> None:
    members = ((players[0],), (players[1],), (players[2],))
    created = make_match(db, cricket("cutthroat"), members)
    state = throw_labels(db, created.leg_id, ["T20", "T20"])
    assert [t.points for t in state.current_leg.teams] == [0, 60, 60]
    assert len(point_awards_for_leg(db, created.leg_id)) == 2

    state = play.undo(db, created.leg_id)

    assert [t.points for t in state.current_leg.teams] == [0, 0, 0]
    assert point_awards_for_leg(db, created.leg_id) == []
    assert len(cricket_effects_for_leg(db, created.leg_id)) == 1
    assert _fk_check(db) == []


def test_undo_cascades_a_darts_effect_and_events_without_orphans(
    db: sqlite3.Connection, players: list[int]
) -> None:
    created = make_match(db, cricket("cutthroat"), ((players[0],), (players[1],)))
    throw_labels(db, created.leg_id, ["T20", "T20"])
    assert len(cricket_effects_for_leg(db, created.leg_id)) == 2
    assert len(point_awards_for_leg(db, created.leg_id)) == 1

    play.undo(db, created.leg_id)

    assert len(darts_for_leg(db, created.leg_id)) == 1
    assert len(cricket_effects_for_leg(db, created.leg_id)) == 1
    assert point_awards_for_leg(db, created.leg_id) == []
    assert _fk_check(db) == []


def test_undoing_the_winning_dart_reopens_the_leg_and_closes_the_next_one(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    state = throw_labels(db, solo.leg_id, SCRIPT_301)
    opened = state.active_leg_id
    assert opened is not None and opened != solo.leg_id

    state = play.undo(db, solo.leg_id)

    assert [leg.id for leg in legs_for_match(db, solo.match_id)] == [solo.leg_id]
    assert get_leg(db, solo.leg_id).winner_team_id is None
    assert get_leg(db, solo.leg_id).completed_at is None
    assert state.current_leg.winner_team_id is None
    assert state.active_leg_id == solo.leg_id
    assert state.legs_won == (0, 0)
    assert leg_state_for(db, solo.leg_id) != []


def test_undoing_the_match_winning_dart_unwins_the_match(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    # Under the default alternate rule each leg starts with the other team, so
    # the same nine-dart script wins one leg each way and leg 2 is the decider.
    state = throw_into_match(db, solo.leg_id, SCRIPT_301 * 3)
    assert state.is_complete is True
    assert state.legs_won == (2, 1)
    decider = state.current_leg.leg_id

    state = play.undo(db, decider)

    assert state.is_complete is False
    assert state.winner_team_id is None
    assert get_match(db, solo.match_id).completed_at is None
    assert state.legs_won == (1, 1)
    assert state.active_leg_id == decider


def test_undo_will_not_reach_back_past_a_leg_that_has_been_played(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    state = throw_into_match(db, solo.leg_id, SCRIPT_301 + ["T20"])
    assert state.active_leg_id != solo.leg_id

    with pytest.raises(LegCompleteError, match="is history"):
        play.undo(db, solo.leg_id)

    assert get_leg(db, solo.leg_id).winner_team_id == solo.team_ids[0]
    assert len(darts_for_leg(db, solo.leg_id)) == len(SCRIPT_301)


def test_repeated_undo_walks_a_whole_leg_back_to_nothing(
    db: sqlite3.Connection, players: list[int]
) -> None:
    created = make_match(db, X01_301, ((players[0],), (players[1],)))
    throw_labels(db, created.leg_id, SCRIPT_301)

    for _ in SCRIPT_301:
        play.undo(db, created.leg_id)

    assert darts_for_leg(db, created.leg_id) == []
    assert visits_for_leg(db, created.leg_id) == []
    assert leg_state_for(db, created.leg_id) == []
    assert [leg.id for leg in legs_for_match(db, created.match_id)] == [created.leg_id]
    assert _fk_check(db) == []


def test_undo_then_rethrow_reproduces_the_same_leg(
    db: sqlite3.Connection, players: list[int]
) -> None:
    created = make_match(db, X01_301, ((players[0],), (players[1],)))
    reference = throw_labels(db, created.leg_id, SCRIPT_301[:5], prefix="a")

    play.undo(db, created.leg_id)
    state = play.throw(
        db, leg_id=created.leg_id, dart=Throw.parse(SCRIPT_301[4]), client_dart_id="again"
    )

    assert state.current_leg.teams == reference.current_leg.teams
    assert state.current_leg.darts_left == reference.current_leg.darts_left
