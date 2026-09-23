"""x01 through the service: does it agree with the pure engine, dart for dart?

The first criterion of #15 is that it does. These tests replay whole legs of
301, 501 and 701 one HTTP-request-sized dart at a time and hold the result
against `engine.replay.replay` over the same throws, which knows nothing about
SQLite.
"""

import sqlite3

import pytest
from servicefixtures import (
    SCRIPT_301,
    SCRIPT_501,
    SCRIPT_701,
    SCRIPT_BUST,
    X01_301,
    X01_501,
    X01_701,
    engine_positions,
    make_match,
    pure_leg,
    throw_labels,
    x01_positions,
)

from darts.engine.throws import Throw
from darts.engine.x01 import Rule, X01Config
from darts.repo.config import GameConfig
from darts.repo.darts import darts_for_leg
from darts.repo.legstate import leg_state_for
from darts.repo.matches import CreatedMatch
from darts.repo.visits import visits_for_leg
from darts.services import play
from darts.services.errors import LegCompleteError

STARTS = {301: (X01_301, SCRIPT_301), 501: (X01_501, SCRIPT_501), 701: (X01_701, SCRIPT_701)}


@pytest.mark.parametrize("start", [301, 501, 701])
def test_a_replayed_leg_matches_the_pure_engine(
    db: sqlite3.Connection, players: list[int], start: int
) -> None:
    config, script = STARTS[start]
    members = ((players[0],), (players[1],))
    created = make_match(db, config, members)

    state = throw_labels(db, created.leg_id, script)

    expected = pure_leg(X01Config(start, Rule.STRAIGHT, Rule.DOUBLE), 0, members, script)
    assert x01_positions(state) == engine_positions(expected)
    assert state.current_leg.darts_thrown == len(expected.darts)
    assert state.current_leg.darts_left == expected.darts_left
    assert expected.winner == 0
    assert state.current_leg.winner_team_id == created.team_ids[0]


@pytest.mark.parametrize("start", [301, 501, 701])
def test_the_service_agrees_with_the_engine_after_every_dart(
    db: sqlite3.Connection, players: list[int], start: int
) -> None:
    """Not just at the end: the state after dart n is the engine's state after n."""
    config, script = STARTS[start]
    members = ((players[0],), (players[1],))
    created = make_match(db, config, members)
    rules = X01Config(start, Rule.STRAIGHT, Rule.DOUBLE)

    for index, label in enumerate(script):
        state = play.throw(
            db, leg_id=created.leg_id, dart=Throw.parse(label), client_dart_id=f"d{index}"
        )
        expected = pure_leg(rules, 0, members, script[: index + 1])
        assert x01_positions(state) == engine_positions(expected), f"after dart {index}"
        assert state.current_leg.darts_left == expected.darts_left


def test_the_next_thrower_follows_the_engines_rotation(
    db: sqlite3.Connection, players: list[int]
) -> None:
    members = ((players[0], players[2]), (players[1], players[3]))
    created = make_match(db, X01_501, members)

    state = throw_labels(db, created.leg_id, ["T20"] * 7)

    expected = pure_leg(X01Config(501, Rule.STRAIGHT, Rule.DOUBLE), 0, members, ["T20"] * 7)
    assert expected.next_thrower is not None
    assert state.current_leg.next_thrower is not None
    assert (
        state.current_leg.next_thrower.team_id == created.team_ids[expected.next_thrower.team_index]
    )
    assert state.current_leg.next_thrower.player_id == int(expected.next_thrower.member)


def test_a_visit_row_grows_dart_by_dart(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    throw_labels(db, solo.leg_id, ["T20"])
    assert visits_for_leg(db, solo.leg_id)[0].is_complete is False
    assert visits_for_leg(db, solo.leg_id)[0].score_after == 241

    throw_labels(db, solo.leg_id, ["T20", "T20"], prefix="more")
    visit = visits_for_leg(db, solo.leg_id)[0]
    assert visit.score_before == 301
    assert visit.score_after == 121
    assert visit.is_complete is True
    assert len(visits_for_leg(db, solo.leg_id)) == 1


def test_a_bust_is_preserved_in_full(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    """#15's explicit contrast with undo: a bust is play, not a misclick."""
    throw_labels(db, solo.leg_id, SCRIPT_BUST)

    visit = visits_for_leg(db, solo.leg_id)[-1]
    assert visit.is_bust is True
    assert visit.is_complete is True
    assert visit.score_after == visit.score_before == 121

    darts = [d for d in darts_for_leg(db, solo.leg_id) if d.visit_id == visit.id]
    assert len(darts) == 2
    assert [d.counted for d in darts] == [False, False]
    assert [d.caused_bust for d in darts] == [False, True]


def test_a_checkout_attempt_is_flagged_on_the_dart_it_was_thrown_at(
    db: sqlite3.Connection, players: list[int]
) -> None:
    """#7's definition: the score before the dart was a one-dart finish."""
    created = make_match(db, X01_301, ((players[0],), (players[1],)))
    throw_labels(db, created.leg_id, SCRIPT_301)

    attempts = [d.seq_in_leg for d in darts_for_leg(db, created.leg_id) if d.was_checkout_attempt]
    # Only the D2 at 4 remaining; 61 and 121 are not one-dart finishes.
    assert attempts == [8]


def test_an_unopened_team_throws_darts_that_do_not_count(
    db: sqlite3.Connection, players: list[int]
) -> None:
    config = GameConfig(
        game_type="x01", best_of=3, start_score=501, in_rule="double", out_rule="double"
    )
    created = make_match(db, config, ((players[0],), (players[1],)))

    state = throw_labels(db, created.leg_id, ["T20", "20", "D20"])

    assert [d.counted for d in darts_for_leg(db, created.leg_id)] == [False, False, True]
    assert state.current_leg.teams[0].remaining == 461
    assert state.current_leg.teams[0].is_open is True


def test_the_cache_tracks_the_leg_and_is_dropped_when_it_ends(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    assert leg_state_for(db, solo.leg_id) == []

    throw_labels(db, solo.leg_id, SCRIPT_301[:3])
    cached = leg_state_for(db, solo.leg_id)
    assert [(c.remaining, c.darts_thrown) for c in cached] == [(121, 3), (301, 0)]

    throw_labels(db, solo.leg_id, SCRIPT_301[3:], prefix="rest")
    assert leg_state_for(db, solo.leg_id) == []


def test_throwing_into_a_won_leg_is_refused(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    throw_labels(db, solo.leg_id, SCRIPT_301)
    before = darts_for_leg(db, solo.leg_id)

    with pytest.raises(LegCompleteError, match="already won"):
        play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="late")

    assert darts_for_leg(db, solo.leg_id) == before


def test_the_public_state_carries_the_visit_just_thrown(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    state = throw_labels(db, solo.leg_id, ["T20", "D20"])

    visit = state.current_leg.current_visit
    assert visit is not None
    assert visit.player_id == state.teams[0].members[0].player_id
    assert [d.label for d in visit.darts] == ["T20", "D20"]
    assert visit.score_before == 301
    assert visit.score_after == 201
    assert visit.is_complete is False
    # Two darts in, the visit is still open and there is nothing to recap yet.
    assert state.current_leg.previous_visit is None


def test_a_finished_visit_moves_from_current_to_previous(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    """The third dart closes a visit, so it stops being the one being thrown."""
    state = throw_labels(db, solo.leg_id, ["T20", "D20", "5"])

    assert state.current_leg.current_visit is None
    previous = state.current_leg.previous_visit
    assert previous is not None
    assert [d.label for d in previous.darts] == ["T20", "D20", "5"]
    assert previous.is_complete is True


def test_state_reads_without_writing(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    throw_labels(db, solo.leg_id, ["T20"])
    first = play.state(db, solo.leg_id)

    assert play.state(db, solo.leg_id) == first
    assert db.in_transaction is False
