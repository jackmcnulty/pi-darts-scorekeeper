"""Tests for the cut-throat and quick cricket variants.

`test_cricket.py` is the spec for standard cricket and for the mark accounting
all three variants share; this file covers only what #9 adds. The two rules
that carry the risk here are cut-throat paying every open opponent *in full*
rather than splitting between them, and cut-throat's win condition running the
opposite way round to standard's — so a team can win by giving points away.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from darts.engine.cricket import (
    MARKS_TO_CLOSE,
    TARGETS,
    CricketConfig,
    CricketTeamState,
    DartOutcome,
    PointEvent,
    Variant,
    apply_dart,
    apply_visit,
    initial_state,
)
from darts.engine.throws import ALL_THROWS, BULL, SINGLE, TRIPLE, Throw

STANDARD = CricketConfig(variant=Variant.STANDARD)
CUTTHROAT = CricketConfig(variant=Variant.CUTTHROAT)
QUICK = CricketConfig(variant=Variant.QUICK)

MISS_THROW = Throw(0, 0)
OUTER_BULL = Throw(BULL, SINGLE)

ALL_SEVEN = dict.fromkeys(TARGETS, MARKS_TO_CLOSE)

ALL_THROWS_SORTED = sorted(ALL_THROWS, key=lambda t: (t.segment, t.multiplier))


def team(marks: dict[int, int] | None = None, points: int = 0) -> CricketTeamState:
    counts = marks or {}
    return CricketTeamState(marks=tuple(counts.get(t, 0) for t in TARGETS), points=points)


def has_won(
    cfg: CricketConfig, state: CricketTeamState, opponents: tuple[CricketTeamState, ...]
) -> bool:
    """Whether the standing position is already a win, probed through the public API.

    A miss changes nothing, so the `win` it reports is the position as it
    stands; and `apply_dart` refuses a dart at a leg that is already over,
    which is itself the answer.
    """
    try:
        return apply_dart(state, MISS_THROW, cfg, opponents).win
    except ValueError:
        return True


# --- the variant setting ---------------------------------------------------


def test_config_defaults_to_standard() -> None:
    """#8's `CricketConfig()` has to keep meaning what it meant."""
    assert CricketConfig().variant is Variant.STANDARD


def test_variants_are_the_three() -> None:
    assert [v.value for v in Variant] == ["standard", "cutthroat", "quick"]


# --- point events ----------------------------------------------------------


def test_point_event_rejects_a_worthless_award() -> None:
    with pytest.raises(ValueError):
        PointEvent(recipient=None, points=0)


def test_point_event_rejects_a_negative_recipient() -> None:
    with pytest.raises(ValueError):
        PointEvent(recipient=-1, points=20)


def test_standard_emits_one_event_to_the_thrower() -> None:
    outcome = apply_dart(team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), STANDARD, (team(),))

    assert outcome.point_events == (PointEvent(recipient=None, points=60),)
    assert outcome.points == 60
    assert outcome.points_conceded == 0
    assert outcome.opponents == (team(),), "standard never moves an opponent's points"


def test_a_dead_target_emits_no_events_under_standard() -> None:
    outcome = apply_dart(
        team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), STANDARD, (team(ALL_SEVEN),)
    )

    assert outcome.point_events == ()
    assert outcome.wasted


# --- cut-throat scoring ----------------------------------------------------


class TestCutThroatScoring:
    """Surplus is given away, in full, to every opponent that is still open."""

    def test_pays_each_open_opponent_the_full_amount(self) -> None:
        """Acceptance criterion 1: two events of 60, not 30 each."""
        outcome = apply_dart(
            team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), CUTTHROAT, (team(), team())
        )

        assert outcome.point_events == (
            PointEvent(recipient=0, points=60),
            PointEvent(recipient=1, points=60),
        )
        assert outcome.points_conceded == 120
        assert [o.points for o in outcome.opponents] == [60, 60]

    def test_pays_the_thrower_nothing(self) -> None:
        outcome = apply_dart(
            team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), CUTTHROAT, (team(), team())
        )

        assert outcome.points == 0
        assert outcome.state.points == 0
        assert not outcome.wasted, "the marks paid somebody, just not the thrower"

    def test_skips_an_opponent_that_has_already_closed(self) -> None:
        """Acceptance criterion 2: one event, to the open opponent only."""
        opponents = (team({20: MARKS_TO_CLOSE}), team({20: 2}))
        outcome = apply_dart(team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), CUTTHROAT, opponents)

        assert outcome.point_events == (PointEvent(recipient=1, points=60),)
        assert outcome.opponents[0].points == 0, "closed, so it receives nothing"
        assert outcome.opponents[1].points == 60

    def test_every_opponent_closed_wastes_the_surplus(self) -> None:
        outcome = apply_dart(
            team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), CUTTHROAT, (team(ALL_SEVEN),)
        )

        assert outcome.point_events == ()
        assert outcome.wasted
        assert outcome.opponents[0].points == 0

    def test_counted_marks_pay_nobody(self) -> None:
        """Only surplus travels. Closing a target costs the opponents nothing."""
        outcome = apply_dart(team(), Throw(20, TRIPLE), CUTTHROAT, (team(), team()))

        assert outcome.counted_marks == 3
        assert outcome.surplus_marks == 0
        assert outcome.point_events == ()
        assert [o.points for o in outcome.opponents] == [0, 0]

    def test_a_split_dart_pays_only_for_the_surplus(self) -> None:
        outcome = apply_dart(team({20: 2}), Throw(20, TRIPLE), CUTTHROAT, (team(),))

        assert (outcome.counted_marks, outcome.surplus_marks) == (1, 2)
        assert outcome.point_events == (PointEvent(recipient=0, points=40),)

    def test_the_bull_pays_twenty_five_a_mark(self) -> None:
        outcome = apply_dart(team({BULL: MARKS_TO_CLOSE}), OUTER_BULL, CUTTHROAT, (team(),))

        assert outcome.point_events == (PointEvent(recipient=0, points=25),)


# --- cut-throat winning ----------------------------------------------------


class TestCutThroatWinning:
    """Cut-throat runs the win condition backwards: fewest points, not most."""

    def test_closed_but_leading_on_points_does_not_win(self) -> None:
        """Acceptance criterion 3."""
        state = team(ALL_SEVEN, points=100)
        opponents = (team(points=40), team(points=60))

        assert not has_won(CUTTHROAT, state, opponents)
        assert has_won(STANDARD, state, opponents), "the same position wins under standard"

    def test_closed_and_trailing_on_points_wins(self) -> None:
        state = team(ALL_SEVEN, points=40)
        opponents = (team(points=100), team(points=60))

        assert has_won(CUTTHROAT, state, opponents)
        assert not has_won(STANDARD, state, opponents), "and loses under standard"

    def test_level_on_points_wins(self) -> None:
        assert has_won(CUTTHROAT, team(ALL_SEVEN, points=100), (team(points=100),))

    def test_must_be_under_every_opponent_not_just_one(self) -> None:
        state = team(ALL_SEVEN, points=50)
        assert not has_won(CUTTHROAT, state, (team(points=90), team(points=10)))

    def test_closing_is_still_required(self) -> None:
        lowest = team({**ALL_SEVEN, 25: 2}, points=0)
        assert not has_won(CUTTHROAT, lowest, (team(points=500),))

    def test_a_team_can_win_by_giving_points_away(self) -> None:
        """The dart that hands 60 points to the one open opponent lifts the
        minimum above the thrower's own total, which is the win."""
        thrower = team(ALL_SEVEN, points=50)
        opponents = (team({20: 2}, points=40), team(ALL_SEVEN, points=60))

        assert not has_won(CUTTHROAT, thrower, opponents), "50 is above the minimum of 40"

        outcome = apply_dart(thrower, Throw(20, TRIPLE), CUTTHROAT, opponents)

        assert outcome.point_events == (PointEvent(recipient=0, points=60),)
        assert [o.points for o in outcome.opponents] == [100, 60]
        assert outcome.state.points == 50, "the thrower gained nothing"
        assert outcome.win, "but the minimum is now 60, so 50 is under it"


def test_cutthroat_visit_threads_opponent_points_between_darts() -> None:
    """Each dart must see the totals the previous dart created.

    Two single 20s pay the open opponent 20 apiece. Only after the second does
    the minimum reach the thrower's 50, so the win lands on dart two and dart
    three is never thrown. Against the *original* totals no dart would win at
    all, which is what makes this a test of the threading rather than of the
    win condition.
    """
    thrower = team(ALL_SEVEN, points=50)
    opponents = (team({20: 2}, points=10), team(ALL_SEVEN, points=60))

    visit = apply_visit(thrower, (Throw(20, SINGLE),) * 3, CUTTHROAT, opponents)

    assert [o.points for o in visit.opponents] == [50, 60]
    assert visit.point_events == (
        PointEvent(recipient=0, points=20),
        PointEvent(recipient=0, points=20),
    )
    assert visit.win
    assert len(visit.darts) == 2, "the third dart was never thrown"
    assert visit.darts[0].win is False, "10 -> 30 is not yet above 50"
    assert visit.darts[1].win is True, "30 -> 50 is level, and level wins"


# --- quick -----------------------------------------------------------------


class TestQuick:
    """Marks as usual, points never."""

    def test_records_marks_but_scores_nothing(self) -> None:
        """Acceptance criterion 4."""
        outcome = apply_dart(team({20: 2}), Throw(20, TRIPLE), QUICK, (team(),))

        assert (outcome.counted_marks, outcome.surplus_marks) == (1, 2)
        assert outcome.state.marks_on(20) == MARKS_TO_CLOSE
        assert outcome.points == 0
        assert outcome.points_conceded == 0
        assert outcome.point_events == ()
        assert outcome.state.points == 0

    def test_surplus_is_always_wasted_even_against_an_open_opponent(self) -> None:
        """The one place `wasted` is not about a dead target."""
        outcome = apply_dart(team({20: MARKS_TO_CLOSE}), Throw(20, TRIPLE), QUICK, (team(),))

        assert outcome.surplus_marks == 3
        assert outcome.wasted
        assert outcome.opponents[0].points == 0

    def test_wins_on_the_closing_dart_however_far_behind(self) -> None:
        one_to_go = team({**ALL_SEVEN, 25: 2}, points=0)
        outcome = apply_dart(one_to_go, OUTER_BULL, QUICK, (team(points=500),))

        assert outcome.state.has_closed_all
        assert outcome.win

    def test_wins_on_the_closing_dart_however_far_ahead(self) -> None:
        one_to_go = team({**ALL_SEVEN, 25: 2}, points=500)
        assert apply_dart(one_to_go, OUTER_BULL, QUICK, (team(points=0),)).win

    def test_closing_is_still_required(self) -> None:
        assert not has_won(QUICK, team({**ALL_SEVEN, 25: 2}), (team(),))

    def test_a_visit_scores_nothing_however_much_it_hits(self) -> None:
        visit = apply_visit(team({20: 3}), (Throw(20, TRIPLE),) * 3, QUICK, (team(),))

        assert visit.points_before == visit.points_after == 0
        assert visit.point_events == ()


# --- the win-condition table -----------------------------------------------


@pytest.mark.parametrize(
    ("variant", "points", "opponent_points", "expected"),
    [
        # standard: closed out, and nobody above you.
        (Variant.STANDARD, 100, (40, 60), True),
        (Variant.STANDARD, 100, (100,), True),
        (Variant.STANDARD, 100, (40, 180), False),
        (Variant.STANDARD, 0, (0,), True),
        # cutthroat: closed out, and nobody below you.
        (Variant.CUTTHROAT, 40, (100, 60), True),
        (Variant.CUTTHROAT, 100, (100,), True),
        (Variant.CUTTHROAT, 100, (40, 60), False),
        (Variant.CUTTHROAT, 0, (0,), True),
        # quick: closed out is the whole of it.
        (Variant.QUICK, 0, (500,), True),
        (Variant.QUICK, 500, (0,), True),
    ],
)
def test_win_condition_table_when_closed_out(
    variant: Variant, points: int, opponent_points: tuple[int, ...], expected: bool
) -> None:
    cfg = CricketConfig(variant=variant)
    state = team({**ALL_SEVEN, 25: 2}, points=points)
    opponents = tuple(team(points=p) for p in opponent_points)

    assert apply_dart(state, OUTER_BULL, cfg, opponents).win is expected


@pytest.mark.parametrize("variant", list(Variant))
@pytest.mark.parametrize("points", [0, 100, 500])
def test_no_variant_wins_without_closing_every_target(variant: Variant, points: int) -> None:
    """The one clause all three share."""
    cfg = CricketConfig(variant=variant)
    state = team({**ALL_SEVEN, 25: 2}, points=points)

    assert not has_won(cfg, state, (team(points=100),))


# --- a three-team cut-throat leg -------------------------------------------


TEAMS = ("A", "B", "C")

#: A complete leg: early surplus pays both opponents, then only the open one.
#: A closes the remaining targets and wins with 57 against 120 and 117.
LEG: tuple[tuple[str, tuple[str, str, str]], ...] = (
    ("A", ("T20", "T20", "MISS")),
    ("B", ("T19", "T19", "MISS")),
    ("C", ("T20", "T20", "12")),
    ("A", ("T19", "T18", "T17")),
    ("B", ("MISS", "MISS", "MISS")),
    ("C", ("MISS", "MISS", "MISS")),
    ("A", ("T16", "T15", "BULL")),
    ("B", ("MISS", "MISS", "MISS")),
    ("C", ("MISS", "MISS", "MISS")),
    ("A", ("25", "MISS", "MISS")),
)

#: Every point event the leg produces, resolved to team names.
EXPECTED_EVENTS: tuple[tuple[str, int], ...] = (
    ("B", 60),  # A's second T20: 3 surplus x 20, to both open opponents...
    ("C", 60),  # ...in full, each.
    ("A", 57),  # B's second T19, likewise.
    ("C", 57),
    ("B", 60),  # C's second T20 — A has closed 20, so only B is paid.
)

EXPECTED_FINAL: dict[str, tuple[dict[int, int], int]] = {
    "A": (ALL_SEVEN, 57),
    "B": ({19: 3}, 120),
    "C": ({20: 3}, 117),
}


def test_three_team_cutthroat_leg() -> None:
    """Acceptance criterion 6."""
    states = {name: initial_state(CUTTHROAT) for name in TEAMS}
    events: list[tuple[str, int]] = []
    attributed: list[tuple[int, int, str, str, int]] = []

    for visit_index, (who, labels) in enumerate(LEG):
        opponent_names = tuple(name for name in TEAMS if name != who)
        opponents = tuple(states[name] for name in opponent_names)
        throws = tuple(Throw.parse(label) for label in labels)

        visit = apply_visit(states[who], throws, CUTTHROAT, opponents)

        states[who] = visit.state
        for name, updated in zip(opponent_names, visit.opponents, strict=True):
            states[name] = updated

        for event in visit.point_events:
            assert event.recipient is not None, "cut-throat never pays the thrower"
            events.append((opponent_names[event.recipient], event.points))

        for dart_index, dart in enumerate(visit.darts):
            for event in dart.point_events:
                assert event.recipient is not None
                attributed.append(
                    (visit_index, dart_index, who, opponent_names[event.recipient], event.points)
                )

        assert visit.win is (visit_index == len(LEG) - 1)

    assert len(visit.darts) == 1, "the leg ends on A's closing outer bull"
    assert attributed == [
        (0, 1, "A", "B", 60),
        (0, 1, "A", "C", 60),
        (1, 1, "B", "A", 57),
        (1, 1, "B", "C", 57),
        (2, 1, "C", "B", 60),
    ]

    assert tuple(events) == EXPECTED_EVENTS

    for name, (marks, points) in EXPECTED_FINAL.items():
        assert states[name] == team(marks, points), name

    assert sum(points for _, points in events) == sum(s.points for s in states.values())


# --- the shared accounting path --------------------------------------------


MARK_SEQUENCE = (
    Throw(20, TRIPLE),
    Throw(20, TRIPLE),
    Throw(19, SINGLE),
    Throw(BULL, 2),
    Throw(12, TRIPLE),
    Throw(19, TRIPLE),
    Throw(0, 0),
)


def test_quick_and_standard_agree_on_marks() -> None:
    """Acceptance criterion 5: one accounting path, three variants."""
    opponents = (team({19: 2}), team())

    by_variant = {
        variant: apply_visit(
            initial_state(CricketConfig(variant=variant)),
            MARK_SEQUENCE,
            CricketConfig(variant=variant),
            opponents,
        ).state.marks
        for variant in Variant
    }

    assert by_variant[Variant.QUICK] == by_variant[Variant.STANDARD]
    assert by_variant[Variant.CUTTHROAT] == by_variant[Variant.STANDARD]
    assert by_variant[Variant.STANDARD] == (3, 3, 0, 0, 0, 0, 2)


# --- properties ------------------------------------------------------------


throws = st.sampled_from(ALL_THROWS_SORTED)
teams = st.builds(
    CricketTeamState,
    marks=st.tuples(*[st.integers(min_value=0, max_value=MARKS_TO_CLOSE)] * len(TARGETS)),
    points=st.integers(min_value=0, max_value=500),
)
variants = st.sampled_from(list(Variant))


@settings(max_examples=300)
@given(sequence=st.lists(throws, max_size=9), opponents=st.lists(teams, max_size=3))
def test_every_variant_agrees_on_marks(
    sequence: list[Throw], opponents: list[CricketTeamState]
) -> None:
    """The stronger form of criterion 5, over arbitrary sequences.

    It holds even though the variants stop the visit at different darts:
    winning requires every target closed, so by the time any variant stops,
    marks are maxed and no later dart can move them.
    """
    marks = {
        variant: apply_visit(
            initial_state(CricketConfig(variant=variant)),
            tuple(sequence),
            CricketConfig(variant=variant),
            tuple(opponents),
        ).state.marks
        for variant in Variant
    }

    assert len(set(marks.values())) == 1


@given(state=teams, throw=throws, opponents=st.lists(teams, max_size=3), variant=variants)
def test_point_events_reconcile_with_every_total(
    state: CricketTeamState,
    throw: Throw,
    opponents: list[CricketTeamState],
    variant: Variant,
) -> None:
    """Nobody gains points that no event accounts for, and vice versa."""
    cfg = CricketConfig(variant=variant)
    before = tuple(opponents)
    if has_won(cfg, state, before):
        return

    outcome = apply_dart(state, throw, cfg, before)

    assert outcome.state.points - state.points == outcome.points
    assert outcome.points == sum(e.points for e in outcome.point_events if e.recipient is None)

    for i, (was, now) in enumerate(zip(before, outcome.opponents, strict=True)):
        expected = sum(e.points for e in outcome.point_events if e.recipient == i)
        assert now.points - was.points == expected
        assert now.marks == was.marks, "a dart never moves an opponent's marks"


@given(state=teams, throw=throws, opponents=st.lists(teams, max_size=3), variant=variants)
def test_only_surplus_ever_pays_and_wasted_means_nobody_was_paid(
    state: CricketTeamState,
    throw: Throw,
    opponents: list[CricketTeamState],
    variant: Variant,
) -> None:
    cfg = CricketConfig(variant=variant)
    if has_won(cfg, state, tuple(opponents)):
        return

    outcome = apply_dart(state, throw, cfg, tuple(opponents))

    if outcome.point_events:
        assert outcome.surplus_marks > 0
        assert not outcome.wasted
    assert outcome.wasted == (outcome.surplus_marks > 0 and not outcome.point_events)


@given(state=teams, throw=throws, opponents=st.lists(teams, min_size=1, max_size=3))
def test_cutthroat_never_pays_the_thrower(
    state: CricketTeamState, throw: Throw, opponents: list[CricketTeamState]
) -> None:
    if has_won(CUTTHROAT, state, tuple(opponents)):
        return

    outcome: DartOutcome = apply_dart(state, throw, CUTTHROAT, tuple(opponents))

    assert outcome.points == 0
    assert outcome.state.points == state.points
    assert all(e.recipient is not None for e in outcome.point_events)


@given(state=teams, throw=throws, opponents=st.lists(teams, max_size=3))
def test_quick_never_pays_anybody(
    state: CricketTeamState, throw: Throw, opponents: list[CricketTeamState]
) -> None:
    if has_won(QUICK, state, tuple(opponents)):
        return

    outcome = apply_dart(state, throw, QUICK, tuple(opponents))

    assert outcome.point_events == ()
    assert outcome.points == 0
    assert outcome.opponents == tuple(opponents)
