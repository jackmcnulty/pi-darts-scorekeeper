"""Offline exhaustive search that produces the committed checkout table.

Run via the `darts-gen-checkouts` console script. This module does the search
and renders the table *as a string*; it never touches the filesystem, because
it lives under `darts.engine` and the engine does no I/O. The script in
`darts.tools` writes what this returns.

Nothing here is on the hot path. The Pi loads the generated table and does
dictionary lookups; the search below runs on a developer's machine.

**This module deliberately does not reuse `x01`'s rule predicates.** It carries
its own reading of the out-rule and the bust rules, so that the cross-check in
`tests/engine/test_checkout.py` — which replays every suggested path through
`x01.apply_visit` — is comparing two independent implementations rather than
one implementation with itself.
"""

from typing import Final

from darts.engine.throws import ALL_THROWS, BULL, Throw
from darts.engine.x01 import Rule

#: A checkout path: the darts thrown, in order, ending on exactly zero.
Path = tuple[Throw, ...]

#: The most darts in a visit, and so the deepest path worth searching.
MAX_DARTS: Final = 3

#: How many ranked paths the table keeps per entry. `checkout.suggest` cannot
#: return more than this, and its default `n` matches it.
PATHS_PER_ENTRY: Final = 3

#: A miss can never help: it scores nothing and consumes a dart, so any path
#: containing one is strictly worse than the same path without it.
SCORING_THROWS: Final[tuple[Throw, ...]] = tuple(
    sorted((t for t in ALL_THROWS if t.score > 0), key=lambda t: (t.segment, t.multiplier))
)

_ORDINAL: Final[dict[Throw, int]] = {t: i for i, t in enumerate(SCORING_THROWS)}


def _finishes(out_rule: Rule, throw: Throw) -> bool:
    """Whether `throw` is a legal finishing dart under `out_rule`.

    Kept local rather than imported from `x01` on purpose — see the module
    docstring.
    """
    if out_rule is Rule.STRAIGHT:
        return True
    if out_rule is Rule.DOUBLE:
        return throw.multiplier == 2
    return throw.multiplier in (2, 3)


# --- ranking ------------------------------------------------------------------
#
# #7 ranked paths by four rules in strict priority, with "finishes on the halving
# ladder" a yes/no flag above setup quality. That let a bull setup, or a D2 or D4
# finish, win whenever the path ended on the ladder (#40). The ranking below
# keeps fewest darts first and the ordinal tiebreak last, and in between weighs
# the setup darts and the finishing dart *together*, the way a player does.
#
# The weights are small integers on purpose: each is a judgement a player would
# recognise, not a fitted probability. #40 checked them against the darts501.com
# checkout chart, and the expected picks it signed off are pinned in
# `tests/engine/test_checkout.py`. Retune here, then update that table.

#: Setup darts that make a path awkward whatever else it does. The bull is the
#: smallest target on the board, and a double as a setup is a double you could
#: miss before the one that counts. One bull outweighs two double setups (the
#: most a visit can hold), so a bull-free path always wins at the same length.
_BULL_SETUP: Final = 3
_DOUBLE_SETUP: Final = 1

#: Doubles at the bottom of the halving ladder. They halve as well as their size
#: allows, but a player finishing on D2 is one miss from D1 and two from a bust.
#: This penalty is what keeps D4 behind D20 and D12, which halve as often.
_BOTTOM_OF_LADDER: Final = frozenset({"D4", "D2"})


def _setup_cost(throw: Throw) -> int:
    """What a non-finishing dart costs, lowest first.

    A single is the biggest target on the board and T20 is where a player aims
    anyway, so both are free. T19 is the standard alternative. Any other triple
    is a small target picked only to reach a double.
    """
    if throw.multiplier == 1 or throw.label == "T20":
        return 0
    return 1 if throw.label == "T19" else 2


def _halvings(segment: int) -> int:
    """How many times a missed double on `segment` still leaves a double.

    Miss D16 into the single and 16 is left, which is D8; miss that and D4, then
    D2, then D1. That is four forgiven misses, the power of two in the segment.
    """
    count = 0
    while segment % 2 == 0:
        segment //= 2
        count += 1
    return count


def _finish_cost(throw: Throw) -> int:
    """What the finishing dart costs, lowest first.

    A double is scored by what a miss leaves (`_halvings`): D16 and D8 survive
    three or more misses, D20 and D12 two, D18/D14/D10/D6 one, an odd double
    none. The bull is the smallest target, so it costs as much as an odd double.

    The other two out-rules finish on more than doubles. A single, legal only on
    a straight out, is the biggest target there is, so it is free. A triple is
    as small a target as a double, and #40 asked for no change to how the other
    out-rules finish, so it costs as much as the worst double: a master out
    still prefers finishing on a double, as it did under #7's ladder flag.
    """
    if throw.segment == BULL or throw.multiplier == 3:
        return 5
    if throw.multiplier == 1:
        return 0
    cost = {0: 5, 1: 3, 2: 1}.get(_halvings(throw.segment), 0)
    return cost + (1 if throw.label in _BOTTOM_OF_LADDER else 0)


def _first_dart_rank(throw: Throw) -> int:
    """How forgiving a first dart is. Lower is better.

    A higher-numbered wedge first, and the bull last of all: it scores 25 but is
    the smallest target on the board. On the same number the triple leads the
    single, so the hard dart is thrown while two darts remain to repair a miss.
    The factor of 4 keeps the multiplier (at most 3) from outweighing a segment.
    """
    if throw.segment == BULL:
        return 0
    return -(throw.segment * 4 + throw.multiplier)


# The ranking runs over hundreds of thousands of candidate paths, so every
# per-throw property it needs is resolved once here and looked up thereafter.

#: The most a path can cost short of awkwardness: two of the dearest setups and
#: the dearest finish. Awkward setups are weighted in units one above it, so a
#: single sum orders by awkwardness first and cost second, exactly as a pair of
#: separate key elements would, but with one sum per path instead of two.
_MAX_COST: Final = 2 * max(map(_setup_cost, SCORING_THROWS)) + max(
    map(_finish_cost, SCORING_THROWS)
)
_AWKWARD_UNIT: Final = _MAX_COST + 1

_SETUP_WEIGHT: Final[dict[Throw, int]] = {
    t: _setup_cost(t)
    + _AWKWARD_UNIT
    * (_BULL_SETUP if t.segment == BULL else _DOUBLE_SETUP if t.multiplier == 2 else 0)
    for t in SCORING_THROWS
}
_FINISH_COST: Final[dict[Throw, int]] = {t: _finish_cost(t) for t in SCORING_THROWS}
_FIRST_DART_RANK: Final[dict[Throw, int]] = {t: _first_dart_rank(t) for t in SCORING_THROWS}

# Bound lookups, fed to `map` below. The dictionary reads then run in C with no
# Python frame per dart, which matters most under coverage: CI instruments this
# loop and runs close to the five-second budget (#46).
_setup_weight = _SETUP_WEIGHT.__getitem__
_ordinal = _ORDINAL.__getitem__


def rank_key(path: Path) -> tuple[int, int, int, tuple[int, ...]]:
    """The ordering from #40, lowest first.

    1. Fewest darts. `generate` relies on this coming first.
    2. One sum, in two bands:
       a. awkward setups: no bull, then no double, wherever the same number of
          darts can avoid them;
       b. then the setup darts' cost plus the finishing dart's cost, weighed
          together, so a T20 setup cannot buy a D2 finish and a D16 finish
          cannot buy a low triple setup.
    3. A forgiving first dart.

    The trailing ordinals are a deterministic tiebreak, so the generated table
    is byte-identical between runs and the no-diff CI check means something.
    """
    return (
        len(path),
        sum(map(_setup_weight, path[:-1])) + _FINISH_COST[path[-1]],
        _FIRST_DART_RANK[path[0]],
        tuple(map(_ordinal, path)),
    )


def _paths_by_total(out_rule: Rule) -> dict[tuple[int, int], list[Path]]:
    """Every checkout path, keyed by `(total scored, darts used)`.

    A path checks out from `remaining` exactly when `remaining` is its total, so
    the search runs once per out-rule rather than once per score.

    The intermediate bust rules fall out of the suffix sums: after the first of
    three darts the player is left with `second + third`, and after the second
    with `third`. Neither depends on the starting score, so both checks hoist
    out of the innermost loop.
    """
    finishers = [t for t in SCORING_THROWS if _finishes(out_rule, t)]

    # x01's third bust rule — leaving exactly 1 under a non-straight out — can
    # never invalidate a checkout path, so there is no check for it here:
    #   * after the final dart the score is 0, not 1;
    #   * after the second-to-last it equals the final dart's score, and a
    #     double or triple always scores at least 2;
    #   * after the first of three it is the sum of two scoring darts, so at
    #     least 2.
    # Under a straight out, leaving 1 is legal anyway and a single 1 finishes
    # it. `test_no_checkout_path_ever_leaves_one` pins this.

    found: dict[tuple[int, int], list[Path]] = {}

    for finisher in finishers:
        found.setdefault((finisher.score, 1), []).append((finisher,))

    for first in SCORING_THROWS:
        for finisher in finishers:
            found.setdefault((first.score + finisher.score, 2), []).append((first, finisher))

    for middle in SCORING_THROWS:
        for finisher in finishers:
            suffix = middle.score + finisher.score
            for first in SCORING_THROWS:
                found.setdefault((first.score + suffix, 3), []).append((first, middle, finisher))

    return found


def generate(out_rule: Rule) -> dict[tuple[int, int], tuple[Path, ...]]:
    """Top-ranked paths for one out-rule, keyed by `(remaining, darts_left)`.

    Only reachable scores get an entry, so the table's bounds are emergent:
    2..170 for a double out, 2..180 for master, 1..180 for straight.
    """
    by_total = _paths_by_total(out_rule)
    for paths in by_total.values():
        paths.sort(key=rank_key)

    table: dict[tuple[int, int], tuple[Path, ...]] = {}
    totals = {total for total, _ in by_total}

    for remaining in sorted(totals):
        # `rank_key` leads with the path length, so a shorter path always
        # outranks a longer one. That means the running top-N can be truncated
        # at every step instead of accumulating every path for the score.
        best: list[Path] = []
        for darts_left in range(1, MAX_DARTS + 1):
            best = (best + by_total.get((remaining, darts_left), []))[:PATHS_PER_ENTRY]
            if best:
                table[(remaining, darts_left)] = tuple(best)

    return table


#: The table as plain data: `(out_rule, darts_left, remaining) -> ranked paths`,
#: each path a tuple of `Throw.label` strings.
TableData = dict[tuple[str, int, int], tuple[tuple[str, ...], ...]]


def generate_table() -> TableData:
    """The full table for all three out-rules, as plain strings and ints."""
    table: TableData = {}
    for out_rule in Rule:
        for (remaining, darts_left), paths in generate(out_rule).items():
            key = (out_rule.value, darts_left, remaining)
            table[key] = tuple(tuple(t.label for t in path) for path in paths)
    return table


_HEADER: Final = '''"""Checkout lookup table. Generated by `darts-gen-checkouts` — do not edit.

Keyed by `(out_rule, darts_left, remaining)`. Each value is the ranked list of
checkout paths, each path a tuple of `Throw.label` strings. A score that cannot
be checked out has no entry at all, so membership *is* checkability.

The table declares its own shape below, so `darts.engine.checkout` can read it
without importing the generator that built it — the search never has to load on
the Pi.

Regenerate with `uv run darts-gen-checkouts`. CI fails if this file and the
generator disagree.
"""

from typing import Final

#: The most darts in a visit, and so the largest `darts_left` key.
MAX_DARTS: Final = {max_darts}

#: How many ranked paths each entry holds.
PATHS_PER_ENTRY: Final = {paths_per_entry}

CHECKOUTS: Final[dict[tuple[str, int, int], tuple[tuple[str, ...], ...]]] = {{
'''


def render_module(table: TableData) -> str:
    """Render the table as the source of an importable Python module.

    Sorted on every axis so that regenerating an unchanged table produces a
    byte-identical file.
    """
    lines = [_HEADER.format(max_darts=MAX_DARTS, paths_per_entry=PATHS_PER_ENTRY)]
    for key in sorted(table):
        out_rule, darts_left, remaining = key
        paths = table[key]
        # A one-element tuple needs its trailing comma; longer ones read better
        # without. Both the inner paths and the outer tuple of paths need this.
        rendered = ", ".join(_render_path(path) for path in paths)
        outer = f"({rendered},)" if len(paths) == 1 else f"({rendered})"
        lines.append(f'    ("{out_rule}", {darts_left}, {remaining}): {outer},\n')
    lines.append("}\n")
    return "".join(lines)


def _render_path(path: tuple[str, ...]) -> str:
    inner = ", ".join(f'"{label}"' for label in path)
    return f"({inner},)" if len(path) == 1 else f"({inner})"
