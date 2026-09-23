"""Checkout suggestions for a leg as it stands, and the reasons there are none.

`engine.checkout.suggest` answers a narrow arithmetic question -- can *this*
score be finished with *this* many darts under *this* out-rule -- by indexing a
committed table. Everything between that and "what should the play screen show"
lives here: which team is about to throw, whether the leg is still accepting
darts, and whether the rules let that team score at all yet.

An empty suggestion list is never bare. `Hints.reason` says which of the six
ways it came to be empty applied, so a client can tell "nothing left to do
here" from "185 is not a checkout" without re-deriving either, and so a test
can assert on the cause rather than on the absence.

Why an unopened team gets nothing
---------------------------------
The table is built from the out-rule alone. Under `double` or `master` *in*, a
team that has not opened scores nothing until it hits the opening ring, so a
path like T20/T20/BULL is not a checkout for them -- it is three darts that do
not count. Suggesting it would be worse than suggesting nothing, so an unopened
team gets `NOT_OPEN` and an empty list. Under the usual straight-in a team is
open from its first dart and this never fires.
"""

from dataclasses import dataclass
from enum import StrEnum

from darts.engine.checkout import suggest
from darts.repo.config import GameConfig, GameType
from darts.services.state import LegState


class NoHintsReason(StrEnum):
    """Why a leg has no checkout suggestions to offer."""

    #: Cricket is not checked out; it is closed out. The table does not apply.
    NOT_X01 = "not_x01"
    #: The leg has been won, so no further dart is going to finish it.
    LEG_COMPLETE = "leg_complete"
    #: The match was abandoned; it is readable, but nothing is actionable.
    MATCH_ABANDONED = "match_abandoned"
    #: Nobody is due to throw, so there is no score to suggest against.
    NO_THROWER = "no_thrower"
    #: An in-rule the throwing team has not satisfied yet; see the module docstring.
    NOT_OPEN = "not_open"
    #: A real score with no finish in the darts left -- 185, or 3 with one dart.
    NOT_CHECKABLE = "not_checkable"


@dataclass(frozen=True, slots=True)
class Hints:
    """What the thrower can finish on, or precisely why nothing is offered.

    `paths` holds throw labels -- ["T20", "T20", "BULL"] -- because that is
    what a scoreboard prints and what `Throw.label` already produces. Best
    first, as the table stores them.

    `reason` is None exactly when `paths` is non-empty, so the two cannot
    disagree about whether there is a hint.
    """

    team_id: int | None
    player_id: int | None
    remaining: int | None
    darts_left: int
    paths: tuple[tuple[str, ...], ...]
    reason: NoHintsReason | None


def _empty(reason: NoHintsReason, leg: LegState, *, name_thrower: bool = True) -> Hints:
    """No suggestions, and why.

    The thrower is still named for the refusals that are about the *score* --
    a cricket leg and an unopened team both have somebody at the oche, and a
    client still has a turn to display. An abandoned match does not: #18 does
    not advertise a thrower nobody may throw for, here or on `/state`.
    """
    thrower = leg.next_thrower if name_thrower else None
    return Hints(
        team_id=None if thrower is None else thrower.team_id,
        player_id=None if thrower is None else thrower.player_id,
        remaining=None,
        darts_left=leg.darts_left,
        paths=(),
        reason=reason,
    )


def for_leg(config: GameConfig, leg: LegState, *, abandoned: bool = False) -> Hints:
    """The suggestions for whoever throws next, or the reason there are none.

    Pure: it reads a replayed `LegState` and a config and touches no database.
    The order of the refusals is the order in which they stop mattering -- an
    abandoned match has no thrower worth naming even if the leg could otherwise
    offer one.
    """
    if config.game_type is not GameType.X01:
        return _empty(NoHintsReason.NOT_X01, leg)
    if abandoned:
        return _empty(NoHintsReason.MATCH_ABANDONED, leg, name_thrower=False)
    if leg.is_complete:
        return _empty(NoHintsReason.LEG_COMPLETE, leg)

    thrower = leg.next_thrower
    if thrower is None:
        return _empty(NoHintsReason.NO_THROWER, leg)

    team = next(row for row in leg.teams if row.team_id == thrower.team_id)
    if not team.is_open:
        return _empty(NoHintsReason.NOT_OPEN, leg)

    # `leg.darts_left` is 1..3 for any leg still accepting a dart, which is
    # what `suggest` requires; the refusals above are what guarantee it here.
    assert team.remaining is not None
    assert config.out_rule is not None
    paths = suggest(team.remaining, leg.darts_left, config.out_rule)
    return Hints(
        team_id=thrower.team_id,
        player_id=thrower.player_id,
        remaining=team.remaining,
        darts_left=leg.darts_left,
        paths=tuple(tuple(throw.label for throw in path) for path in paths),
        reason=None if paths else NoHintsReason.NOT_CHECKABLE,
    )
