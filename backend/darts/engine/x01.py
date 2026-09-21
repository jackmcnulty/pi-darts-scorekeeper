"""x01 scoring: in-rules, out-rules and bust handling for 301, 501, 701 and friends.

Pure functions over frozen state. Nothing here knows about players, turn order,
databases or clocks — a caller hands in a state and some throws and gets a new
state back. Turn rotation is #10's problem; checkout suggestions are #7's.

The one rule worth reading twice is the bust: it voids the **entire visit**, not
just the dart that caused it. See `apply_visit`.
"""

from dataclasses import dataclass, replace
from enum import StrEnum

from darts.engine.throws import Throw


class Rule(StrEnum):
    """How a team is allowed to start scoring, and how they are allowed to finish."""

    STRAIGHT = "straight"
    DOUBLE = "double"
    MASTER = "master"


class BustReason(StrEnum):
    """Why a visit was voided."""

    #: The dart scored more than was left.
    BELOW_ZERO = "below_zero"
    #: The dart landed on exactly zero but was not a legal finish.
    WRONG_FINISH = "wrong_finish"
    #: The dart left 1, which cannot be finished on a double or a master out.
    LEFT_ONE = "left_one"


@dataclass(frozen=True)
class X01Config:
    """The rules of a leg. `start` is any positive number, not just 301/501/701."""

    start: int
    in_rule: Rule
    out_rule: Rule

    def __post_init__(self) -> None:
        if self.start <= 0:
            raise ValueError(f"start must be positive; got {self.start!r}")


@dataclass(frozen=True)
class X01TeamState:
    """Where one team stands in a leg.

    `remaining` counts down to 0. `is_open` records whether the in-rule has been
    satisfied — under `STRAIGHT` that is true from the outset. `darts` is the
    running count of darts thrown, which includes darts that scored nothing.
    """

    remaining: int
    is_open: bool
    darts: int

    def __post_init__(self) -> None:
        if self.remaining < 0:
            raise ValueError(f"remaining cannot be negative; got {self.remaining!r}")
        if self.darts < 0:
            raise ValueError(f"darts cannot be negative; got {self.darts!r}")

    @property
    def is_finished(self) -> bool:
        """True once the leg has been checked out."""
        return self.remaining == 0


@dataclass(frozen=True)
class DartOutcome:
    """What one dart did.

    `counted` means the dart's score was applied to `remaining`. It is False for
    darts thrown before the in-rule is satisfied, for a dart that busts, and —
    after `apply_visit` has voided a visit — for every dart in that visit.
    """

    throw: Throw
    state: X01TeamState
    counted: bool
    bust: BustReason | None
    checkout: bool


@dataclass(frozen=True)
class VisitOutcome:
    """What a whole visit did, after any bust has been applied.

    `score_before` and `score_after` are equal whenever `bust` is set; that is
    the entire point of a bust.
    """

    state: X01TeamState
    darts: tuple[DartOutcome, ...]
    bust: BustReason | None
    checkout: bool
    score_before: int
    score_after: int

    @property
    def is_bust(self) -> bool:
        return self.bust is not None


def _satisfies(rule: Rule, throw: Throw) -> bool:
    """Whether `throw` meets `rule`, as an in-rule or an out-rule.

    The two use identical predicates. The inner bull is 25 doubled, so it
    qualifies under both `DOUBLE` and `MASTER`; the outer bull is a single and
    qualifies under neither.
    """
    if rule is Rule.STRAIGHT:
        return True
    if rule is Rule.DOUBLE:
        return throw.is_double
    return throw.is_double or throw.is_triple


def initial_state(cfg: X01Config) -> X01TeamState:
    """A team at the start of a leg, on `cfg.start` with no darts thrown.

    Under a straight in-rule the team is open immediately; otherwise they must
    earn it. Not named in #6, but a leg has to start somewhere and every caller
    would otherwise rebuild this by hand.
    """
    return X01TeamState(
        remaining=cfg.start,
        is_open=cfg.in_rule is Rule.STRAIGHT,
        darts=0,
    )


def apply_dart(state: X01TeamState, throw: Throw, cfg: X01Config) -> DartOutcome:
    """Apply a single dart.

    A dart that busts leaves `remaining` and `is_open` exactly as it found them
    and reports the reason; reverting the *rest* of the visit is `apply_visit`'s
    job, since a single dart cannot know what came before it.
    """
    if state.is_finished:
        raise ValueError("the leg is already won; no further darts may be thrown")

    after_throwing = replace(state, darts=state.darts + 1)

    # Before the in-rule is satisfied, darts are thrown but score nothing. The
    # dart that satisfies it both opens the team and scores.
    if not state.is_open and not _satisfies(cfg.in_rule, throw):
        return DartOutcome(
            throw=throw, state=after_throwing, counted=False, bust=None, checkout=False
        )

    new_remaining = state.remaining - throw.score
    bust = _bust_reason(new_remaining, throw, cfg)

    if bust is not None:
        # The dart is void: the score it would have made, and the opening it
        # would have earned, both evaporate.
        return DartOutcome(
            throw=throw, state=after_throwing, counted=False, bust=bust, checkout=False
        )

    return DartOutcome(
        throw=throw,
        state=X01TeamState(remaining=new_remaining, is_open=True, darts=after_throwing.darts),
        counted=True,
        bust=None,
        checkout=new_remaining == 0,
    )


def _bust_reason(new_remaining: int, throw: Throw, cfg: X01Config) -> BustReason | None:
    """The bust checks, in the order #6 specifies. Order matters: the zero cases
    are decided before the `left_one` case, so they can never collide."""
    if new_remaining < 0:
        return BustReason.BELOW_ZERO
    if new_remaining == 0 and not _satisfies(cfg.out_rule, throw):
        return BustReason.WRONG_FINISH
    if new_remaining == 1 and cfg.out_rule is not Rule.STRAIGHT:
        return BustReason.LEFT_ONE
    return None


def apply_visit(state: X01TeamState, throws: tuple[Throw, ...], cfg: X01Config) -> VisitOutcome:
    """Apply a visit's darts in order, stopping at a bust or a checkout.

    On a bust the whole visit is undone: `remaining` and `is_open` go back to
    what they were when the visit began, and every dart already thrown is
    reported with `counted=False` — including darts that scored perfectly well
    before the offending one. The darts themselves are *not* undone; they still
    count toward `darts`, because they really were thrown.

    Darts after the stopping dart are never thrown, so they get no outcome and
    do not count toward `darts`.

    Any number of darts is accepted. Enforcing three-to-a-visit is turn
    structure, which belongs to #10.
    """
    outcomes: list[DartOutcome] = []
    current = state
    bust: BustReason | None = None
    checkout = False

    for throw in throws:
        outcome = apply_dart(current, throw, cfg)
        outcomes.append(outcome)
        current = outcome.state

        if outcome.bust is not None:
            bust = outcome.bust
            break
        if outcome.checkout:
            checkout = True
            break

    if bust is not None:
        # Rewind to the start of the visit, keeping only the darts thrown. Each
        # dart keeps its own running dart count — the throws happened — but the
        # score and the opening at every point in the visit are the ones the
        # visit began with.
        current = replace(state, darts=current.darts)
        outcomes = [
            replace(o, counted=False, state=replace(state, darts=o.state.darts)) for o in outcomes
        ]

    return VisitOutcome(
        state=current,
        darts=tuple(outcomes),
        bust=bust,
        checkout=checkout,
        score_before=state.remaining,
        score_after=current.remaining,
    )
