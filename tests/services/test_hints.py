"""Checkout hints as a pure function of a leg's state.

`services.hints.for_leg` touches no database, so it is tested against
hand-built `LegState` values rather than through a played leg. That is what
makes the awkward positions cheap to state: a leg whose stored winner and
replayed thrower disagree takes one line here and a corrupted database over in
`tests/api`.

The played-through-HTTP cases live in `tests/api/test_play_api.py`; these are
the ones about the decision, not the plumbing.
"""

import pytest

from darts.repo.config import GameConfig
from darts.services import hints
from darts.services.hints import NoHintsReason
from darts.services.state import LegState, TeamLegState, Thrower

X01 = GameConfig(game_type="x01", best_of=3, start_score=501, in_rule="straight", out_rule="double")
DOUBLE_IN = GameConfig(
    game_type="x01", best_of=3, start_score=501, in_rule="double", out_rule="double"
)
STRAIGHT_OUT = GameConfig(
    game_type="x01", best_of=3, start_score=501, in_rule="straight", out_rule="straight"
)
CRICKET = GameConfig(game_type="cricket", best_of=3, variant="standard")


def x01_leg(
    *,
    remaining: int = 170,
    darts_left: int = 3,
    is_open: bool = True,
    winner: int | None = None,
    thrower: bool = True,
) -> LegState:
    """A leg reduced to the four things `for_leg` actually reads."""
    return LegState(
        leg_id=1,
        leg_index=0,
        starting_team_id=10,
        winner_team_id=winner,
        is_complete=winner is not None,
        darts_thrown=0,
        darts_left=darts_left,
        next_thrower=Thrower(team_id=10, player_id=100, display_name="Ana") if thrower else None,
        teams=(
            TeamLegState(
                team_id=10,
                remaining=remaining,
                is_open=is_open,
                darts_thrown=0,
                points=0,
                marks=None,
            ),
            TeamLegState(
                team_id=11, remaining=501, is_open=True, darts_thrown=0, points=0, marks=None
            ),
        ),
        current_visit=None,
        previous_visit=None,
    )


def test_a_checkable_score_is_offered_best_first() -> None:
    result = hints.for_leg(X01, x01_leg())

    assert result.paths[0] == ("T20", "T20", "BULL")
    assert result.reason is None
    assert result.remaining == 170
    assert result.darts_left == 3
    assert result.team_id == 10
    assert result.player_id == 100


def test_the_out_rule_decides_how_many_ways_there_are() -> None:
    """Straight out finishes 170 three ways; double out allows only the bull."""
    assert len(hints.for_leg(STRAIGHT_OUT, x01_leg()).paths) == 3
    assert hints.for_leg(X01, x01_leg()).paths == (("T20", "T20", "BULL"),)


@pytest.mark.parametrize("darts_left", [1, 2])
def test_a_three_dart_finish_is_not_offered_on_fewer_darts(darts_left: int) -> None:
    result = hints.for_leg(X01, x01_leg(darts_left=darts_left))

    assert result.paths == ()
    assert result.reason is NoHintsReason.NOT_CHECKABLE
    # The position is still reported; it is the suggestion that is missing.
    assert result.remaining == 170
    assert result.darts_left == darts_left


def test_a_score_above_the_ceiling_is_not_checkable() -> None:
    assert hints.for_leg(X01, x01_leg(remaining=185)).reason is NoHintsReason.NOT_CHECKABLE


def test_cricket_is_refused_before_anything_else_is_asked() -> None:
    result = hints.for_leg(CRICKET, x01_leg())

    assert result.reason is NoHintsReason.NOT_X01
    assert result.paths == ()
    assert result.remaining is None


def test_an_unopened_team_is_offered_nothing_under_double_in() -> None:
    result = hints.for_leg(DOUBLE_IN, x01_leg(is_open=False))

    assert result.reason is NoHintsReason.NOT_OPEN
    assert result.paths == ()
    # Still their turn, even though the hint is withheld.
    assert result.team_id == 10


def test_a_won_leg_offers_nothing() -> None:
    result = hints.for_leg(X01, x01_leg(winner=10, thrower=False))

    assert result.reason is NoHintsReason.LEG_COMPLETE
    assert result.paths == ()


def test_an_abandoned_match_names_no_thrower_at_all() -> None:
    """Unlike the other refusals: #18 advertises nobody to throw for an abandoned match."""
    result = hints.for_leg(X01, x01_leg(), abandoned=True)

    assert result.reason is NoHintsReason.MATCH_ABANDONED
    assert result.paths == ()
    assert result.team_id is None
    assert result.player_id is None


def test_abandonment_outranks_a_leg_that_could_otherwise_be_finished() -> None:
    assert (
        hints.for_leg(X01, x01_leg(winner=10, thrower=False), abandoned=True).reason
        is NoHintsReason.MATCH_ABANDONED
    )


def test_a_leg_with_no_thrower_but_no_winner_is_refused_rather_than_guessed() -> None:
    """The two come from different places, so they can in principle disagree.

    `is_complete` is the stored `legs.winner_team_id`; `next_thrower` is
    replayed from the darts. A database where those have drifted is what
    `darts-verify` exists to find, and the hint layer's job in the meantime is
    to say it has nobody to suggest for rather than to index a None.
    """
    result = hints.for_leg(X01, x01_leg(thrower=False))

    assert result.reason is NoHintsReason.NO_THROWER
    assert result.paths == ()
    assert result.team_id is None
