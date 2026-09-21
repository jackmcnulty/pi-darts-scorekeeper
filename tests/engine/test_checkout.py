"""Tests for the checkout table, its generator, and the runtime lookups.

The load-bearing test is `test_table_agrees_with_the_x01_engine`: it rebuilds
the set of checkable scores from scratch by feeding candidate dart sequences
through `x01.apply_visit`, and compares that against the committed table. The
generator and the engine are separate implementations of the same rules, so
agreeing is evidence; it is the reason `checkout_gen` does not import `x01`'s
rule predicates.
"""

import ast
import time
from functools import cache
from itertools import product
from pathlib import Path

import pytest

from darts.engine import checkout_gen
from darts.engine.checkout import (
    MAX_SUGGESTIONS,
    is_checkable,
    suggest,
    was_checkout_attempt,
)
from darts.engine.checkout_gen import SCORING_THROWS, generate_table, rank_key, render_module
from darts.engine.checkout_table import CHECKOUTS, MAX_DARTS, PATHS_PER_ENTRY
from darts.engine.throws import ALL_THROWS, Throw
from darts.engine.x01 import Rule, X01Config, X01TeamState, apply_visit
from darts.tools.gen_checkouts import TABLE_PATH, main

T20 = Throw(20, 3)
T19 = Throw(19, 3)
D20 = Throw(20, 2)
D16 = Throw(16, 2)
BULL = Throw(25, 2)

#: The highest score each out-rule can finish from in three darts.
CEILINGS = {Rule.DOUBLE: 170, Rule.MASTER: 180, Rule.STRAIGHT: 180}

#: Scores no three darts can finish on a double. The classic set from #7.
IMPOSSIBLE_IN_THREE = {169, 168, 166, 165, 163, 162, 159}


# --- acceptance criteria ---------------------------------------------------


def test_170_is_checkable_and_169_is_not() -> None:
    assert is_checkable(170, 3, Rule.DOUBLE) is True
    assert is_checkable(169, 3, Rule.DOUBLE) is False


@pytest.mark.parametrize("remaining", sorted(IMPOSSIBLE_IN_THREE))
def test_the_classic_impossible_set_is_not_checkable(remaining: int) -> None:
    assert is_checkable(remaining, 3, Rule.DOUBLE) is False
    assert suggest(remaining, 3, Rule.DOUBLE) == []


def test_170_suggests_the_only_path_there_is() -> None:
    assert suggest(170, 3, Rule.DOUBLE)[0] == (T20, T20, BULL)


def test_40_on_one_dart_is_exactly_d20() -> None:
    assert suggest(40, 1, Rule.DOUBLE) == [(D20,)]


def test_32_prefers_the_halving_ladder() -> None:
    assert suggest(32, 2, Rule.DOUBLE)[0][0] == D16


@pytest.mark.parametrize("out_rule", list(Rule))
def test_nothing_above_the_ceiling_is_checkable(out_rule: Rule) -> None:
    ceiling = CEILINGS[out_rule]
    assert is_checkable(ceiling, 3, out_rule) is True
    for remaining in range(ceiling + 1, ceiling + 40):
        assert is_checkable(remaining, 3, out_rule) is False


@pytest.mark.parametrize("out_rule", list(Rule))
@pytest.mark.parametrize("remaining", [0, -1, -170])
def test_nothing_at_or_below_zero_is_checkable(remaining: int, out_rule: Rule) -> None:
    assert is_checkable(remaining, 3, out_rule) is False


def test_one_is_checkable_only_on_a_straight_out() -> None:
    """The correction to #7: a single 1 finishes a straight-out leg."""
    assert is_checkable(1, 1, Rule.DOUBLE) is False
    assert is_checkable(1, 3, Rule.MASTER) is False
    assert is_checkable(1, 1, Rule.STRAIGHT) is True
    assert suggest(1, 1, Rule.STRAIGHT) == [(Throw(1, 1),)]


def test_master_and_straight_reach_180_but_double_stops_at_170() -> None:
    """The other half of the correction: 180 is T20 T20 T20."""
    assert is_checkable(180, 3, Rule.DOUBLE) is False
    assert is_checkable(180, 3, Rule.MASTER) is True
    assert is_checkable(180, 3, Rule.STRAIGHT) is True
    assert suggest(180, 3, Rule.MASTER)[0] == (T20, T20, T20)


def test_generation_completes_within_five_seconds() -> None:
    started = time.perf_counter()
    generate_table()
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"table generation took {elapsed:.2f}s"


# --- the cross-check against the x01 engine --------------------------------


@cache
def _checkable_by_brute_force(out_rule: Rule) -> frozenset[tuple[int, int]]:
    """`(remaining, darts_left)` pairs that x01 confirms can be checked out.

    Enumerates every ordered sequence of up to three scoring darts and replays
    it through `apply_visit`. A path checks out from exactly one score — its own
    total — so one pass covers every score at once.

    Misses are excluded: a miss scores nothing and consumes a dart, so any
    sequence containing one is dominated by the same sequence without it. This
    is asserted separately in `test_no_suggested_path_contains_a_miss` and
    `test_a_miss_never_creates_a_checkout`.
    """
    cfg = X01Config(start=501, in_rule=Rule.STRAIGHT, out_rule=out_rule)
    checkable: set[tuple[int, int]] = set()

    for length in range(1, MAX_DARTS + 1):
        for sequence in product(SCORING_THROWS, repeat=length):
            total = sum(t.score for t in sequence)
            visit = apply_visit(X01TeamState(remaining=total, is_open=True, darts=0), sequence, cfg)
            # `len(visit.darts) == length` rejects sequences that finished early;
            # those are shorter paths and get found on an earlier pass.
            if visit.checkout and len(visit.darts) == length:
                for darts_left in range(length, MAX_DARTS + 1):
                    checkable.add((total, darts_left))

    return frozenset(checkable)


@pytest.mark.parametrize("out_rule", list(Rule))
def test_table_agrees_with_the_x01_engine(out_rule: Rule) -> None:
    """Exhaustive cross-check of the generator against the engine.

    Every score from 0 to 200 and every dart count, both directions: nothing
    the engine can finish is missing from the table, and nothing in the table
    is unfinishable.
    """
    truth = _checkable_by_brute_force(out_rule)

    disagreements = [
        (remaining, darts_left, is_checkable(remaining, darts_left, out_rule))
        for remaining in range(0, 201)
        for darts_left in range(1, MAX_DARTS + 1)
        if is_checkable(remaining, darts_left, out_rule) != ((remaining, darts_left) in truth)
    ]

    assert not disagreements, (
        f"table and x01 disagree for {out_rule.value} out at "
        f"{[(r, d) for r, d, _ in disagreements]}"
    )


@pytest.mark.parametrize("out_rule", list(Rule))
def test_every_suggested_path_really_checks_out(out_rule: Rule) -> None:
    """#7's criterion: replay every stored path through `apply_visit`."""
    cfg = X01Config(start=501, in_rule=Rule.STRAIGHT, out_rule=out_rule)
    checked = 0

    for (rule_value, darts_left, remaining), paths in CHECKOUTS.items():
        if rule_value != out_rule.value:
            continue
        for labels in paths:
            path = tuple(Throw.parse(label) for label in labels)
            assert len(path) <= darts_left

            visit = apply_visit(X01TeamState(remaining=remaining, is_open=True, darts=0), path, cfg)
            assert visit.checkout is True, f"{labels} does not finish {remaining}"
            assert visit.bust is None
            assert visit.state.remaining == 0
            assert len(visit.darts) == len(path), "the path finished before its last dart"
            checked += 1

    assert checked > 0


def test_no_checkout_path_ever_leaves_one() -> None:
    """Why the generator has no `left_one` check.

    A finishing dart scores at least 2 under a double or master out, and two
    scoring darts always sum to at least 2, so the bust rule cannot bite. Under
    a straight out leaving 1 is legal, and a single 1 then finishes.
    """
    for (rule_value, _, remaining), paths in CHECKOUTS.items():
        if rule_value == Rule.STRAIGHT.value:
            continue
        for labels in paths:
            scores = [Throw.parse(label).score for label in labels]
            running = remaining
            for score in scores[:-1]:
                running -= score
                assert running >= 2, f"{labels} leaves {running} from {remaining}"


def test_no_suggested_path_contains_a_miss() -> None:
    miss = Throw(0, 0)
    for paths in CHECKOUTS.values():
        for labels in paths:
            assert miss.label not in labels


@pytest.mark.parametrize("out_rule", list(Rule))
def test_a_miss_never_creates_a_checkout(out_rule: Rule) -> None:
    """Justifies excluding misses from the search.

    A dart that scores nothing cannot turn an unfinishable score into a
    finishable one, so restricting the search to scoring darts loses nothing.
    """
    cfg = X01Config(start=501, in_rule=Rule.STRAIGHT, out_rule=out_rule)
    miss = Throw(0, 0)

    for remaining in range(1, 181):
        for sequence in product(ALL_THROWS, repeat=2):
            if miss not in sequence:
                continue
            visit = apply_visit(
                X01TeamState(remaining=remaining, is_open=True, darts=0), sequence, cfg
            )
            if not visit.checkout:
                continue
            # Drop the misses; what is left must already check out on its own.
            without = tuple(t for t in sequence if t != miss)
            assert is_checkable(remaining, len(without), out_rule), (
                f"{[t.label for t in sequence]} finishes {remaining} but "
                f"{[t.label for t in without]} is not in the table"
            )


# --- ranking ---------------------------------------------------------------


def test_fewest_darts_wins() -> None:
    """A one-dart finish always outranks a two- or three-dart route."""
    for remaining in (32, 40, 36, 50):
        best = suggest(remaining, 3, Rule.DOUBLE)[0]
        assert len(best) == 1

    lengths = [len(path) for path in suggest(100, 3, Rule.DOUBLE)]
    assert lengths == sorted(lengths)


@pytest.mark.parametrize(
    ("remaining", "expected_finish"),
    [
        (32, "D16"),
        (40, "D20"),
        (16, "D8"),
        (8, "D4"),
        (4, "D2"),
    ],
)
def test_halving_ladder_finishes_are_preferred(remaining: int, expected_finish: str) -> None:
    assert suggest(remaining, 3, Rule.DOUBLE)[0][-1].label == expected_finish


def test_a_ladder_finish_outranks_an_awkward_one() -> None:
    """Same length, same setup: D16 beats D13 on the ranking key."""
    ladder = (Throw(6, 1), D16)
    awkward = (Throw(12, 1), Throw(13, 2))
    assert rank_key(ladder) < rank_key(awkward)


def test_t20_setups_outrank_the_bull() -> None:
    assert rank_key((T20, D20)) < rank_key((BULL, Throw(15, 2)))
    assert rank_key((T19, D20)) < rank_key((BULL, Throw(15, 2)))


def test_a_forgiving_first_dart_is_preferred() -> None:
    """Same length, same finish, same setup cost — decided on the first dart."""
    on_20 = (Throw(20, 1), D20)
    on_19 = (Throw(19, 1), D20)
    on_1 = (Throw(1, 1), D20)

    assert rank_key(on_20) < rank_key(on_19) < rank_key(on_1)


def test_the_bull_is_the_least_forgiving_first_dart() -> None:
    """Segment 25 is the highest number on the board and the smallest target."""
    assert rank_key((Throw(1, 1), D20)) < rank_key((BULL, D20))


def test_101_leads_with_a_treble_not_the_bull() -> None:
    """T17 BULL is the standard two-dart 101, and it leads on the treble."""
    assert suggest(101, 3, Rule.DOUBLE)[0] == (Throw(17, 3), BULL)


# --- suggest / is_checkable contracts --------------------------------------


@pytest.mark.parametrize("darts_left", [0, -1, 4, 99])
def test_darts_left_outside_a_visit_is_rejected(darts_left: int) -> None:
    with pytest.raises(ValueError, match="darts_left"):
        is_checkable(40, darts_left, Rule.DOUBLE)
    with pytest.raises(ValueError, match="darts_left"):
        suggest(40, darts_left, Rule.DOUBLE)


def test_suggest_returns_at_most_the_stored_depth() -> None:
    assert MAX_SUGGESTIONS == PATHS_PER_ENTRY == 3
    assert len(suggest(100, 3, Rule.DOUBLE, n=99)) <= MAX_SUGGESTIONS


@pytest.mark.parametrize("n", [1, 2, 3])
def test_suggest_honours_smaller_n(n: int) -> None:
    assert len(suggest(100, 3, Rule.DOUBLE, n=n)) == n


def test_suggest_with_n_below_one_returns_nothing() -> None:
    assert suggest(100, 3, Rule.DOUBLE, n=0) == []


def test_suggest_is_empty_when_not_checkable() -> None:
    assert suggest(169, 3, Rule.DOUBLE) == []
    assert suggest(41, 1, Rule.DOUBLE) == []


def test_suggestions_are_ranked_and_distinct() -> None:
    paths = suggest(100, 3, Rule.DOUBLE)
    assert len(set(paths)) == len(paths)
    assert [rank_key(p) for p in paths] == sorted(rank_key(p) for p in paths)


def test_fewer_darts_never_makes_more_scores_checkable() -> None:
    """Monotonicity: anything checkable in one dart is checkable in two or three."""
    for out_rule in Rule:
        for remaining in range(1, 201):
            one = is_checkable(remaining, 1, out_rule)
            two = is_checkable(remaining, 2, out_rule)
            three = is_checkable(remaining, 3, out_rule)
            assert not one or two
            assert not two or three


# --- was_checkout_attempt --------------------------------------------------


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        # One-dart finishes on a double out: the doubles, plus the inner bull.
        (2, True),
        (40, True),
        (50, True),
        (32, True),
        # Odd numbers and anything above 50 are not one-dart finishes.
        (1, False),
        (3, False),
        (41, False),
        (51, False),
        (60, False),
        (170, False),
        (0, False),
    ],
)
def test_was_checkout_attempt_on_a_double_out(remaining: int, expected: bool) -> None:
    assert was_checkout_attempt(remaining, Rule.DOUBLE) is expected


def test_was_checkout_attempt_follows_the_out_rule() -> None:
    """60 is a one-dart finish with T20, but only where a triple may finish."""
    assert was_checkout_attempt(60, Rule.DOUBLE) is False
    assert was_checkout_attempt(60, Rule.MASTER) is True
    assert was_checkout_attempt(60, Rule.STRAIGHT) is True

    # 20 is a single, so only a straight out accepts it.
    assert was_checkout_attempt(20, Rule.DOUBLE) is True  # D10
    assert was_checkout_attempt(19, Rule.DOUBLE) is False
    assert was_checkout_attempt(19, Rule.STRAIGHT) is True


@pytest.mark.parametrize("out_rule", list(Rule))
def test_was_checkout_attempt_matches_one_dart_checkability(out_rule: Rule) -> None:
    """It is defined as exactly that, so it must not drift."""
    for remaining in range(1, 201):
        assert was_checkout_attempt(remaining, out_rule) is is_checkable(remaining, 1, out_rule)


# --- the committed artifact ------------------------------------------------


def test_table_is_up_to_date() -> None:
    """Regenerate in memory and diff against the committed file."""
    expected = render_module(generate_table())
    committed = TABLE_PATH.read_text(encoding="utf-8")
    assert committed == expected, "checkout_table.py is stale; run `uv run darts-gen-checkouts`"


def test_generation_is_deterministic() -> None:
    """Two runs must be byte-identical, or the no-diff CI check is noise."""
    assert render_module(generate_table()) == render_module(generate_table())


def test_check_mode_passes_on_the_committed_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_check_mode_fails_on_a_stale_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CI guard must actually fail when the table drifts."""
    stale = tmp_path / "checkout_table.py"
    stale.write_text("CHECKOUTS = {}\n", encoding="utf-8")
    monkeypatch.setattr("darts.tools.gen_checkouts.TABLE_PATH", stale)

    assert main(["--check"]) == 1
    assert "does not match the generator" in capsys.readouterr().err


def test_writing_the_table_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "checkout_table.py"
    monkeypatch.setattr("darts.tools.gen_checkouts.TABLE_PATH", target)

    assert main([]) == 0
    first = target.read_text(encoding="utf-8")
    assert main([]) == 0
    assert target.read_text(encoding="utf-8") == first
    assert first == TABLE_PATH.read_text(encoding="utf-8")


def test_the_table_declares_the_shape_the_generator_built_it_with() -> None:
    """The runtime reads these from the table, not from the generator."""
    assert MAX_DARTS == checkout_gen.MAX_DARTS
    assert PATHS_PER_ENTRY == checkout_gen.PATHS_PER_ENTRY


def test_the_runtime_does_not_import_the_generator() -> None:
    """The search must never have to load on the Pi.

    Parsed from disk rather than checked against the imported module, since the
    test session has already imported the generator for its own purposes. The
    docstring mentions `checkout_gen`, so this looks at imports, not text.
    """
    source = (TABLE_PATH.parent / "checkout.py").read_text(encoding="utf-8")
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any("checkout_gen" in name for name in imported), imported


def test_the_table_covers_every_out_rule() -> None:
    rules = {key[0] for key in CHECKOUTS}
    assert rules == {r.value for r in Rule}


def test_stored_paths_are_well_formed() -> None:
    """Every label round-trips through `Throw.parse`, and lengths are sane."""
    for (_, darts_left, remaining), paths in CHECKOUTS.items():
        assert 1 <= darts_left <= MAX_DARTS
        assert remaining >= 1
        assert 1 <= len(paths) <= PATHS_PER_ENTRY
        for labels in paths:
            assert 1 <= len(labels) <= darts_left
            throws = [Throw.parse(label) for label in labels]
            assert sum(t.score for t in throws) == remaining
