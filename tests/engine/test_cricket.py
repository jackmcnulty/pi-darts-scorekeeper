"""Tests for the standard cricket engine.

Two rules get their own class, because they are the two that implementations
get wrong: surplus on a dead target scores nothing, and closing every target
while behind on points is not a win.
"""

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from darts.engine.cricket import (
    MARKS_TO_CLOSE,
    TARGETS,
    CricketConfig,
    CricketTeamState,
    apply_dart,
    apply_visit,
    initial_state,
)
from darts.engine.throws import ALL_THROWS, BULL, DOUBLE, SINGLE, TRIPLE, Throw

CFG = CricketConfig()

MISS_THROW = Throw(0, 0)
OUTER_BULL = Throw(BULL, SINGLE)
INNER_BULL = Throw(BULL, DOUBLE)

#: Wedges that are legal to throw at but score nothing in cricket: 1 through 14.
NON_TARGETS = tuple(sorted(set(range(1, 21)) - set(TARGETS)))

ALL_THROWS_SORTED = sorted(ALL_THROWS, key=lambda t: (t.segment, t.multiplier))

ALL_SEVEN = dict.fromkeys(TARGETS, MARKS_TO_CLOSE)


def team(marks: dict[int, int] | None = None, points: int = 0) -> CricketTeamState:
    """A team state written the readable way round: only the targets that matter."""
    counts = marks or {}
    return CricketTeamState(marks=tuple(counts.get(t, 0) for t in TARGETS), points=points)


#: An opponent with nothing closed, so every target is live.
WIDE_OPEN = (team(),)

#: An opponent who has closed all seven, so every target is dead.
ALL_CLOSED = (team(ALL_SEVEN),)


# --- config and state ------------------------------------------------------


def test_targets_are_the_seven() -> None:
    assert TARGETS == (20, 19, 18, 17, 16, 15, 25)
    assert len(TARGETS) == 7


def test_non_targets_are_the_other_fourteen_wedges() -> None:
    assert NON_TARGETS == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)


def test_state_is_frozen_and_hashable() -> None:
    """Unlike a Mapping field, a tuple keeps the state usable as a dict key."""
    assert hash(team({20: 2}, points=40)) == hash(team({20: 2}, points=40))
    assert len({team(), team()}) == 1
    with pytest.raises(AttributeError):
        team().points = 5  # type: ignore[misc]


@pytest.mark.parametrize("marks", [(0,) * 6, (0,) * 8, ()])
def test_state_rejects_wrong_length(marks: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        CricketTeamState(marks=marks, points=0)


@pytest.mark.parametrize("bad", [-1, 4, 99])
def test_state_rejects_impossible_mark_counts(bad: int) -> None:
    with pytest.raises(ValueError):
        CricketTeamState(marks=(bad,) + (0,) * 6, points=0)


def test_state_rejects_negative_points() -> None:
    with pytest.raises(ValueError):
        CricketTeamState(marks=(0,) * 7, points=-1)


def test_marks_by_target_is_a_detached_view() -> None:
    state = team({20: 3, 25: 1})
    assert state.marks_by_target == {20: 3, 19: 0, 18: 0, 17: 0, 16: 0, 15: 0, 25: 1}

    view = dict(state.marks_by_target)
    view[20] = 0
    assert state.marks_on(20) == 3


def test_marks_on_rejects_non_targets() -> None:
    with pytest.raises(ValueError):
        team().marks_on(12)


def test_initial_state_is_empty() -> None:
    initial = initial_state(CFG)
    assert initial.marks == (0,) * 7
    assert initial.points == 0
    assert not initial.has_closed_all
    assert all(not initial.has_closed(t) for t in TARGETS)


# --- mark and surplus accounting -------------------------------------------


@pytest.mark.parametrize(
    ("prior", "multiplier", "counted", "surplus", "points"),
    [
        (0, SINGLE, 1, 0, 0),
        (0, DOUBLE, 2, 0, 0),
        (0, TRIPLE, 3, 0, 0),
        (1, SINGLE, 1, 0, 0),
        (1, DOUBLE, 2, 0, 0),
        (1, TRIPLE, 2, 1, 20),
        (2, SINGLE, 1, 0, 0),
        (2, DOUBLE, 1, 1, 20),
        (2, TRIPLE, 1, 2, 40),
        # Already closed, so every mark is surplus.
        (3, SINGLE, 0, 1, 20),
        (3, DOUBLE, 0, 2, 40),
        (3, TRIPLE, 0, 3, 60),
    ],
)
def test_mark_and_surplus_table(
    prior: int, multiplier: int, counted: int, surplus: int, points: int
) -> None:
    """The whole accounting rule on 20, against an opponent who has 20 open."""
    outcome = apply_dart(team({20: prior}), Throw(20, multiplier), CFG, WIDE_OPEN)

    assert outcome.target == 20
    assert outcome.counted_marks == counted
    assert outcome.surplus_marks == surplus
    assert outcome.points == points
    assert outcome.state.marks_on(20) == prior + counted
    assert outcome.state.points == points
    assert not outcome.wasted


def test_triple_twenty_on_no_marks_closes_exactly_with_no_points() -> None:
    """Acceptance criterion 1."""
    outcome = apply_dart(team(), Throw(20, TRIPLE), CFG, WIDE_OPEN)

    assert outcome.counted_marks == 3
    assert outcome.surplus_marks == 0
    assert outcome.points == 0
    assert outcome.state.has_closed(20)
    assert outcome.state.points == 0


def test_triple_twenty_on_two_marks_closes_and_scores_forty() -> None:
    """Acceptance criterion 2: 1 counted mark + 2 surplus x 20."""
    outcome = apply_dart(team({20: 2}), Throw(20, TRIPLE), CFG, WIDE_OPEN)

    assert (outcome.counted_marks, outcome.surplus_marks) == (1, 2)
    assert outcome.points == 40
    assert outcome.state.marks_on(20) == 3


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("prior", [0, 1, 2, 3])
def test_marks_never_exceed_three(target: int, prior: int) -> None:
    """Acceptance criterion 7, on every target."""
    multiplier = DOUBLE if target == BULL else TRIPLE
    outcome = apply_dart(team({target: prior}), Throw(target, multiplier), CFG, WIDE_OPEN)

    assert outcome.state.marks_on(target) <= MARKS_TO_CLOSE
    assert outcome.counted_marks + outcome.surplus_marks == multiplier


def test_other_targets_are_untouched() -> None:
    outcome = apply_dart(team({19: 2}), Throw(20, TRIPLE), CFG, WIDE_OPEN)

    assert outcome.state.marks_by_target == {20: 3, 19: 2, 18: 0, 17: 0, 16: 0, 15: 0, 25: 0}


@pytest.mark.parametrize("target", TARGETS)
def test_a_split_dart_scores_for_the_surplus_only(target: int) -> None:
    """A dart split between closing and surplus scores marks x target for the
    surplus part alone — never its face value, which is what an x01-shaped
    implementation would hand out."""
    multiplier = DOUBLE if target == BULL else TRIPLE
    throw = Throw(target, multiplier)
    outcome = apply_dart(team({target: MARKS_TO_CLOSE - 1}), throw, CFG, WIDE_OPEN)

    assert outcome.counted_marks == 1
    assert outcome.surplus_marks == multiplier - 1
    assert outcome.points == target * (multiplier - 1)
    assert outcome.points < throw.score


# --- the bulls -------------------------------------------------------------


def test_outer_bull_is_one_mark() -> None:
    """Acceptance criterion 4, first half."""
    outcome = apply_dart(team(), OUTER_BULL, CFG, WIDE_OPEN)

    assert outcome.counted_marks == 1
    assert outcome.state.marks_on(BULL) == 1
    assert outcome.points == 0


def test_inner_bull_is_two_marks() -> None:
    """Acceptance criterion 4, second half."""
    outcome = apply_dart(team(), INNER_BULL, CFG, WIDE_OPEN)

    assert outcome.counted_marks == 2
    assert outcome.state.marks_on(BULL) == 2


def test_inner_bull_at_two_marks_closes_and_scores_twenty_five() -> None:
    """Acceptance criterion 4, third part: 1 counted mark + 1 surplus x 25."""
    outcome = apply_dart(team({BULL: 2}), INNER_BULL, CFG, WIDE_OPEN)

    assert (outcome.counted_marks, outcome.surplus_marks) == (1, 1)
    assert outcome.points == 25
    assert outcome.state.has_closed(BULL)


def test_three_outer_bulls_close_the_bull() -> None:
    state = team()
    for _ in range(3):
        state = apply_dart(state, OUTER_BULL, CFG, WIDE_OPEN).state

    assert state.has_closed(BULL)
    assert state.points == 0


# --- non-target darts ------------------------------------------------------


@pytest.mark.parametrize("segment", NON_TARGETS)
@pytest.mark.parametrize("multiplier", [SINGLE, DOUBLE, TRIPLE])
def test_non_target_dart_changes_nothing(segment: int, multiplier: int) -> None:
    """Acceptance criterion 6. A 12 is a legal dart that simply does not count."""
    before = team({20: 2, 19: 3}, points=57)
    outcome = apply_dart(before, Throw(segment, multiplier), CFG, WIDE_OPEN)

    assert outcome.state == before
    assert outcome.target is None
    assert (outcome.counted_marks, outcome.surplus_marks, outcome.points) == (0, 0, 0)
    assert not outcome.wasted
    assert not outcome.win


def test_miss_changes_nothing() -> None:
    before = team({20: 2}, points=40)
    outcome = apply_dart(before, MISS_THROW, CFG, WIDE_OPEN)

    assert outcome.state == before
    assert outcome.target is None


# --- dead targets ----------------------------------------------------------


class TestDeadTarget:
    """Surplus only scores while an opponent still has the target open.

    Easy to get wrong in two directions: scoring against a target everyone has
    closed, and refusing to score merely because the *throwing* team has closed
    it — which is the only way surplus can exist at all.
    """

    def test_surplus_is_wasted_once_every_opponent_has_closed(self) -> None:
        """Acceptance criterion 3."""
        outcome = apply_dart(team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), CFG, ALL_CLOSED)

        assert outcome.surplus_marks == 3
        assert outcome.points == 0
        assert outcome.wasted
        assert outcome.state.points == 0

    def test_one_opponent_still_open_keeps_the_target_live(self) -> None:
        opponents = (team(ALL_SEVEN), team({20: 2}))
        outcome = apply_dart(team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), CFG, opponents)

        assert outcome.points == 60
        assert not outcome.wasted

    def test_the_throwing_team_closing_it_does_not_kill_it(self) -> None:
        """The throwing team has closed 20 — that is the precondition for
        surplus, not a reason to withhold the points."""
        outcome = apply_dart(team({20: MARKS_TO_CLOSE}), Throw(20, SINGLE), CFG, WIDE_OPEN)

        assert outcome.points == 20
        assert not outcome.wasted

    def test_a_dead_target_still_accepts_counted_marks(self) -> None:
        """Closing a dead target scores nothing but must still close it, or the
        team could never satisfy the all-seven half of the win condition."""
        outcome = apply_dart(team({20: 2}), Throw(20, TRIPLE), CFG, ALL_CLOSED)

        assert outcome.counted_marks == 1
        assert outcome.state.has_closed(20)
        assert outcome.points == 0
        assert outcome.wasted

    def test_no_surplus_is_not_wasted(self) -> None:
        """`wasted` flags points that were lost, not darts that never had any."""
        outcome = apply_dart(team(), Throw(20, TRIPLE), CFG, ALL_CLOSED)

        assert outcome.surplus_marks == 0
        assert not outcome.wasted

    def test_targets_die_independently(self) -> None:
        opponents = (team({20: MARKS_TO_CLOSE}),)
        closed_both = team({20: MARKS_TO_CLOSE, 19: MARKS_TO_CLOSE})

        assert apply_dart(closed_both, Throw(20, SINGLE), CFG, opponents).points == 0
        assert apply_dart(closed_both, Throw(19, SINGLE), CFG, opponents).points == 19

    def test_with_no_opponents_every_target_is_dead(self) -> None:
        """Vacuously: no opponent has it open, so nothing scores. Documented
        behaviour of the default argument, not an accident."""
        outcome = apply_dart(team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), CFG)

        assert outcome.points == 0
        assert outcome.wasted


# --- closing is not winning ------------------------------------------------


class TestClosingIsNotWinning:
    """All seven closed is only half the win condition; the other half is points."""

    def test_closed_everything_but_trailing_does_not_win(self) -> None:
        """Acceptance criterion 5: the dart that closes the seventh target
        while 60 points behind does not end the leg."""
        one_to_go = team({**ALL_SEVEN, 15: 2}, points=40)
        opponents = (team({20: 2}, points=100),)

        outcome = apply_dart(one_to_go, Throw(15, SINGLE), CFG, opponents)

        assert outcome.counted_marks == 1
        assert outcome.state.has_closed_all
        assert not outcome.win

    def test_catching_up_on_points_wins(self) -> None:
        """The same team, one live target later, draws level and wins on `>=`."""
        behind = team(ALL_SEVEN, points=40)
        opponents = (team({20: 2}, points=100),)

        outcome = apply_dart(behind, Throw(20, TRIPLE), CFG, opponents)

        assert outcome.points == 60
        assert outcome.state.points == 100
        assert outcome.win, "level on points is a win; the rule is >=, not >"

    def test_ahead_on_points_without_closing_does_not_win(self) -> None:
        ahead = team({**ALL_SEVEN, 25: 2}, points=500)
        outcome = apply_dart(ahead, Throw(20, SINGLE), CFG, WIDE_OPEN)

        assert not outcome.state.has_closed_all
        assert not outcome.win

    def test_the_closing_dart_itself_wins(self) -> None:
        one_to_go = team({**ALL_SEVEN, 25: 2}, points=100)
        outcome = apply_dart(one_to_go, OUTER_BULL, CFG, (team(points=20),))

        assert outcome.counted_marks == 1
        assert outcome.state.has_closed_all
        assert outcome.win

    def test_must_be_level_with_every_opponent_not_just_one(self) -> None:
        state = team(ALL_SEVEN, points=100)
        opponents = (team(points=20), team(points=180))

        assert not apply_dart(state, MISS_THROW, CFG, opponents).win

    def test_a_won_leg_rejects_further_darts(self) -> None:
        won = team(ALL_SEVEN, points=100)

        with pytest.raises(ValueError, match="already won"):
            apply_dart(won, Throw(20, TRIPLE), CFG, (team(points=20),))


# --- visits ----------------------------------------------------------------


def test_visit_applies_darts_in_order() -> None:
    outcome = apply_visit(
        team(), (Throw(20, TRIPLE), Throw(20, TRIPLE), Throw(19, TRIPLE)), CFG, WIDE_OPEN
    )

    assert len(outcome.darts) == 3
    assert outcome.state.marks_by_target == {20: 3, 19: 3, 18: 0, 17: 0, 16: 0, 15: 0, 25: 0}
    assert outcome.points_before == 0
    assert outcome.points_after == 60
    assert not outcome.win


def test_visit_stops_at_a_win() -> None:
    """A win ends the visit, the way a checkout does in x01."""
    one_to_go = team({**ALL_SEVEN, 25: 2}, points=100)
    outcome = apply_visit(
        one_to_go, (OUTER_BULL, Throw(20, TRIPLE), Throw(19, TRIPLE)), CFG, (team(points=20),)
    )

    assert outcome.win
    assert len(outcome.darts) == 1, "the two darts after the win were never thrown"
    assert outcome.state.points == 100


def test_visit_of_no_darts_is_a_no_op() -> None:
    before = team({20: 2}, points=40)
    outcome = apply_visit(before, (), CFG, WIDE_OPEN)

    assert outcome.state == before
    assert outcome.darts == ()
    assert not outcome.win
    assert outcome.points_before == outcome.points_after == 40


def test_visit_accepts_more_than_three_darts() -> None:
    """Three-to-a-visit is turn structure, which belongs to #10."""
    outcome = apply_visit(team(), (Throw(20, SINGLE),) * 5, CFG, WIDE_OPEN)

    assert len(outcome.darts) == 5
    assert outcome.state.marks_on(20) == 3
    assert outcome.state.points == 40


def test_a_visit_never_busts_however_much_it_scores() -> None:
    """Cricket has no bust, so a visit is never voided."""
    outcome = apply_visit(
        team(ALL_SEVEN),
        (Throw(20, TRIPLE),) * 3,
        CFG,
        (team({20: 2}, points=10_000),),
    )

    assert outcome.points_before == 0
    assert outcome.points_after == 180


# --- a full two-player leg -------------------------------------------------


#: A complete standard leg, alternating visits, written as throw labels.
#:
#: Built to walk every rule in one sequence: closing, surplus scoring, a dead
#: target, a wasted dart, a non-target dart, both bulls, a team closing all
#: seven while behind (which does *not* win), and finally the win.
LEG: tuple[tuple[str, tuple[str, str, str]], ...] = (
    ("A", ("T20", "20", "MISS")),
    ("B", ("T19", "T19", "T19")),
    ("A", ("T19", "T18", "T17")),
    ("B", ("T19", "T18", "12")),
    ("A", ("T16", "T15", "BULL")),
    ("B", ("MISS", "MISS", "MISS")),
    ("A", ("25", "T20", "T20")),
)

#: The throwing team's marks and points at the end of each visit above.
EXPECTED_AFTER_VISIT: tuple[tuple[dict[int, int], int], ...] = (
    ({20: 3}, 20),
    ({19: 3}, 114),
    ({20: 3, 19: 3, 18: 3, 17: 3}, 20),
    ({19: 3, 18: 3}, 114),
    ({20: 3, 19: 3, 18: 3, 17: 3, 16: 3, 15: 3, 25: 2}, 20),
    ({19: 3, 18: 3}, 114),
    ({20: 3, 19: 3, 18: 3, 17: 3, 16: 3, 15: 3, 25: 3}, 140),
)


def test_full_two_player_leg() -> None:
    states = {"A": initial_state(CFG), "B": initial_state(CFG)}
    winner: str | None = None

    for (who, labels), (expected_marks, expected_points) in zip(
        LEG, EXPECTED_AFTER_VISIT, strict=True
    ):
        other = "B" if who == "A" else "A"
        throws = tuple(Throw.parse(label) for label in labels)

        outcome = apply_visit(states[who], throws, CFG, (states[other],))
        states[who] = outcome.state

        assert states[who] == team(expected_marks, expected_points), f"after {who}: {labels}"
        if outcome.win:
            winner = who

    assert winner == "A"
    assert states["A"].points == 140
    assert states["B"].points == 114
    assert states["A"].has_closed_all
    assert not states["B"].has_closed_all


def test_full_leg_dead_target_and_non_target_darts() -> None:
    """B's fourth visit, which the end state alone cannot show: it opens on a
    19 that both teams have closed, and closes with a dart at a 12."""
    a_after_v2 = team({20: 3, 19: 3, 18: 3, 17: 3}, points=20)
    b_after_v1 = team({19: 3}, points=114)

    visit = apply_visit(
        b_after_v1,
        tuple(Throw.parse(label) for label in ("T19", "T18", "12")),
        CFG,
        (a_after_v2,),
    )
    wasted_dart, closing_dart, non_target = visit.darts

    assert wasted_dart.surplus_marks == 3
    assert wasted_dart.points == 0
    assert wasted_dart.wasted

    assert closing_dart.counted_marks == 3
    assert closing_dart.points == 0

    assert non_target.target is None
    assert non_target.state == closing_dart.state

    assert visit.points_after == 114, "the whole visit scored nothing"


def test_full_leg_closing_all_seven_while_behind_does_not_win() -> None:
    """A's last visit: the outer bull closes her seventh target on 20 points
    against B's 114, and the leg carries on for two more darts."""
    a_before = team({20: 3, 19: 3, 18: 3, 17: 3, 16: 3, 15: 3, 25: 2}, points=20)
    b = team({19: 3, 18: 3}, points=114)

    visit = apply_visit(a_before, (OUTER_BULL, Throw(20, TRIPLE), Throw(20, TRIPLE)), CFG, (b,))
    closes, catches_up, wins = visit.darts

    assert closes.state.has_closed_all
    assert not closes.win, "closed all seven, but 20 points against 114"

    assert catches_up.points == 60
    assert catches_up.state.points == 80
    assert not catches_up.win, "still behind 114"

    assert wins.points == 60
    assert wins.state.points == 140
    assert wins.win
    assert visit.win


# --- properties ------------------------------------------------------------


throws = st.sampled_from(ALL_THROWS_SORTED)
teams = st.builds(
    CricketTeamState,
    marks=st.tuples(*[st.integers(min_value=0, max_value=MARKS_TO_CLOSE)] * len(TARGETS)),
    points=st.integers(min_value=0, max_value=500),
)


def _throwable(state: CricketTeamState, opponent: CricketTeamState) -> bool:
    """Whether the leg is still live, so `apply_dart` will not reject the dart."""
    return not (state.has_closed_all and state.points >= opponent.points)


@given(state=teams, throw=throws, opponent=teams)
def test_marks_stay_in_range_for_any_dart(
    state: CricketTeamState, throw: Throw, opponent: CricketTeamState
) -> None:
    assume(_throwable(state, opponent))
    outcome = apply_dart(state, throw, CFG, (opponent,))

    assert len(outcome.state.marks) == len(TARGETS)
    assert all(0 <= m <= MARKS_TO_CLOSE for m in outcome.state.marks)


@given(state=teams, throw=throws, opponent=teams)
def test_marks_and_points_never_go_backwards(
    state: CricketTeamState, throw: Throw, opponent: CricketTeamState
) -> None:
    assume(_throwable(state, opponent))
    outcome = apply_dart(state, throw, CFG, (opponent,))

    assert outcome.state.points >= state.points
    assert all(
        after >= before for before, after in zip(state.marks, outcome.state.marks, strict=True)
    )


@given(state=teams, throw=throws, opponent=teams)
def test_marks_account_for_every_pip_of_the_multiplier(
    state: CricketTeamState, throw: Throw, opponent: CricketTeamState
) -> None:
    """A dart's marks are split between counted and surplus and nowhere else."""
    assume(_throwable(state, opponent))
    outcome = apply_dart(state, throw, CFG, (opponent,))

    if outcome.target is None:
        assert (outcome.counted_marks, outcome.surplus_marks, outcome.points) == (0, 0, 0)
        assert outcome.state == state
    else:
        assert outcome.counted_marks + outcome.surplus_marks == throw.multiplier
        assert outcome.points in (0, outcome.target * outcome.surplus_marks)


@given(state=teams, throw=throws, opponent=teams)
def test_points_only_ever_come_from_unwasted_surplus(
    state: CricketTeamState, throw: Throw, opponent: CricketTeamState
) -> None:
    assume(_throwable(state, opponent))
    outcome = apply_dart(state, throw, CFG, (opponent,))

    if outcome.points:
        assert outcome.surplus_marks > 0
        assert not outcome.wasted
    if outcome.wasted:
        assert outcome.points == 0


@settings(max_examples=200)
@given(sequence=st.lists(throws, max_size=9), opponent=teams)
def test_visit_is_a_fold_of_apply_dart(sequence: list[Throw], opponent: CricketTeamState) -> None:
    """`apply_visit` is `apply_dart` repeated, up to the point it stops at a win."""
    visit = apply_visit(initial_state(CFG), tuple(sequence), CFG, (opponent,))

    state = initial_state(CFG)
    for throw in sequence[: len(visit.darts)]:
        state = apply_dart(state, throw, CFG, (opponent,)).state

    assert visit.state == state
    assert visit.points_before == 0
    assert visit.points_after == state.points
