"""The play screen's whole conversation with the server: one read and two writes.

Every response here is the *same* complete state, so a client that has recorded
a dart never has to ask what happened next. That is the shape the endpoints are
built around and the reason `POST /darts` returns a match state rather than a
receipt: a round trip on the Pi is cheap, but a second round trip the client has
to know to make is a race, and a scoreboard that repaints from two responses can
show a score from one and a thrower from the other.

Two legs, not one
-----------------
`current_leg` is the leg that was addressed, shown complete with its winner if
the last dart won it. `active_leg` is where the next dart goes when that is a
*different* leg -- which happens for exactly one response, the one that reports
a leg being won. Both are full states, so the client can paint the finished leg,
show it, and start the next one without asking again. `active_leg` is None the
rest of the time, including when it would merely repeat `current_leg`.

Abandoned matches read, but offer nothing
-----------------------------------------
`services.play` still projects the last unfinished leg of an abandoned match as
active, which is true of the rows -- that leg exists and its darts are worth
reading. It is not true of the *game*, and #17 already refuses every dart and
undo aimed at one. So this layer reports `status` and then withholds everything
actionable: no `active_leg_id`, no `active_leg`, no `next_thrower`, no hints.
Scores, marks, visits and the tally all still read, because looking at a match
somebody walked away from is the point of keeping it.

Nothing here decides a rule. `services.play` owns scoring, rotation, busts,
undo, advancement and its own transactions; this module validates a payload,
calls one function, and maps frozen dataclasses onto the wire.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from darts.api.deps import ConnectionDep
from darts.engine.throws import ALL_THROWS, Throw
from darts.repo.config import GameConfig
from darts.repo.errors import NotFoundError
from darts.repo.matches import MatchStatus, get_match
from darts.services import hints, play
from darts.services import state as public

router = APIRouter(prefix="/api", tags=["play"])

MatchId = Annotated[int, Path(gt=0)]
LegId = Annotated[int, Path(gt=0)]

#: The 63 legal throws, indexed the three ways the request model asks about
#: them. `ALL_THROWS` is the definition; deriving these from it is what stops
#: the API and the engine disagreeing about what a dart may be.
_LEGAL_SEGMENTS = frozenset(throw.segment for throw in ALL_THROWS)
_LEGAL_MULTIPLIERS = frozenset(throw.multiplier for throw in ALL_THROWS)
_LEGAL_THROWS = frozenset((throw.segment, throw.multiplier) for throw in ALL_THROWS)


class DartWrite(BaseModel):
    """One dart as a client reports it.

    Validated here rather than by letting `Throw.__post_init__` raise inside the
    endpoint: a `ValueError` escaping a handler is a 500, whereas a refusal
    during request parsing is the field-level 422 the client can act on. By the
    time the endpoint runs, `Throw(segment, multiplier)` cannot fail.

    `client_dart_id` is opaque text the client mints per dart, not a UUID --
    the column is a unique TEXT and nothing needs it to be more than that.
    It must be non-blank, since whitespace is not an identity.
    """

    model_config = ConfigDict(extra="forbid")

    segment: int
    multiplier: int
    client_dart_id: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("segment")
    @classmethod
    def _check_segment(cls, value: int) -> int:
        if value not in _LEGAL_SEGMENTS:
            raise ValueError("segment must be 0 (a miss), 1..20, or 25 (either bull ring)")
        return value

    @field_validator("multiplier")
    @classmethod
    def _check_multiplier(cls, value: int) -> int:
        if value not in _LEGAL_MULTIPLIERS:
            raise ValueError("multiplier must be 0 (a miss), 1, 2, or 3")
        return value

    @field_validator("client_dart_id")
    @classmethod
    def _check_client_dart_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("client_dart_id must not be blank")
        return value

    @model_validator(mode="after")
    def _check_combination(self) -> "DartWrite":
        """The pairs no single field can refuse: triple bull, and half a miss."""
        if (self.segment, self.multiplier) not in _LEGAL_THROWS:
            raise ValueError(f"no such throw: segment={self.segment}, multiplier={self.multiplier}")
        return self

    @property
    def throw(self) -> Throw:
        return Throw(self.segment, self.multiplier)


class ThrowerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    team_id: int
    player_id: int
    display_name: str


class DartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    dart_id: int
    dart_index: int
    segment: int
    multiplier: int
    #: "T20", "BULL", "MISS" -- the string a scoreboard prints.
    label: str
    counted: bool
    caused_bust: bool
    was_checkout_attempt: bool


class VisitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    visit_id: int
    team_id: int
    player_id: int
    visit_index: int
    #: x01 remaining in an x01 leg, the throwing team's points in a cricket one.
    score_before: int
    score_after: int
    is_bust: bool
    is_complete: bool
    darts: list[DartResponse]


class TeamLegResponse(BaseModel):
    """One team's position. x01 fills `remaining`/`is_open`, cricket fills `marks`."""

    model_config = ConfigDict(from_attributes=True)
    team_id: int
    remaining: int | None
    is_open: bool | None
    darts_thrown: int
    points: int
    #: Marks per target, keyed by number (15..20 and 25). None in an x01 leg.
    marks: dict[int, int] | None
    #: This leg's 3-dart average for this team, computed as #19 computes one.
    #: None before the team's first dart -- an average of no darts does not
    #: exist, and 0 would read as a bad one -- and None throughout cricket.
    three_dart_average: float | None


class CheckoutResponse(BaseModel):
    """Finishes for whoever throws next, or why there are none.

    `paths` is empty exactly when `reason` is set, so a client never has to
    decide which of the two to believe.
    """

    model_config = ConfigDict(from_attributes=True)
    team_id: int | None
    player_id: int | None
    remaining: int | None
    darts_left: int
    paths: list[list[str]]
    reason: hints.NoHintsReason | None


class LegStateResponse(BaseModel):
    """A leg, everything a board draws from it, and its hints."""

    model_config = ConfigDict(from_attributes=True)
    leg_id: int
    leg_index: int
    starting_team_id: int
    winner_team_id: int | None
    is_complete: bool
    darts_thrown: int
    darts_left: int
    next_thrower: ThrowerResponse | None
    teams: list[TeamLegResponse]
    #: The visit being thrown, or None when the last one finished.
    current_visit: VisitResponse | None
    #: The last visit that finished -- the recap line. Never from another leg.
    previous_visit: VisitResponse | None
    checkout: CheckoutResponse


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    player_id: int
    member_index: int
    display_name: str
    is_archived: bool


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    team_index: int
    name: str | None
    is_solo: bool
    members: list[MemberResponse]


class MatchStateResponse(BaseModel):
    """The one fat read, and what every write returns."""

    model_config = ConfigDict(from_attributes=True)
    match_id: int
    config: GameConfig
    status: MatchStatus
    teams: list[TeamResponse]
    #: Legs won, positional to `teams`.
    legs_won: list[int]
    winner_team_id: int | None
    is_complete: bool
    current_leg: LegStateResponse
    #: Where the next dart goes. None for a won or abandoned match.
    active_leg_id: int | None
    #: The active leg in full, when it is not `current_leg`. See the module docstring.
    active_leg: LegStateResponse | None


def _leg_response(config: GameConfig, leg: public.LegState, *, abandoned: bool) -> LegStateResponse:
    """One leg on the wire, with the hints it implies.

    Built field by field rather than validated straight off the dataclass,
    because `checkout` is not on it: the hint is derived from the leg, not
    stored with it, and an abandoned match withholds the thrower besides.
    """
    return LegStateResponse(
        leg_id=leg.leg_id,
        leg_index=leg.leg_index,
        starting_team_id=leg.starting_team_id,
        winner_team_id=leg.winner_team_id,
        is_complete=leg.is_complete,
        darts_thrown=leg.darts_thrown,
        darts_left=leg.darts_left,
        # Readable, but nothing to act on: see the module docstring.
        next_thrower=(
            None
            if abandoned or leg.next_thrower is None
            else ThrowerResponse.model_validate(leg.next_thrower)
        ),
        teams=[TeamLegResponse.model_validate(team) for team in leg.teams],
        current_visit=(
            None if leg.current_visit is None else VisitResponse.model_validate(leg.current_visit)
        ),
        previous_visit=(
            None if leg.previous_visit is None else VisitResponse.model_validate(leg.previous_visit)
        ),
        checkout=CheckoutResponse.model_validate(hints.for_leg(config, leg, abandoned=abandoned)),
    )


def _state_response(conn: sqlite3.Connection, state: public.GameState) -> MatchStateResponse:
    """Map a projected `GameState` onto the wire, resolving the second leg.

    The extra `play.state` call is the leg the last dart opened, and it is made
    for one response in a match: the one reporting a leg win. That leg has no
    darts yet, so replaying it is reading a handful of rows.
    """
    abandoned = state.status is MatchStatus.ABANDONED
    active_id = None if abandoned else state.active_leg_id
    active = (
        play.state(conn, active_id).current_leg
        if active_id is not None and active_id != state.current_leg.leg_id
        else None
    )
    return MatchStateResponse(
        match_id=state.match_id,
        config=state.config,
        status=state.status,
        teams=[TeamResponse.model_validate(team) for team in state.teams],
        legs_won=list(state.legs_won),
        winner_team_id=state.winner_team_id,
        is_complete=state.is_complete,
        current_leg=_leg_response(state.config, state.current_leg, abandoned=abandoned),
        active_leg_id=active_id,
        active_leg=(
            None if active is None else _leg_response(state.config, active, abandoned=False)
        ),
    )


@router.get("/matches/{match_id}/state", response_model=MatchStateResponse)
def get_match_state(match_id: MatchId, conn: ConnectionDep) -> MatchStateResponse:
    """Everything the play screen needs, addressed by match rather than by leg.

    The leg it reports is the match's latest, which is #17's `current_leg_id`
    and the same leg the match detail route names. For a match in progress that
    is the leg being played; for a finished one it is the leg that won it, shown
    complete; for an abandoned one it is wherever play stopped. `play.state`
    takes a *leg* id, so resolving the match first is not optional.
    """
    match = get_match(conn, match_id)
    if match.current_leg_id is None:  # pragma: no cover - create_match always opens leg 0
        raise NotFoundError(f"match {match_id} has no legs")
    return _state_response(conn, play.state(conn, match.current_leg_id))


@router.post("/legs/{leg_id}/darts", response_model=MatchStateResponse)
def record_dart(leg_id: LegId, payload: DartWrite, conn: ConnectionDep) -> MatchStateResponse:
    """Record one dart and return the whole new state.

    200 rather than 201: the response is the state of the match, not the dart
    row, and a retry of a dart already recorded has created nothing. Answering
    201 to a request that inserted no row would be a lie the client cannot
    check, and #18 asks for the retry to be a plain 200.

    Idempotent on `client_dart_id`: a key already recorded inserts nothing and
    returns the state *as it stands now*. An immediate retry therefore returns
    exactly what the first call did. A retry sent after other darts have landed
    returns the newer state, which is the honest answer -- the service does not
    store responses and will not replay history. The same key describing a
    different dart, or aimed at a different leg, is a 409 rather than somebody
    else's throw.
    """
    return _state_response(
        conn,
        play.throw(conn, leg_id=leg_id, dart=payload.throw, client_dart_id=payload.client_dart_id),
    )


@router.post("/legs/{leg_id}/undo", response_model=MatchStateResponse)
def undo_dart(leg_id: LegId, conn: ConnectionDep) -> MatchStateResponse:
    """Delete the leg's last dart and return the state that leaves.

    409 for a leg with nothing to undo, for a later leg already played into,
    and for an abandoned match.
    """
    return _state_response(conn, play.undo(conn, leg_id))


@router.get("/legs/{leg_id}/checkout", response_model=CheckoutResponse)
def get_checkout(leg_id: LegId, conn: ConnectionDep) -> hints.Hints:
    """The same hints `/state` embeds, on their own, for testing and debugging.

    Nothing in the app needs this -- the fat read already carries it -- but a
    hint that can only be seen inside a hundred-line payload is a hint that is
    hard to be sure about.
    """
    state = play.state(conn, leg_id)
    return hints.for_leg(
        state.config, state.current_leg, abandoned=state.status is MatchStatus.ABANDONED
    )
