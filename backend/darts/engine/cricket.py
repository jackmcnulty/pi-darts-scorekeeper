"""American Cricket: mark accounting, surplus handling and the win, in three variants.

Pure functions over frozen state, in the same style as `darts.engine.x01`.
Nothing here knows about players, turn order, databases or clocks. Rotation,
replay and undo are #10.

Mark accounting is **identical** in all three variants — a dart contributes
`multiplier` marks, a target closes at three, the rest is surplus. Only what
happens to the surplus, and what counts as a win, differ:

===========  ==========================================  ==========================
Variant      Surplus marks                               Win
===========  ==========================================  ==========================
`standard`   `target x surplus` to the thrower, if at     closed all, and
             least one opponent still has it open         `points >= max(others)`
`cutthroat`  `target x surplus` to *each* opponent that   closed all, and
             has not closed it, in full and separately    `points <= min(others)`
`quick`      wasted entirely                              closed all
===========  ==========================================  ==========================

Three rules are worth reading twice, because they are the ones that
implementations get wrong:

* **Dead targets.** Surplus only pays out while at least one *opponent* still
  has the target open. Once everybody has closed it there is nobody to score
  against, and the marks are wasted — they still cap at three, they just buy
  nothing. `standard` and `cutthroat` share this; `quick` wastes every surplus
  mark regardless.
* **Closing is not winning.** Under `standard` and `cutthroat`, a team that has
  closed all seven targets on the wrong side of the points has not won. The leg
  continues. Only `quick` ends on the closing dart.
* **Cut-throat points travel.** A cut-throat dart pays *opponents*, never the
  thrower, and the cut-throat win condition reads the opponents' points. So a
  team can win by giving points away, and the opponents' totals must be
  threaded from dart to dart within a visit. `apply_visit` does this; see
  `PointEvent`.

Unlike x01, cricket has no bust, so a visit is never voided.

On the shape of these functions
-------------------------------
#8 originally asked for x01's signatures verbatim so the replay layer could
dispatch uniformly. It cannot be done: x01's `apply_dart(state, throw, cfg)`
sees exactly one team, and both rules above need the opponents' state. So the
first three parameters keep x01's order and meaning and an explicit
`opponents` parameter carries the rest. In particular `cfg` still means
*configuration* — nothing that changes from dart to dart is smuggled into it.
The ticket was amended to match before this landed.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from darts.engine.throws import Throw

#: The seven cricket targets, in the order they appear on a scoreboard.
TARGETS: Final[tuple[int, ...]] = (20, 19, 18, 17, 16, 15, 25)

#: Marks needed to close a target. Marks beyond this are surplus.
MARKS_TO_CLOSE: Final = 3

_TARGET_INDEX: Final[Mapping[int, int]] = {target: i for i, target in enumerate(TARGETS)}


class Variant(StrEnum):
    """Which cricket is being played. Mark accounting is the same in all three."""

    #: Surplus scores for the thrower. Win by closing out in front.
    STANDARD = "standard"
    #: Surplus is given away to opponents. Win by closing out behind.
    CUTTHROAT = "cutthroat"
    #: Surplus scores for nobody. First team to close out wins.
    QUICK = "quick"


@dataclass(frozen=True, slots=True)
class CricketConfig:
    """The rules of a cricket leg.

    `variant` is the only setting, and it defaults to `STANDARD` so that
    `CricketConfig()` still means what it meant in #8. `initial_state(cfg)` and
    the `(state, throw, cfg)` prefix match x01, which is what lets #10 dispatch
    on a game type rather than special-casing one.
    """

    variant: Variant = Variant.STANDARD


@dataclass(frozen=True, slots=True)
class PointEvent:
    """Points awarded by one dart to one team.

    A dart produces one event per *recipient*, which is what lets #11/#15 later
    record exactly who received what from which dart. `recipient` is `None` for
    the throwing team and otherwise indexes the `opponents` tuple that was
    passed in — the engine has no notion of team identity, which is #10's.

    The three variants differ only in who ends up on the receiving end:
    `standard` emits at most one event, to the thrower; `cutthroat` emits one
    per opponent that still has the target open; `quick` emits none, ever.
    """

    recipient: int | None
    points: int

    def __post_init__(self) -> None:
        if self.recipient is not None and self.recipient < 0:
            raise ValueError(f"recipient must be None or an index; got {self.recipient!r}")
        if self.points <= 0:
            raise ValueError(f"a point event must award something; got {self.points!r}")


@dataclass(frozen=True, slots=True)
class CricketTeamState:
    """Where one team stands in a leg.

    `marks` is positionally aligned to `TARGETS` — `marks[0]` is the team's
    marks on 20 — and every entry is 0..3. #8 wrote this field as a
    `Mapping[int, int]`, which would have made the dataclass unhashable and
    mutable through the field, unlike every other engine state. A tuple keeps
    it frozen and hashable like `Throw` and `X01TeamState`; `marks_by_target`
    gives callers the mapping view the ticket was reaching for.

    There is no dart counter here. x01 needs one only because #6 asked for it;
    no cricket rule depends on how many darts have been thrown, and counting
    them is turn structure, which is #10's.
    """

    marks: tuple[int, ...]
    points: int

    def __post_init__(self) -> None:
        if len(self.marks) != len(TARGETS):
            raise ValueError(f"marks must have one entry per target; got {self.marks!r}")
        if any(not 0 <= m <= MARKS_TO_CLOSE for m in self.marks):
            raise ValueError(f"marks must each be 0..{MARKS_TO_CLOSE}; got {self.marks!r}")
        if self.points < 0:
            raise ValueError(f"points cannot be negative; got {self.points!r}")

    @property
    def marks_by_target(self) -> Mapping[int, int]:
        """The marks as a target -> count mapping, for callers and displays.

        A fresh dict each call, so mutating it cannot reach back into the state.
        """
        return dict(zip(TARGETS, self.marks, strict=True))

    def marks_on(self, target: int) -> int:
        """Marks on one target. Raises for anything that is not a cricket target."""
        return self.marks[_index_of(target)]

    def has_closed(self, target: int) -> bool:
        return self.marks_on(target) >= MARKS_TO_CLOSE

    @property
    def has_closed_all(self) -> bool:
        """True once all seven targets are closed. On its own this is not a win."""
        return all(m >= MARKS_TO_CLOSE for m in self.marks)


@dataclass(frozen=True, slots=True)
class DartOutcome:
    """What one dart did.

    `target` is `None` for a dart that did not land on a cricket target — a 12,
    or a miss — in which case every other count is zero and `state` is the
    state that was passed in.

    `counted_marks` went toward closing and `surplus_marks` did not; together
    they always equal the throw's multiplier. `wasted` is True exactly when
    there were surplus marks that paid nobody — a dead target under `standard`
    or `cutthroat`, and any surplus at all under `quick`.

    `points` is what the *throwing* team gained, which under `cutthroat` is
    always zero however much the dart gave away; `point_events` is the full
    picture and `opponents` is those events already applied, so the caller does
    not have to fold them itself.
    """

    throw: Throw
    state: CricketTeamState
    opponents: tuple[CricketTeamState, ...]
    target: int | None
    counted_marks: int
    surplus_marks: int
    points: int
    point_events: tuple[PointEvent, ...]
    wasted: bool
    win: bool

    @property
    def points_conceded(self) -> int:
        """Points this dart handed to opponents. Non-zero only under `cutthroat`."""
        return sum(e.points for e in self.point_events if e.recipient is not None)


@dataclass(frozen=True, slots=True)
class VisitOutcome:
    """What a whole visit did.

    There is no bust in cricket, so unlike x01's equivalent this can never
    report the visit as voided; `points_after` is never less than
    `points_before` in any variant, because no variant takes points away from
    the team that threw.

    `opponents` is their state at the end of the visit, which under `cutthroat`
    is not the tuple that was passed in — the visit's own darts will have paid
    them. `point_events` is every event from every dart, in throwing order.
    """

    state: CricketTeamState
    opponents: tuple[CricketTeamState, ...]
    darts: tuple[DartOutcome, ...]
    point_events: tuple[PointEvent, ...]
    win: bool
    points_before: int
    points_after: int


def _index_of(target: int) -> int:
    index = _TARGET_INDEX.get(target)
    if index is None:
        raise ValueError(f"not a cricket target: {target!r}")
    return index


def _is_dead(target: int, opponents: tuple[CricketTeamState, ...]) -> bool:
    """Whether there is anybody left to score against on `target`.

    A target is dead once every opposing team has closed it. Surplus only
    exists for a team that has closed the target itself, so "every opponent has
    closed it" and #8's "every team has closed it" say the same thing.

    With no opponents at all this is vacuously True and nothing ever scores.
    That is the honest reading of the rule rather than a special case: cricket
    is a game against somebody, and a caller who omits `opponents` is
    describing a board with no live targets on it.
    """
    return all(opponent.has_closed(target) for opponent in opponents)


def _has_won(
    state: CricketTeamState, opponents: tuple[CricketTeamState, ...], cfg: CricketConfig
) -> bool:
    """Closing every target is necessary in all three variants, and on its own
    it is sufficient only in `quick`. The other two then compare points, in
    opposite directions: `standard` wants the most, `cutthroat` the fewest."""
    if not state.has_closed_all:
        return False
    if cfg.variant is Variant.QUICK:
        return True
    if cfg.variant is Variant.CUTTHROAT:
        return all(state.points <= o.points for o in opponents)
    return all(state.points >= o.points for o in opponents)


def _surplus_events(
    target: int,
    surplus: int,
    cfg: CricketConfig,
    opponents: tuple[CricketTeamState, ...],
) -> tuple[PointEvent, ...]:
    """Who gets paid for `surplus` marks on `target`, and how much.

    Cut-throat gives every still-open opponent the **full** amount rather than
    a share of it, which is the rule's whole character: three surplus marks on
    20 against two open opponents cost 60 points each, not 30.
    """
    if surplus <= 0:
        return ()

    points = target * surplus

    if cfg.variant is Variant.QUICK:
        return ()
    if cfg.variant is Variant.CUTTHROAT:
        return tuple(
            PointEvent(recipient=i, points=points)
            for i, opponent in enumerate(opponents)
            if not opponent.has_closed(target)
        )
    if _is_dead(target, opponents):
        return ()
    return (PointEvent(recipient=None, points=points),)


def _award(
    opponents: tuple[CricketTeamState, ...], events: tuple[PointEvent, ...]
) -> tuple[CricketTeamState, ...]:
    """Apply the opponent-facing events, leaving untouched teams identical."""
    gained = [0] * len(opponents)
    for event in events:
        if event.recipient is not None:
            gained[event.recipient] += event.points

    return tuple(
        replace(opponent, points=opponent.points + g) if g else opponent
        for opponent, g in zip(opponents, gained, strict=True)
    )


def initial_state(cfg: CricketConfig) -> CricketTeamState:
    """A team at the start of a leg: no marks anywhere, no points.

    `cfg` is unused for all variants and is taken anyway to match x01's
    entry point, so callers do not have to know which game they are setting up.
    """
    return CricketTeamState(marks=(0,) * len(TARGETS), points=0)


def apply_dart(
    state: CricketTeamState,
    throw: Throw,
    cfg: CricketConfig,
    opponents: tuple[CricketTeamState, ...] = (),
) -> DartOutcome:
    """Apply a single dart.

    A dart at a non-target is legal and returns the state untouched. A dart at
    a target adds `throw.multiplier` marks, capped at three; the surplus then
    pays whoever the variant says it pays, which may be nobody and under
    `cutthroat` is never the thrower.
    """
    if _has_won(state, opponents, cfg):
        raise ValueError("the leg is already won; no further darts may be thrown")

    index = _TARGET_INDEX.get(throw.segment)
    if index is None:
        # A 12, a 3, or a miss. Legal, representable, and changes nothing.
        return DartOutcome(
            throw=throw,
            state=state,
            opponents=opponents,
            target=None,
            counted_marks=0,
            surplus_marks=0,
            points=0,
            point_events=(),
            wasted=False,
            win=False,
        )

    target = throw.segment
    counted = min(throw.multiplier, MARKS_TO_CLOSE - state.marks[index])
    surplus = throw.multiplier - counted

    events = _surplus_events(target, surplus, cfg, opponents)
    points = sum(e.points for e in events if e.recipient is None)

    new_marks = list(state.marks)
    new_marks[index] += counted
    new_state = CricketTeamState(marks=tuple(new_marks), points=state.points + points)
    new_opponents = _award(opponents, events)

    return DartOutcome(
        throw=throw,
        state=new_state,
        opponents=new_opponents,
        target=target,
        counted_marks=counted,
        surplus_marks=surplus,
        points=points,
        point_events=events,
        # Surplus that paid nobody: a dead target, or any surplus under `quick`.
        wasted=surplus > 0 and not events,
        win=_has_won(new_state, new_opponents, cfg),
    )


def apply_visit(
    state: CricketTeamState,
    throws: tuple[Throw, ...],
    cfg: CricketConfig,
    opponents: tuple[CricketTeamState, ...] = (),
) -> VisitOutcome:
    """Apply a visit's darts in order, stopping at a win.

    The opponents' *marks* cannot change mid-visit — they are not the ones
    throwing — but under `cutthroat` their *points* can, because the darts
    being applied here are what pays them. Those updated totals are threaded
    into the next dart rather than the caller's originals, which matters: the
    cut-throat win condition reads the opponents' points, so a dart can put the
    thrower over the line precisely by giving points away. Evaluating that
    against stale totals would miss the win.

    A win ends the visit, the way a checkout does in x01: darts after it are
    never thrown and get no outcome. Further calls to `apply_dart` on the
    winning position raise `ValueError`.

    Any number of darts is accepted. Enforcing three-to-a-visit is turn
    structure, which belongs to #10.
    """
    outcomes: list[DartOutcome] = []
    current = state
    current_opponents = opponents
    win = False

    for throw in throws:
        outcome = apply_dart(current, throw, cfg, current_opponents)
        outcomes.append(outcome)
        current = outcome.state
        current_opponents = outcome.opponents

        if outcome.win:
            win = True
            break

    return VisitOutcome(
        state=current,
        opponents=current_opponents,
        darts=tuple(outcomes),
        point_events=tuple(e for outcome in outcomes for e in outcome.point_events),
        win=win,
        points_before=state.points,
        points_after=current.points,
    )
