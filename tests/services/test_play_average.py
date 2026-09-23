"""The per-leg 3-dart average #24's scoreboard shows beside each team's score.

The arithmetic is #19's, deliberately: `3 * sum(counted * score) / darts`, which
is what `stats/sql/x01.sql` computes and what `derive.three_dart_average` exists
to avoid re-deriving in a client. The interesting claims are all about the two
edges -- a team with no darts has no average rather than an average of nought,
and a bust scores nothing while still costing the darts it took -- because those
are the two a client computing `(start - remaining) / darts * 3` by hand would
plausibly get wrong.
"""

import sqlite3

import pytest
from servicefixtures import SCRIPT_BUST, X01_301, cricket, make_match, throw_labels

from darts.repo.config import GameConfig
from darts.repo.matches import CreatedMatch
from darts.services import play


def averages(state: object) -> list[float | None]:
    """Each team's average in `current_leg`, positional to the teams."""
    assert isinstance(state, play.public.GameState)
    return [team.three_dart_average for team in state.current_leg.teams]


def test_a_team_with_no_darts_has_no_average(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    """None, not 0.0 -- #19's rule for the whole stats layer.

    A fresh leg is the state the play screen opens on, so this is the value the
    first paint renders, and 0.0 would read as a terrible average rather than as
    an absent one.
    """
    assert averages(play.state(db, solo.leg_id)) == [None, None]


def test_a_maximum_visit_averages_one_eighty(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    """A 180 is three darts scoring 180, so the 3-dart average is 180.0.

    The check worth doing by eye, because it is the one value where a factor of
    three going the wrong way is obvious: a maximum visit cannot average 60.
    """
    state = throw_labels(db, solo.leg_id, ["T20", "T20", "T20"])

    assert averages(state) == [180.0, None]


def test_the_average_updates_mid_visit(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    """Two darts in, the average is over two darts and not over a whole visit.

    `current_visit` is part-thrown here, and the payload is repainted after
    every dart, so the number has to mean something at that moment.
    """
    state = throw_labels(db, solo.leg_id, ["T20", "20"])

    assert averages(state) == [120.0, None]


def test_a_bust_scores_nothing_but_still_costs_its_darts(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    """The case a client deriving this itself would get wrong.

    `SCRIPT_BUST` leaves the first team on 121 after one visit of 180, then
    throws T20, T20 -- which reaches 1 and voids the visit under double out. So
    the team has thrown five darts and only the first three counted: 3 * 180 / 5.
    Reverting the score without also charging the darts would say 3 * 180 / 3,
    and charging the darts without voiding the score would say 3 * 300 / 5.
    """
    state = throw_labels(db, solo.leg_id, SCRIPT_BUST)

    busted, opponent = state.current_leg.teams
    assert busted.remaining == 121, "the bust reverted the score"
    assert busted.darts_thrown == 5
    assert busted.three_dart_average == pytest.approx(3.0 * 180 / 5)
    assert busted.three_dart_average == pytest.approx(108.0)
    # The opponent's own visit is untouched by the other team's bust.
    assert opponent.three_dart_average == pytest.approx(180.0)


def test_a_missed_dart_costs_the_denominator(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    """A miss is a dart: one triple twenty and two misses averages 60, not 180."""
    state = throw_labels(db, solo.leg_id, ["T20", "MISS", "MISS"])

    assert averages(state) == [60.0, None]


def test_an_uncounted_opening_dart_costs_the_denominator(
    db: sqlite3.Connection, players: list[int]
) -> None:
    """Under double in, darts thrown before the double score nothing but count.

    Same `counted` flag as a bust, reached a different way -- which is why
    `derive.three_dart_average` multiplies by it rather than special-casing the
    bust.
    """
    # Built rather than `model_copy`d off X01_301: an update dict bypasses
    # validation, so `in_rule` would stay a plain str and serialise with a
    # pydantic warning instead of becoming the `Rule` enum.
    double_in = GameConfig(
        game_type="x01", best_of=3, start_score=301, in_rule="double", out_rule="double"
    )
    created = make_match(db, double_in, ((players[0],), (players[1],)))

    # T20 does not open the leg, so it scores nothing; D20 opens and scores 40.
    state = throw_labels(db, created.leg_id, ["T20", "D20"])

    opener = state.current_leg.teams[0]
    assert opener.remaining == 301 - 40
    assert opener.darts_thrown == 2
    assert opener.three_dart_average == pytest.approx(3.0 * 40 / 2)


def test_a_two_player_team_pools_both_members_darts(
    db: sqlite3.Connection, players: list[int]
) -> None:
    """It is a team average, because a `ScoreCard` is a team.

    Ana throws 180 and Cal, her partner, throws 60 next time round -- so the
    team's six darts have scored 240 and the card reads 120, not either member's
    own figure.
    """
    created = make_match(db, X01_301, ((players[0], players[2]), (players[1], players[3])))

    state = throw_labels(
        db,
        created.leg_id,
        ["T20", "T20", "T20"] + ["MISS", "MISS", "MISS"] + ["20", "20", "20"],
    )

    assert state.current_leg.teams[0].three_dart_average == pytest.approx(3.0 * 240 / 6)
    assert state.current_leg.teams[1].three_dart_average == pytest.approx(0.0)


def test_cricket_has_no_three_dart_average(db: sqlite3.Connection, players: list[int]) -> None:
    """Cricket is scored by marks per round; a 3-dart average is not about it.

    The same reason `report.X01Stats` is computed from x01 darts alone, and the
    same split as `remaining` and `marks` on this dataclass.
    """
    created = make_match(db, cricket("standard"), ((players[0],), (players[1],)))

    state = throw_labels(db, created.leg_id, ["T20", "T19", "T18"])

    assert averages(state) == [None, None]
