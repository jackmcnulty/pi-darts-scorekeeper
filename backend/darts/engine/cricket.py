"""Standard (scoring) American Cricket: mark accounting, surplus scoring and the win.

Pure functions over frozen state, in the same style as `darts.engine.x01`.
Nothing here knows about players, turn order, databases or clocks. Cut-throat
and quick variants are #9; rotation, replay and undo are #10.

Two rules are worth reading twice, because they are the two that
implementations get wrong:

* **Dead targets.** Surplus marks only score while at least one *opponent*
  still has the target open. Once everybody has closed it, the target is dead
  and further marks are wasted — they still cap at three, they just buy
  nothing.
* **Closing is not winning.** A team that has closed all seven targets while
  trailing on points has not won. The leg continues, and they have to outscore
  the leader on targets that are still live before it ends.

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
from dataclasses import dataclass
from typing import Final

from darts.engine.throws import Throw

#: The seven cricket targets, in the order they appear on a scoreboard.
TARGETS: Final[tuple[int, ...]] = (20, 19, 18, 17, 16, 15, 25)

#: Marks needed to close a target. Marks beyond this are surplus.
MARKS_TO_CLOSE: Final = 3

_TARGET_INDEX: Final[Mapping[int, int]] = {target: i for i, target in enumerate(TARGETS)}


@dataclass(frozen=True, slots=True)
class CricketConfig:
    """The rules of a standard cricket leg.

    Deliberately empty: standard cricket has nothing to configure. It exists so
    that `initial_state(cfg)` and the `(state, throw, cfg)` prefix match x01,
    which is what lets #10 dispatch on a game type rather than special-casing
    one. #9's variants are what will give it fields.
    """


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
    there were surplus marks and the target was already dead, which is the case
    #8 asks to be flagged rather than silently scored as nothing.
    """

    throw: Throw
    state: CricketTeamState
    target: int | None
    counted_marks: int
    surplus_marks: int
    points: int
    wasted: bool
    win: bool


@dataclass(frozen=True, slots=True)
class VisitOutcome:
    """What a whole visit did.

    There is no bust in cricket, so unlike x01's equivalent this can never
    report the visit as voided; `points_after` is never less than
    `points_before`.
    """

    state: CricketTeamState
    darts: tuple[DartOutcome, ...]
    win: bool
    points_before: int
    points_after: int


def _index_of(target: int) -> int:
    index = _TARGET_INDEX.get(target)
    if index is None:
        raise ValueError(f"not a cricket target: {target!r}")
    return index


def _is_dead(target: int, opponents: tuple[CricketTeamState, ...]) -> bool:
    """Whether surplus marks on `target` are worthless.

    A target is dead once every opposing team has closed it. Surplus only
    exists for a team that has closed the target itself, so "every opponent has
    closed it" and #8's "every team has closed it" say the same thing.

    With no opponents at all this is vacuously True and nothing ever scores.
    That is the honest reading of the rule rather than a special case: standard
    cricket is a game against somebody, and a caller who omits `opponents` is
    describing a board with no live targets on it.
    """
    return all(opponent.has_closed(target) for opponent in opponents)


def _has_won(state: CricketTeamState, opponents: tuple[CricketTeamState, ...]) -> bool:
    """All seven closed *and* not behind on points. Both halves are required."""
    return state.has_closed_all and all(state.points >= o.points for o in opponents)


def initial_state(cfg: CricketConfig) -> CricketTeamState:
    """A team at the start of a leg: no marks anywhere, no points.

    `cfg` is unused for standard cricket and is taken anyway to match x01's
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
    a target adds `throw.multiplier` marks, capped at three, and scores
    `target x surplus` to the throwing team unless the target is dead.
    """
    if _has_won(state, opponents):
        raise ValueError("the leg is already won; no further darts may be thrown")

    index = _TARGET_INDEX.get(throw.segment)
    if index is None:
        # A 12, a 3, or a miss. Legal, representable, and changes nothing.
        return DartOutcome(
            throw=throw,
            state=state,
            target=None,
            counted_marks=0,
            surplus_marks=0,
            points=0,
            wasted=False,
            win=False,
        )

    target = throw.segment
    counted = min(throw.multiplier, MARKS_TO_CLOSE - state.marks[index])
    surplus = throw.multiplier - counted

    dead = _is_dead(target, opponents)
    points = 0 if dead else target * surplus

    new_marks = list(state.marks)
    new_marks[index] += counted
    new_state = CricketTeamState(marks=tuple(new_marks), points=state.points + points)

    return DartOutcome(
        throw=throw,
        state=new_state,
        target=target,
        counted_marks=counted,
        surplus_marks=surplus,
        points=points,
        wasted=surplus > 0 and dead,
        win=_has_won(new_state, opponents),
    )


def apply_visit(
    state: CricketTeamState,
    throws: tuple[Throw, ...],
    cfg: CricketConfig,
    opponents: tuple[CricketTeamState, ...] = (),
) -> VisitOutcome:
    """Apply a visit's darts in order, stopping at a win.

    The opponents cannot change mid-visit — they are not the ones throwing — so
    the same `opponents` applies to every dart.

    A win ends the visit, the way a checkout does in x01: darts after it are
    never thrown and get no outcome. #8 does not say so either way, but the
    alternative is a team throwing at a leg that is already over, and a caller
    that genuinely wants the rest can keep calling `apply_dart`.

    Any number of darts is accepted. Enforcing three-to-a-visit is turn
    structure, which belongs to #10.
    """
    outcomes: list[DartOutcome] = []
    current = state
    win = False

    for throw in throws:
        outcome = apply_dart(current, throw, cfg, opponents)
        outcomes.append(outcome)
        current = outcome.state

        if outcome.win:
            win = True
            break

    return VisitOutcome(
        state=current,
        darts=tuple(outcomes),
        win=win,
        points_before=state.points,
        points_after=current.points,
    )
