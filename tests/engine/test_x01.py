"""Tests for the x01 engine.

The bust rule gets its own class, because voiding the whole visit rather than
just the offending dart is the thing implementations get wrong.
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from darts.engine.throws import ALL_THROWS, BULL, DOUBLE, SINGLE, TRIPLE, Throw
from darts.engine.x01 import (
    BustReason,
    Rule,
    X01Config,
    X01TeamState,
    apply_dart,
    apply_visit,
    initial_state,
)

ALL_THROWS_SORTED = sorted(ALL_THROWS, key=lambda t: (t.segment, t.multiplier))

MISS_THROW = Throw(0, 0)
OUTER_BULL = Throw(BULL, SINGLE)
INNER_BULL = Throw(BULL, DOUBLE)

STARTS = [301, 501, 701]


def cfg(start: int = 501, in_rule: Rule = Rule.STRAIGHT, out_rule: Rule = Rule.DOUBLE) -> X01Config:
    return X01Config(start=start, in_rule=in_rule, out_rule=out_rule)


def state(remaining: int, *, is_open: bool = True, darts: int = 0) -> X01TeamState:
    return X01TeamState(remaining=remaining, is_open=is_open, darts=darts)


# --- config and state ------------------------------------------------------


@pytest.mark.parametrize("start", [0, -1, -501])
def test_config_rejects_non_positive_start(start: int) -> None:
    with pytest.raises(ValueError):
        X01Config(start=start, in_rule=Rule.STRAIGHT, out_rule=Rule.DOUBLE)


@pytest.mark.parametrize("start", [1, 2, 170, 301, 501, 701, 1001])
def test_config_accepts_any_positive_start(start: int) -> None:
    assert X01Config(start=start, in_rule=Rule.STRAIGHT, out_rule=Rule.DOUBLE).start == start


def test_state_rejects_impossible_values() -> None:
    with pytest.raises(ValueError):
        X01TeamState(remaining=-1, is_open=True, darts=0)
    with pytest.raises(ValueError):
        X01TeamState(remaining=10, is_open=True, darts=-1)


@pytest.mark.parametrize("start", STARTS)
@pytest.mark.parametrize("in_rule", list(Rule))
def test_initial_state(start: int, in_rule: Rule) -> None:
    """A straight-in team is open from the outset; everyone else must earn it."""
    initial = initial_state(cfg(start=start, in_rule=in_rule))
    assert initial.remaining == start
    assert initial.darts == 0
    assert initial.is_open is (in_rule is Rule.STRAIGHT)
    assert not initial.is_finished


def test_rule_is_a_string_enum() -> None:
    """Values are the strings #6 specifies, so they serialise as themselves."""
    assert [r.value for r in Rule] == ["straight", "double", "master"]
    assert [b.value for b in BustReason] == ["below_zero", "wrong_finish", "left_one"]


# --- in-rule ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("in_rule", "throw", "opens"),
    [
        # STRAIGHT opens on literally anything, including a miss.
        (Rule.STRAIGHT, MISS_THROW, True),
        (Rule.STRAIGHT, Throw(20, SINGLE), True),
        (Rule.STRAIGHT, OUTER_BULL, True),
        # DOUBLE needs multiplier 2. The inner bull is 25 doubled, so it counts.
        (Rule.DOUBLE, Throw(20, SINGLE), False),
        (Rule.DOUBLE, Throw(20, TRIPLE), False),
        (Rule.DOUBLE, MISS_THROW, False),
        (Rule.DOUBLE, OUTER_BULL, False),
        (Rule.DOUBLE, Throw(20, DOUBLE), True),
        (Rule.DOUBLE, INNER_BULL, True),
        # MASTER takes doubles or triples.
        (Rule.MASTER, Throw(20, SINGLE), False),
        (Rule.MASTER, MISS_THROW, False),
        (Rule.MASTER, OUTER_BULL, False),
        (Rule.MASTER, Throw(20, DOUBLE), True),
        (Rule.MASTER, Throw(20, TRIPLE), True),
        (Rule.MASTER, INNER_BULL, True),
    ],
)
def test_in_rule_decides_what_opens(in_rule: Rule, throw: Throw, opens: bool) -> None:
    configuration = cfg(in_rule=in_rule, out_rule=Rule.STRAIGHT)
    outcome = apply_dart(initial_state(configuration), throw, configuration)

    assert outcome.state.is_open is opens
    assert outcome.counted is opens
    assert outcome.state.remaining == (501 - throw.score if opens else 501)


def test_darts_before_the_opening_double_are_thrown_but_score_nothing() -> None:
    """Acceptance criterion: counted=False, remaining unchanged, darts still tick up."""
    configuration = cfg(start=301, in_rule=Rule.DOUBLE, out_rule=Rule.DOUBLE)
    visit = apply_visit(
        initial_state(configuration),
        (Throw(20, TRIPLE), Throw(19, SINGLE), MISS_THROW),
        configuration,
    )

    assert [d.counted for d in visit.darts] == [False, False, False]
    assert visit.state.remaining == 301
    assert visit.state.is_open is False
    assert visit.state.darts == 3
    assert visit.bust is None


def test_the_opening_dart_itself_scores() -> None:
    configuration = cfg(start=301, in_rule=Rule.DOUBLE, out_rule=Rule.DOUBLE)
    visit = apply_visit(
        initial_state(configuration),
        (Throw(20, SINGLE), Throw(20, DOUBLE), Throw(20, TRIPLE)),
        configuration,
    )

    assert [d.counted for d in visit.darts] == [False, True, True]
    assert visit.state.remaining == 301 - 40 - 60
    assert visit.state.is_open is True
    assert visit.state.darts == 3


# --- out-rule --------------------------------------------------------------


@pytest.mark.parametrize(
    ("out_rule", "throw", "finishes"),
    [
        (Rule.STRAIGHT, Throw(20, SINGLE), True),
        (Rule.STRAIGHT, OUTER_BULL, True),
        (Rule.DOUBLE, Throw(20, SINGLE), False),
        (Rule.DOUBLE, Throw(20, TRIPLE), False),
        (Rule.DOUBLE, Throw(20, DOUBLE), True),
        (Rule.MASTER, Throw(20, SINGLE), False),
        (Rule.MASTER, Throw(20, DOUBLE), True),
        (Rule.MASTER, Throw(20, TRIPLE), True),
    ],
)
def test_out_rule_decides_what_finishes(out_rule: Rule, throw: Throw, finishes: bool) -> None:
    configuration = cfg(out_rule=out_rule)
    outcome = apply_dart(state(throw.score), throw, configuration)

    assert outcome.checkout is finishes
    assert outcome.state.is_finished is finishes
    if not finishes:
        assert outcome.bust is BustReason.WRONG_FINISH


@pytest.mark.parametrize("out_rule", [Rule.DOUBLE, Rule.MASTER])
def test_inner_bull_finishes_but_outer_bull_cannot(out_rule: Rule) -> None:
    """#6's criterion, as two scenarios — no single remaining satisfies both.

    The inner bull only reaches 0 from 50; the outer bull only reaches 0 from 25.
    """
    configuration = cfg(out_rule=out_rule)

    finished = apply_dart(state(50), INNER_BULL, configuration)
    assert finished.checkout is True
    assert finished.bust is None
    assert finished.state.remaining == 0

    refused = apply_dart(state(25), OUTER_BULL, configuration)
    assert refused.checkout is False
    assert refused.bust is BustReason.WRONG_FINISH
    assert refused.state.remaining == 25


def test_outer_bull_finishes_a_straight_out_leg() -> None:
    outcome = apply_dart(state(25), OUTER_BULL, cfg(out_rule=Rule.STRAIGHT))
    assert outcome.checkout is True
    assert outcome.state.remaining == 0


# --- bust reasons ----------------------------------------------------------


@pytest.mark.parametrize(
    ("remaining", "throw", "out_rule", "expected"),
    [
        # below_zero: scored more than was left.
        (20, Throw(20, TRIPLE), Rule.STRAIGHT, BustReason.BELOW_ZERO),
        (20, Throw(20, TRIPLE), Rule.DOUBLE, BustReason.BELOW_ZERO),
        (50, Throw(20, TRIPLE), Rule.MASTER, BustReason.BELOW_ZERO),
        (1, Throw(20, SINGLE), Rule.STRAIGHT, BustReason.BELOW_ZERO),
        (25, INNER_BULL, Rule.DOUBLE, BustReason.BELOW_ZERO),
        # wrong_finish: landed on zero with an illegal finishing dart.
        (20, Throw(20, SINGLE), Rule.DOUBLE, BustReason.WRONG_FINISH),
        (60, Throw(20, TRIPLE), Rule.DOUBLE, BustReason.WRONG_FINISH),
        (25, OUTER_BULL, Rule.DOUBLE, BustReason.WRONG_FINISH),
        (20, Throw(20, SINGLE), Rule.MASTER, BustReason.WRONG_FINISH),
        (25, OUTER_BULL, Rule.MASTER, BustReason.WRONG_FINISH),
        # left_one: cannot be finished on a double or master out.
        (21, Throw(20, SINGLE), Rule.DOUBLE, BustReason.LEFT_ONE),
        (41, Throw(20, DOUBLE), Rule.DOUBLE, BustReason.LEFT_ONE),
        (61, Throw(20, TRIPLE), Rule.MASTER, BustReason.LEFT_ONE),
        (26, OUTER_BULL, Rule.DOUBLE, BustReason.LEFT_ONE),
        # Not busts.
        (21, Throw(20, SINGLE), Rule.STRAIGHT, None),
        (40, Throw(20, DOUBLE), Rule.DOUBLE, None),
        (50, INNER_BULL, Rule.DOUBLE, None),
        (60, Throw(20, TRIPLE), Rule.MASTER, None),
        (100, Throw(20, TRIPLE), Rule.DOUBLE, None),
        (100, MISS_THROW, Rule.DOUBLE, None),
    ],
)
def test_bust_reasons(
    remaining: int, throw: Throw, out_rule: Rule, expected: BustReason | None
) -> None:
    outcome = apply_dart(state(remaining), throw, cfg(out_rule=out_rule))
    assert outcome.bust is expected


@pytest.mark.parametrize("reason", list(BustReason))
def test_every_bust_reason_is_reachable(reason: BustReason) -> None:
    """Guards against a reason existing in the enum but never being produced."""
    scenarios = {
        BustReason.BELOW_ZERO: (20, Throw(20, TRIPLE)),
        BustReason.WRONG_FINISH: (20, Throw(20, SINGLE)),
        BustReason.LEFT_ONE: (21, Throw(20, SINGLE)),
    }
    remaining, throw = scenarios[reason]
    assert apply_dart(state(remaining), throw, cfg(out_rule=Rule.DOUBLE)).bust is reason


@pytest.mark.parametrize("out_rule", list(Rule))
def test_leaving_one_busts_unless_the_out_rule_is_straight(out_rule: Rule) -> None:
    """Acceptance criterion, stated directly."""
    outcome = apply_dart(state(21), Throw(20, SINGLE), cfg(out_rule=out_rule))

    if out_rule is Rule.STRAIGHT:
        assert outcome.bust is None
        assert outcome.state.remaining == 1
    else:
        assert outcome.bust is BustReason.LEFT_ONE
        assert outcome.state.remaining == 21


def test_below_zero_is_checked_before_wrong_finish() -> None:
    """Ordering matters: -20 is below zero, not a wrong finish."""
    outcome = apply_dart(state(40), Throw(20, TRIPLE), cfg(out_rule=Rule.DOUBLE))
    assert outcome.bust is BustReason.BELOW_ZERO


def test_landing_on_zero_is_never_reported_as_left_one() -> None:
    outcome = apply_dart(state(20), Throw(20, SINGLE), cfg(out_rule=Rule.DOUBLE))
    assert outcome.bust is BustReason.WRONG_FINISH


# --- the bust rule ---------------------------------------------------------


class TestBustVoidsTheWholeVisit:
    """A bust reverts the entire visit, not just the dart that caused it.

    The darts still happened, so they still count toward `darts`. Everything
    else goes back to where the visit started.
    """

    CONFIG = X01Config(start=501, in_rule=Rule.STRAIGHT, out_rule=Rule.DOUBLE)

    def test_bust_on_the_first_dart(self) -> None:
        start = state(20, darts=9)
        visit = apply_visit(start, (Throw(20, TRIPLE),), self.CONFIG)

        assert visit.bust is BustReason.BELOW_ZERO
        assert visit.score_before == visit.score_after == 20
        assert visit.state.remaining == 20
        assert visit.state.darts == 10

    def test_bust_on_the_second_dart_voids_the_first(self) -> None:
        """The first dart scored 60 perfectly legally. It is still voided."""
        start = state(100, darts=9)
        visit = apply_visit(start, (Throw(20, TRIPLE), Throw(20, TRIPLE)), self.CONFIG)

        assert visit.bust is BustReason.BELOW_ZERO
        assert visit.score_before == visit.score_after == 100
        assert visit.state.remaining == 100
        assert [d.counted for d in visit.darts] == [False, False]

    def test_bust_on_the_third_dart_voids_the_first_two(self) -> None:
        start = state(150, darts=9)
        visit = apply_visit(
            start,
            (Throw(20, TRIPLE), Throw(20, TRIPLE), Throw(20, TRIPLE)),
            self.CONFIG,
        )

        assert visit.bust is BustReason.BELOW_ZERO
        assert visit.score_after == 150
        assert [d.counted for d in visit.darts] == [False, False, False]
        assert visit.state.darts == 12

    @pytest.mark.parametrize("dart_index", [0, 1, 2])
    def test_score_reverts_regardless_of_which_dart_busted(self, dart_index: int) -> None:
        """Acceptance criterion, swept across every position in the visit."""
        # From 40, single 1s are harmless; the inner bull at `dart_index` busts.
        throws = [Throw(1, SINGLE)] * 3
        throws[dart_index] = INNER_BULL
        visit = apply_visit(state(40, darts=6), tuple(throws), self.CONFIG)

        assert visit.bust is BustReason.BELOW_ZERO
        assert visit.score_before == 40
        assert visit.score_after == 40
        assert visit.state.remaining == 40
        assert all(not d.counted for d in visit.darts)

    def test_darts_after_the_bust_are_never_thrown(self) -> None:
        """Acceptance criterion: apply_visit stops at the bust."""
        start = state(20, darts=0)
        visit = apply_visit(
            start,
            (Throw(20, TRIPLE), Throw(20, TRIPLE), Throw(20, TRIPLE)),
            self.CONFIG,
        )

        assert len(visit.darts) == 1
        assert visit.state.darts == 1

    def test_a_bust_also_voids_the_in_rule(self) -> None:
        """Jack's call on #6: the opening double is void along with the visit.

        Double-in from 61: D20 opens and leaves 21, then T20 busts. The team
        ends on 61 and is *not* open.
        """
        configuration = X01Config(start=61, in_rule=Rule.DOUBLE, out_rule=Rule.DOUBLE)
        visit = apply_visit(
            initial_state(configuration),
            (Throw(20, DOUBLE), Throw(20, TRIPLE)),
            configuration,
        )

        assert visit.bust is BustReason.BELOW_ZERO
        assert visit.state.remaining == 61
        assert visit.state.is_open is False
        assert visit.state.darts == 2
        assert all(not d.counted for d in visit.darts)

    def test_an_already_open_team_stays_open_through_a_bust(self) -> None:
        """Reverting means back to the start of the visit, not back to closed."""
        configuration = X01Config(start=501, in_rule=Rule.DOUBLE, out_rule=Rule.DOUBLE)
        visit = apply_visit(state(20, is_open=True, darts=12), (Throw(20, TRIPLE),), configuration)

        assert visit.bust is BustReason.BELOW_ZERO
        assert visit.state.is_open is True

    def test_voided_darts_keep_their_own_running_dart_count(self) -> None:
        visit = apply_visit(
            state(100, darts=9),
            (Throw(20, TRIPLE), Throw(20, TRIPLE)),
            self.CONFIG,
        )

        assert [d.state.darts for d in visit.darts] == [10, 11]
        assert [d.state.remaining for d in visit.darts] == [100, 100]


# --- visits ----------------------------------------------------------------


def test_visit_stops_after_a_checkout() -> None:
    """Acceptance criterion: no darts are thrown after the leg is won."""
    visit = apply_visit(state(40), (Throw(20, DOUBLE), Throw(20, TRIPLE), Throw(20, TRIPLE)), cfg())

    assert visit.checkout is True
    assert len(visit.darts) == 1
    assert visit.state.remaining == 0
    assert visit.state.darts == 1


def test_empty_visit_changes_nothing() -> None:
    start = state(501, darts=3)
    visit = apply_visit(start, (), cfg())

    assert visit.state == start
    assert visit.darts == ()
    assert visit.bust is None
    assert visit.checkout is False


def test_a_clean_visit_subtracts_every_dart() -> None:
    visit = apply_visit(
        state(501), (Throw(20, TRIPLE), Throw(20, TRIPLE), Throw(20, TRIPLE)), cfg()
    )

    assert visit.score_before == 501
    assert visit.score_after == 501 - 180
    assert visit.state.darts == 3
    assert all(d.counted for d in visit.darts)
    assert visit.bust is None


def test_apply_dart_refuses_to_throw_at_a_won_leg() -> None:
    with pytest.raises(ValueError, match="already won"):
        apply_dart(state(0), Throw(20, SINGLE), cfg())


def test_is_bust_mirrors_the_reason() -> None:
    assert apply_visit(state(20), (Throw(20, TRIPLE),), cfg()).is_bust is True
    assert apply_visit(state(100), (Throw(20, TRIPLE),), cfg()).is_bust is False


# --- full legs -------------------------------------------------------------


def test_a_full_501_double_out_leg_played_to_zero() -> None:
    """Nine-dart finish: 180, 180, then 141 out (T20 T19 D12)."""
    configuration = cfg(start=501, in_rule=Rule.STRAIGHT, out_rule=Rule.DOUBLE)
    current = initial_state(configuration)

    visits = [
        (Throw(20, TRIPLE), Throw(20, TRIPLE), Throw(20, TRIPLE)),
        (Throw(20, TRIPLE), Throw(20, TRIPLE), Throw(20, TRIPLE)),
        (Throw(20, TRIPLE), Throw(19, TRIPLE), Throw(12, DOUBLE)),
    ]
    remaining_after = [321, 141, 0]

    for throws, expected in zip(visits, remaining_after, strict=True):
        visit = apply_visit(current, throws, configuration)
        assert visit.bust is None
        assert visit.state.remaining == expected
        current = visit.state

    assert current.is_finished
    assert current.darts == 9


def test_a_full_301_double_in_double_out_leg_with_wasted_darts() -> None:
    """Includes a whole visit thrown before the opening double lands."""
    configuration = cfg(start=301, in_rule=Rule.DOUBLE, out_rule=Rule.DOUBLE)
    current = initial_state(configuration)

    # Visit 1: three darts, none of them a double. Nothing scores.
    visit = apply_visit(current, (Throw(20, TRIPLE), Throw(20, SINGLE), MISS_THROW), configuration)
    assert visit.state.remaining == 301
    assert visit.state.is_open is False
    assert visit.state.darts == 3
    assert all(not d.counted for d in visit.darts)
    current = visit.state

    # Visit 2: first dart misses again, then D20 opens and everything after counts.
    visit = apply_visit(
        current, (Throw(5, SINGLE), Throw(20, DOUBLE), Throw(20, TRIPLE)), configuration
    )
    assert [d.counted for d in visit.darts] == [False, True, True]
    assert visit.state.is_open is True
    assert visit.state.remaining == 301 - 40 - 60
    current = visit.state

    # Visit 3: 201 left. 140 leaves 61.
    visit = apply_visit(
        current, (Throw(20, TRIPLE), Throw(20, TRIPLE), Throw(20, SINGLE)), configuration
    )
    assert visit.state.remaining == 61
    current = visit.state

    # Visit 4: 61 out is T15 D8.
    visit = apply_visit(current, (Throw(15, TRIPLE), Throw(8, DOUBLE)), configuration)
    assert visit.checkout is True
    assert visit.state.remaining == 0
    current = visit.state

    assert current.is_finished
    assert current.darts == 11


@pytest.mark.parametrize("start", STARTS)
def test_the_same_leg_logic_works_for_every_start(start: int) -> None:
    """Acceptance criterion: nothing is hardcoded to 501."""
    configuration = cfg(start=start, in_rule=Rule.STRAIGHT, out_rule=Rule.DOUBLE)
    current = initial_state(configuration)

    # Every x01 start is odd, so take 1 off to make a double-out reachable.
    opener = apply_dart(current, Throw(1, SINGLE), configuration)
    assert opener.bust is None
    current = opener.state
    assert current.remaining == start - 1

    # Grind down in 40s. No step lands on 0 or 1 from any of the three starts.
    while current.remaining > 40:
        outcome = apply_dart(current, Throw(20, DOUBLE), configuration)
        assert outcome.bust is None
        current = outcome.state

    assert current.remaining % 2 == 0, "a double-out finish must be reachable"

    finish = apply_dart(current, Throw(current.remaining // 2, DOUBLE), configuration)
    assert finish.checkout is True
    assert finish.state.is_finished
    assert finish.state.remaining == 0


# --- all nine rule combinations -------------------------------------------


@pytest.mark.parametrize("out_rule", list(Rule))
@pytest.mark.parametrize("in_rule", list(Rule))
def test_all_nine_rule_combinations_play_a_leg_to_zero(in_rule: Rule, out_rule: Rule) -> None:
    """Every in_rule x out_rule pair can open, score and finish."""
    configuration = cfg(start=301, in_rule=in_rule, out_rule=out_rule)
    current = initial_state(configuration)

    # Opening dart: a double satisfies every in-rule.
    opener = apply_dart(current, Throw(20, DOUBLE), configuration)
    assert opener.state.is_open is True
    assert opener.counted is True
    current = opener.state
    assert current.remaining == 261

    # 261 down to 30, never touching 0 or 1, so no out-rule can object en route.
    for throw, expected in [
        (Throw(20, TRIPLE), 201),
        (Throw(20, TRIPLE), 141),
        (Throw(20, TRIPLE), 81),
        (Throw(17, TRIPLE), 30),
    ]:
        outcome = apply_dart(current, throw, configuration)
        assert outcome.bust is None
        assert outcome.state.remaining == expected
        current = outcome.state

    # D15 is a legal finish under all three out-rules.
    done = apply_dart(current, Throw(15, DOUBLE), configuration)
    assert done.bust is None
    assert done.checkout is True
    assert done.state.is_finished
    assert done.state.darts == 6


@pytest.mark.parametrize("out_rule", list(Rule))
@pytest.mark.parametrize("in_rule", list(Rule))
def test_all_nine_rule_combinations_respect_their_rules(in_rule: Rule, out_rule: Rule) -> None:
    """A single that neither opens nor finishes, checked against both rules."""
    configuration = cfg(start=301, in_rule=in_rule, out_rule=out_rule)
    single = Throw(20, SINGLE)

    opened = apply_dart(initial_state(configuration), single, configuration)
    assert opened.state.is_open is (in_rule is Rule.STRAIGHT)

    finished = apply_dart(state(20), single, configuration)
    assert finished.checkout is (out_rule is Rule.STRAIGHT)


# --- properties ------------------------------------------------------------

_LEG_RULES = st.sampled_from(list(Rule))


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(
    start=st.sampled_from(STARTS),
    in_rule=_LEG_RULES,
    out_rule=_LEG_RULES,
    visits=st.lists(
        st.lists(st.sampled_from(ALL_THROWS_SORTED), min_size=1, max_size=3),
        min_size=1,
        max_size=60,
    ),
)
def test_random_play_never_reaches_an_illegal_state(
    start: int, in_rule: Rule, out_rule: Rule, visits: list[list[Throw]]
) -> None:
    """Over arbitrary dart sequences: remaining is never negative, never left at
    1 under a non-straight out-rule, and a checkout is always the last counted
    dart of the leg.
    """
    configuration = cfg(start=start, in_rule=in_rule, out_rule=out_rule)
    current = initial_state(configuration)
    checked_out = False

    for throws in visits:
        if checked_out:
            break

        before = current
        visit = apply_visit(current, tuple(throws), configuration)

        assert visit.state.remaining >= 0
        assert visit.score_before == before.remaining
        assert visit.score_after == visit.state.remaining
        assert visit.state.darts == before.darts + len(visit.darts)

        if out_rule is not Rule.STRAIGHT:
            assert visit.state.remaining != 1

        if visit.is_bust:
            assert visit.state.remaining == before.remaining
            assert visit.state.is_open == before.is_open
            assert not any(d.counted for d in visit.darts)
            assert not visit.checkout

        if visit.checkout:
            assert visit.state.remaining == 0
            # The checkout is the final dart of the visit, and it counted.
            assert visit.darts[-1].checkout is True
            assert visit.darts[-1].counted is True
            assert not any(d.checkout for d in visit.darts[:-1])
            checked_out = True

        # Nothing can happen after the leg is won.
        assert visit.state.is_finished == checked_out
        current = visit.state

    if checked_out:
        assert current.is_finished


@given(
    remaining=st.integers(min_value=1, max_value=701),
    throw=st.sampled_from(ALL_THROWS_SORTED),
    out_rule=_LEG_RULES,
)
def test_a_dart_either_counts_or_leaves_the_score_alone(
    remaining: int, throw: Throw, out_rule: Rule
) -> None:
    configuration = cfg(out_rule=out_rule)
    outcome = apply_dart(state(remaining), throw, configuration)

    if outcome.counted:
        assert outcome.state.remaining == remaining - throw.score
        assert outcome.bust is None
    else:
        assert outcome.state.remaining == remaining
        assert outcome.bust is not None

    assert outcome.state.darts == 1
    assert outcome.checkout is (outcome.state.remaining == 0)
