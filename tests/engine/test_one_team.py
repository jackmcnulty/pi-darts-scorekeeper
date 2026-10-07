"""The engine with one team: #68's single-sided match, practice with nobody to beat.

Nothing in the engine special-cases one team. Cricket's opponent rules are
written as `all(...)` over the opponents, so with none they are vacuously
true: every target is dead from the first dart (nothing ever scores), and
closing all seven is a win in every variant. Rotation has an explicit
one-team branch. These tests pin that reading down, because #68 relies on it
rather than adding a rule of its own -- a later change to either `all` would
otherwise silently make solo cricket unwinnable or let it score.

"Win" here is the engine's word for finishing a leg. That a single-sided
finish is never a *win* is a stats decision, enforced in views.sql.
"""

import pytest

from darts.engine import cricket, x01
from darts.engine.replay import LegConfig, MatchConfig, replay, replay_match
from darts.engine.rotation import StartRule, starting_team, thrower_for
from darts.engine.throws import Throw
from darts.engine.types import Team, Thrower

SOLO = (Team(("Ana",)),)
PAIR = (Team(("Ana", "Ben")),)
X01_301 = x01.X01Config(301, x01.Rule.STRAIGHT, x01.Rule.DOUBLE)
#: Six triples and two bulls: all seven closed with one surplus mark nowhere.
CLOSE_OUT = "T20 T19 T18 T17 T16 T15 BULL 25"
#: 301 in six darts: 180, then T20 T7 D20 for the remaining 121.
CHECKOUT_301 = "T20 T20 T20 T20 T7 D20"


def darts(labels: str) -> tuple[Throw, ...]:
    return tuple(Throw.parse(label) for label in labels.split())


@pytest.mark.parametrize("variant", list(cricket.Variant))
def test_solo_cricket_never_scores_and_ends_on_the_seventh_close(
    variant: cricket.Variant,
) -> None:
    cfg = cricket.CricketConfig(variant)
    # Surplus on every target, which against an open opponent would pay out
    # under standard and cut-throat.
    script = "T20 T20 T19 T19 T18 T18 T17 T17 T16 T16 T15 T15 BULL BULL"
    leg = replay(LegConfig(cfg), SOLO, darts(script))
    assert leg.winner == 0
    assert leg.next_thrower is None
    assert leg.teams[0] == cricket.CricketTeamState(
        marks=(cricket.MARKS_TO_CLOSE,) * len(cricket.TARGETS), points=0
    )
    for visit in leg.visits:
        assert isinstance(visit.outcome, cricket.VisitOutcome)
        assert all(d.point_events == () for d in visit.outcome.darts)
    # One dart short of the last close is not finished.
    assert replay(LegConfig(cfg), SOLO, darts(script)[:-1]).winner is None


@pytest.mark.parametrize("variant", list(cricket.Variant))
def test_apply_dart_without_opponents_wastes_surplus(variant: cricket.Variant) -> None:
    cfg = cricket.CricketConfig(variant)
    closed = cricket.CricketTeamState(marks=(3, 0, 0, 0, 0, 0, 0), points=0)
    outcome = cricket.apply_dart(closed, Throw.parse("T20"), cfg)
    assert outcome.points == 0
    assert outcome.point_events == ()
    assert outcome.surplus_marks == 3
    assert outcome.state.points == 0


@pytest.mark.parametrize("variant", list(cricket.Variant))
def test_finished_solo_cricket_leg_refuses_another_dart(variant: cricket.Variant) -> None:
    cfg = cricket.CricketConfig(variant)
    done = cricket.CricketTeamState(marks=(3,) * len(cricket.TARGETS), points=0)
    with pytest.raises(ValueError, match="already won"):
        cricket.apply_dart(done, Throw.parse("MISS"), cfg)


def test_solo_x01_ends_at_checkout_and_busts_as_ever() -> None:
    leg = replay(LegConfig(X01_301), SOLO, darts(CHECKOUT_301))
    assert leg.winner == 0
    assert leg.next_thrower is None
    # A bust hands the throw back to the same, only, team.
    bust = replay(LegConfig(X01_301), SOLO, darts("T20 T20 T20 T20 T20"))
    assert bust.winner is None
    assert bust.teams[0].remaining == 121
    assert bust.next_thrower == Thrower(0, 0, "Ana")
    assert bust.darts_left == 3


def test_every_visit_belongs_to_the_one_team_and_members_rotate() -> None:
    leg = replay(LegConfig(X01_301), PAIR, (Throw.parse("MISS"),) * 12)
    assert [v.thrower.member for v in leg.visits] == ["Ana", "Ben"] * 2
    assert {v.thrower.team_index for v in leg.visits} == {0}
    assert thrower_for(PAIR, 0, 3) == Thrower(0, 1, "Ben")


@pytest.mark.parametrize("rule", list(StartRule))
@pytest.mark.parametrize("leg_index", [0, 1, 4])
def test_every_start_rule_starts_the_only_team(rule: StartRule, leg_index: int) -> None:
    previous = None if leg_index == 0 else 0
    assert starting_team(1, leg_index, rule, 0, previous, 0) == 0


def test_a_second_team_cannot_start_a_one_team_leg() -> None:
    with pytest.raises(ValueError, match="starter outside team range"):
        starting_team(1, 0, StartRule.FIXED, fixed_team=1)
    with pytest.raises(ValueError, match="valid starting team"):
        replay(LegConfig(X01_301, starting_team=1), SOLO, ())


@pytest.mark.parametrize("rule", list(StartRule))
def test_solo_match_takes_legs_to_win_legs(rule: StartRule) -> None:
    """Best of 3 is first to 2, so a solo match is two finished legs."""
    config = MatchConfig(X01_301, best_of=3, start_rule=rule)
    one = replay_match(config, SOLO, (darts(CHECKOUT_301),))
    assert one.winner is None
    assert one.legs_won == (1,)
    assert one.current_leg is not None
    assert one.current_leg.next_thrower == Thrower(0, 0, "Ana")
    two = replay_match(config, SOLO, (darts(CHECKOUT_301),) * 2)
    assert two.winner == 0
    assert two.legs_won == (2,)
    assert two.current_leg is None
