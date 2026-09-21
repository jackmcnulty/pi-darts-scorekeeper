"""Checkout hints at runtime: pure dictionary lookups against a committed table.

Every question here is answered by indexing `checkout_table.CHECKOUTS`, so the
hint can repaint on every keypad tap without the Pi thinking about it. The
search that built that table lives in `checkout_gen` and never runs in the app.

A score with no entry in the table cannot be checked out, so membership *is*
checkability — there is no separate flag to keep in sync.
"""

from functools import cache
from typing import Final

from darts.engine.checkout_table import CHECKOUTS, MAX_DARTS, PATHS_PER_ENTRY
from darts.engine.throws import Throw
from darts.engine.x01 import Rule

#: The most paths `suggest` can return, fixed by what the table stores.
MAX_SUGGESTIONS: Final = PATHS_PER_ENTRY


def _validate_darts_left(darts_left: int) -> None:
    if not 1 <= darts_left <= MAX_DARTS:
        raise ValueError(f"darts_left must be 1..{MAX_DARTS}; got {darts_left!r}")


@cache
def _parse(labels: tuple[str, ...]) -> tuple[Throw, ...]:
    """Turn a stored path back into throws, once per distinct path."""
    return tuple(Throw.parse(label) for label in labels)


def is_checkable(remaining: int, darts_left: int, out_rule: Rule) -> bool:
    """Whether `remaining` can be finished with `darts_left` darts under `out_rule`.

    False for anything the table has no entry for, which covers every score
    above the out-rule's ceiling (170 double, 180 master and straight), every
    score at or below zero, and 1 under a double or master out.
    """
    _validate_darts_left(darts_left)
    return (out_rule.value, darts_left, remaining) in CHECKOUTS


def suggest(
    remaining: int, darts_left: int, out_rule: Rule, n: int = MAX_SUGGESTIONS
) -> list[tuple[Throw, ...]]:
    """The best checkout paths for this score, best first.

    Returns an empty list when the score is not checkable. `n` is clamped to
    `MAX_SUGGESTIONS`, since that is how many paths the table stores; asking for
    more is not an error, it just cannot be honoured.
    """
    _validate_darts_left(darts_left)
    if n < 1:
        return []

    paths = CHECKOUTS.get((out_rule.value, darts_left, remaining), ())
    return [_parse(path) for path in paths[:n]]


def was_checkout_attempt(remaining: int, out_rule: Rule) -> bool:
    """Whether a dart thrown at `remaining` counts as a checkout attempt.

    #7's definition: a dart is an attempt iff the score immediately before it
    was a genuine one-dart finish. Aim is not observable, so this is the
    objective, reproducible measure, and it is how mainstream scoring apps
    compute checkout percentage.
    """
    return is_checkable(remaining, 1, out_rule)
