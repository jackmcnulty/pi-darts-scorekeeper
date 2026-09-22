"""Phase 1 integration: rotation, scoring, replay, match progression and undo."""

from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from darts.engine import cricket, x01
from darts.engine.checkout import suggest
from darts.engine.replay import LegConfig, MatchConfig, replay, replay_match, undo
from darts.engine.rotation import StartRule, starting_team, thrower_for
from darts.engine.throws import ALL_THROWS, Throw
from darts.engine.types import Team, Thrower

MISS = Throw.parse("MISS")
TEAMS = (Team(("A1", "A2")), Team(("B1",)))
X01 = x01.X01Config(61, x01.Rule.DOUBLE, x01.Rule.DOUBLE)


def darts(labels: str) -> tuple[Throw, ...]:
    return tuple(Throw.parse(label) for label in labels.split())


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        ((("A",), ("B",)), ["A", "B"] * 6),
        ((("A1", "A2"), ("B",)), ["A1", "B", "A2", "B"] * 3),
        ((("A1", "A2"), ("B1", "B2")), ["A1", "B1", "A2", "B2"] * 3),
        (
            (("A1", "A2"), ("B1", "B2", "B3")),
            ["A1", "B1", "A2", "B2", "A1", "B3", "A2", "B1", "A1", "B2", "A2", "B3"],
        ),
        ((("A1", "A2"), ("B",), ("C",)), ["A1", "B", "C", "A2", "B", "C"] * 2),
    ],
)
def test_rotation_tables(members: tuple[tuple[str, ...], ...], expected: list[str]) -> None:
    teams = tuple(Team(m) for m in members)
    cfg = LegConfig(X01)
    state = replay(cfg, teams, (MISS,) * 36)
    assert [v.thrower.member for v in state.visits] == expected
    for k in range(36):
        partial = replay(cfg, teams, (MISS,) * k)
        assert partial.next_thrower is not None
        assert partial.next_thrower.member == expected[k // 3]
        assert partial.darts_left == 3 - k % 3


@pytest.mark.parametrize("rules", [X01, *(cricket.CricketConfig(v) for v in cricket.Variant)])
def test_empty_leg_and_empty_undo(rules: x01.X01Config | cricket.CricketConfig) -> None:
    cfg = LegConfig(rules, 1)
    state = replay(cfg, TEAMS, ())
    assert state.next_thrower == Thrower(1, 0, "B1")
    assert state.darts_left == 3
    assert state.winner is None
    assert state.visits == ()
    assert undo(cfg, TEAMS, state) == state
    assert hash(state)


def test_bust_undo_restores_partial_visit_and_opening() -> None:
    cfg = LegConfig(X01)
    before = replay(cfg, TEAMS, darts("D20"))
    busted = replay(cfg, TEAMS, darts("D20 T20"))
    assert before.teams[0] == x01.X01TeamState(21, True, 1)
    assert busted.teams[0] == x01.X01TeamState(61, False, 2)
    assert busted.next_thrower == Thrower(1, 0, "B1")
    assert busted.darts_left == 3
    assert all(not d.counted for d in busted.visits[0].outcome.darts)
    assert undo(cfg, TEAMS, busted) == before
    # No padding after the bust: the very next actual dart belongs to B.
    resumed = replay(cfg, TEAMS, darts("D20 T20 D10 MISS MISS"))
    assert resumed.teams[1] == x01.X01TeamState(41, True, 3)
    assert resumed.next_thrower == Thrower(0, 1, "A2")


@pytest.mark.parametrize("bust_dart", [1, 2, 3])
def test_bust_advances_after_any_dart(bust_dart: int) -> None:
    cfg = LegConfig(x01.X01Config(20, x01.Rule.STRAIGHT, x01.Rule.DOUBLE))
    state = replay(cfg, TEAMS, (MISS,) * (bust_dart - 1) + darts("T20 1"))
    assert len(state.visits[0].outcome.darts) == bust_dart
    assert state.visits[1].thrower.team_index == 1
    assert state.darts_left == 2


def test_checkout_hint_to_complete_leg_and_undo() -> None:
    cfg = LegConfig(x01.X01Config(170, x01.Rule.STRAIGHT, x01.Rule.DOUBLE))
    path = suggest(170, 3, x01.Rule.DOUBLE)[0]
    state = replay(cfg, TEAMS, path)
    assert state.winner == 0
    assert state.next_thrower is None
    assert state.darts_left == 0
    assert state.teams[0] == x01.X01TeamState(0, True, 3)
    assert undo(cfg, TEAMS, state) == replay(cfg, TEAMS, path[:-1])
    with pytest.raises(ValueError, match="after the leg"):
        replay(cfg, TEAMS, path + (MISS,))


@pytest.mark.parametrize("variant", list(cricket.Variant))
def test_complete_cricket_leg(variant: cricket.Variant) -> None:
    cfg = LegConfig(cricket.CricketConfig(variant))
    recorded = darts("T20 T19 T18 MISS MISS MISS T17 T16 T15 MISS MISS MISS BULL 25")
    state = replay(cfg, TEAMS, recorded)
    assert state.winner == 0
    assert state.teams[0] == cricket.CricketTeamState((3,) * 7, 0)
    assert state.next_thrower is None
    assert state.darts_left == 0
    assert state.visits[-1].thrower.member == "A1"
    assert undo(cfg, TEAMS, state).darts_left == 2
    with pytest.raises(ValueError, match="after the leg"):
        replay(cfg, TEAMS, recorded + (MISS,))


def test_cutthroat_routing_and_undo_every_prefix() -> None:
    teams = (*TEAMS, Team(("C",)))
    cfg = LegConfig(cricket.CricketConfig(cricket.Variant.CUTTHROAT))
    recorded = darts(
        "T20 T20 MISS T19 T19 MISS T20 T20 12 "
        "T19 T18 T17 MISS MISS MISS MISS MISS MISS "
        "T16 T15 BULL MISS MISS MISS MISS MISS MISS 25"
    )
    state = replay(cfg, teams, recorded)
    assert state.winner == 0
    assert [s.points for s in state.teams] == [57, 120, 117]
    attributed = []
    for visit in state.visits:
        opponents = [i for i in range(3) if i != visit.thrower.team_index]
        assert isinstance(visit.outcome, cricket.VisitOutcome)
        for dart in visit.outcome.darts:
            for event in dart.point_events:
                assert event.recipient is not None
                attributed.append(
                    (visit.thrower.team_index, opponents[event.recipient], event.points)
                )
    assert attributed == [(0, 1, 60), (0, 2, 60), (1, 0, 57), (1, 2, 57), (2, 1, 60)]
    for k in range(len(recorded)):
        assert undo(cfg, teams, replay(cfg, teams, recorded[: k + 1])) == replay(
            cfg, teams, recorded[:k]
        )
    awarded = replay(cfg, teams, recorded[:2])
    assert [s.points for s in awarded.teams] == [0, 60, 60]
    assert [s.points for s in undo(cfg, teams, awarded).teams] == [0, 0, 0]


@settings(max_examples=80, deadline=None)
@given(
    sequence=st.lists(st.sampled_from(sorted(ALL_THROWS, key=lambda t: t.label)), max_size=45),
    rules=st.one_of(
        st.builds(
            x01.X01Config,
            st.sampled_from([25, 61, 301]),
            st.sampled_from(list(x01.Rule)),
            st.sampled_from(list(x01.Rule)),
        ),
        st.builds(cricket.CricketConfig, st.sampled_from(list(cricket.Variant))),
    ),
)
def test_random_leg_undo_at_every_position(
    sequence: list[Throw], rules: x01.X01Config | cricket.CricketConfig
) -> None:
    cfg = LegConfig(rules)
    recorded: tuple[Throw, ...] = ()
    prior = replay(cfg, TEAMS, recorded)
    for throw in sequence:
        if prior.winner is not None:
            break
        recorded += (throw,)
        current = replay(cfg, TEAMS, recorded)
        assert undo(cfg, TEAMS, current) == prior
        assert current.darts == recorded
        assert tuple(d.throw for v in current.visits for d in v.outcome.darts) == recorded
        assert len(current.visits[-1].outcome.darts) <= 3
        prior = current


@pytest.mark.parametrize("best_of", [1, 3, 5])
@pytest.mark.parametrize("winning_team", [0, 1])
def test_best_of_boundaries(best_of: int, winning_team: int) -> None:
    rules = x01.X01Config(2, x01.Rule.STRAIGHT, x01.Rule.DOUBLE)
    cfg = MatchConfig(rules, best_of, StartRule.FIXED)
    threshold = (best_of + 1) // 2
    # Alternate wins until the eventual loser is one below the threshold.
    winners = [winning_team, 1 - winning_team] * (threshold - 1) + [winning_team]
    histories: tuple[tuple[Throw, ...], ...] = ()
    for index, winner in enumerate(winners):
        histories += ((MISS,) * (3 * winner) + darts("D1"),)
        state = replay_match(cfg, TEAMS, histories)
        final = index == len(winners) - 1
        assert state.winner == (winning_team if final else None)
        assert (state.current_leg is None) is final
        assert state.legs_won[1 - winning_team] < threshold
    assert state.legs_won[winning_team] == threshold
    with pytest.raises(ValueError, match="after the match"):
        replay_match(cfg, TEAMS, histories + (darts("D1"),))
    # Undo the match-winning dart by replaying the shortened last leg.
    reopened = replay_match(cfg, TEAMS, histories[:-1] + (histories[-1][:-1],))
    assert reopened.winner is None
    assert reopened.legs_won[winning_team] == threshold - 1
    assert reopened.current_leg is not None


@pytest.mark.parametrize("rule", list(StartRule))
def test_match_start_rules(rule: StartRule) -> None:
    rules = x01.X01Config(2, x01.Rule.STRAIGHT, x01.Rule.DOUBLE)
    cfg = MatchConfig(rules, 5, rule, 1)
    initial = replay_match(cfg, TEAMS, ())
    assert initial.current_leg is not None
    first = 0 if rule is StartRule.ALTERNATE else 1
    assert initial.current_leg.next_thrower == thrower_for(TEAMS, first, 0)
    after = replay_match(cfg, TEAMS, (darts("D1"),))
    expected = {
        StartRule.ALTERNATE: 1,
        StartRule.FIXED: 1,
        StartRule.WINNER_STARTS: 1,
        StartRule.LOSER_STARTS: 0,
    }[rule]
    assert after.current_leg is not None
    assert after.current_leg.next_thrower == thrower_for(TEAMS, expected, 0)


def test_three_way_alternate_and_loser_starts() -> None:
    assert [starting_team(3, i) for i in range(7)] == [0, 1, 2, 0, 1, 2, 0]
    assert starting_team(3, 1, StartRule.LOSER_STARTS, previous_winner=1) == 2
    assert starting_team(3, 1, StartRule.LOSER_STARTS, previous_winner=2) == 1
    assert starting_team(3, 1, StartRule.LOSER_STARTS, previous_winner=0, previous_starter=2) == 1
    assert starting_team(1, 1, StartRule.LOSER_STARTS, previous_winner=0) == 0


def test_three_way_match_alternates_each_leg_and_resets_members() -> None:
    teams = (*TEAMS, Team(("C",)))
    rules = x01.X01Config(2, x01.Rule.STRAIGHT, x01.Rule.DOUBLE)
    cfg = MatchConfig(rules, 3)
    histories = (darts("D1"),) * 3
    state = replay_match(cfg, teams, histories)
    assert state.legs_won == (1, 1, 1)
    assert state.winner is None
    assert [leg.visits[0].thrower.member for leg in state.legs] == ["A1", "B1", "C"]
    assert state.current_leg is not None
    assert state.current_leg.next_thrower == Thrower(0, 0, "A1")
    assert replay_match(cfg, teams, histories + (darts("D1"),)).winner == 0


@pytest.mark.parametrize(
    ("rule", "expected"), [(StartRule.WINNER_STARTS, 1), (StartRule.LOSER_STARTS, 0)]
)
def test_start_rule_uses_winner_even_when_not_previous_starter(
    rule: StartRule, expected: int
) -> None:
    rules = x01.X01Config(2, x01.Rule.STRAIGHT, x01.Rule.DOUBLE)
    state = replay_match(MatchConfig(rules, 3, rule), TEAMS, (darts("MISS MISS MISS D1"),))
    assert state.legs_won == (0, 1)
    assert state.current_leg is not None
    assert state.current_leg.next_thrower == thrower_for(TEAMS, expected, 0)


def test_invalid_inputs_and_unfinished_legs() -> None:
    with pytest.raises(ValueError):
        Team(())
    for count in [0, -1, 2]:
        with pytest.raises(ValueError):
            MatchConfig(X01, count)
    for teams, starter in [((), 0), (TEAMS, -1), (TEAMS, 2)]:
        with pytest.raises(ValueError):
            replay(LegConfig(X01, starter), teams, ())
    for index, count in [(-1, 0), (2, 0), (0, -1)]:
        with pytest.raises(ValueError):
            thrower_for(TEAMS, index, count)
    for n, leg in [(0, 0), (2, -1)]:
        with pytest.raises(ValueError):
            starting_team(n, leg)
    with pytest.raises(ValueError):
        starting_team(2, 0, fixed_team=2)
    with pytest.raises(ValueError):
        starting_team(2, 1, previous_winner=2)
    with pytest.raises(ValueError):
        starting_team(2, 1, StartRule.WINNER_STARTS)
    with pytest.raises(ValueError, match="unfinished"):
        replay_match(MatchConfig(X01, 3), TEAMS, ((MISS,), ()))


def test_replay_results_are_frozen_and_hashable() -> None:
    state = replay(LegConfig(X01), TEAMS, (MISS,))
    assert hash(state) == hash(replay(LegConfig(X01), TEAMS, (MISS,)))
    with pytest.raises(FrozenInstanceError):
        state.darts_left = 0
