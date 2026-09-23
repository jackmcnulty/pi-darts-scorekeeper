"""The public game state: everything a scoreboard needs, and nothing it does not.

Frozen dataclasses rather than pydantic models. The service layer's job is to
be right about darts, not to be a wire format; #18 owns the response schema and
maps these across. That keeps a change of API shape from reaching into game
logic, and keeps `GameState` describable without importing a web framework.

Identity is by database id throughout -- `team_id`, `player_id`, `leg_id` --
because those are what a client sends back. The engine's team *indices* never
surface; they are an implementation detail of the replay and stop at the edge
of `play`.

There are no checkout suggestions here. `TeamLegState` carries `remaining`,
`LegState` carries `darts_left` and `GameState.config` carries the out-rule,
which is everything `engine.checkout.suggest` needs; wiring it up is #20's.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from darts.repo.config import GameConfig
from darts.repo.matches import Team


@dataclass(frozen=True, slots=True)
class Thrower:
    """Who throws the next dart, resolved to real ids."""

    team_id: int
    player_id: int
    display_name: str


@dataclass(frozen=True, slots=True)
class DartState:
    """One recorded dart, as the board would show it.

    `label` is `Throw.label` -- "T20", "BULL", "MISS" -- so a client does not
    have to re-derive the one string every scoreboard displays.
    """

    dart_id: int
    dart_index: int
    segment: int
    multiplier: int
    label: str
    counted: bool
    caused_bust: bool
    was_checkout_attempt: bool


@dataclass(frozen=True, slots=True)
class VisitState:
    """A visit and its darts.

    `score_before` and `score_after` are x01 remaining in an x01 leg and the
    throwing team's cricket points in a cricket leg, exactly as the column
    means it.
    """

    visit_id: int
    team_id: int
    player_id: int
    visit_index: int
    score_before: int
    score_after: int
    is_bust: bool
    is_complete: bool
    darts: tuple[DartState, ...]


@dataclass(frozen=True, slots=True)
class TeamLegState:
    """One team's position in the current leg.

    x01 fills `remaining` and `is_open` and leaves `marks` None; cricket does
    the reverse and uses `points`. The split mirrors `leg_team_state`, which is
    the cache of precisely this.
    """

    team_id: int
    remaining: int | None
    is_open: bool | None
    darts_thrown: int
    points: int
    marks: Mapping[int, int] | None


@dataclass(frozen=True, slots=True)
class LegState:
    """A leg as it stands, derived from its darts rather than from its cache."""

    leg_id: int
    leg_index: int
    starting_team_id: int
    winner_team_id: int | None
    is_complete: bool
    darts_thrown: int
    darts_left: int
    next_thrower: Thrower | None
    teams: tuple[TeamLegState, ...]
    last_visit: VisitState | None


@dataclass(frozen=True, slots=True)
class GameState:
    """A match, its teams, its tally, and the leg the caller addressed.

    `legs_won` is positional to `teams`. `current_leg` is always the leg that
    was asked about, shown complete with its winner if the last dart won it.

    `active_leg_id` is the separate question "where does the next dart go": the
    same leg while it is in progress, the leg opened behind it once it
    finishes, and None once the match is won. A client that has just recorded a
    winning dart needs both -- the finished scoreboard to show, and somewhere
    to throw next.
    """

    match_id: int
    config: GameConfig
    teams: tuple[Team, ...]
    legs_won: tuple[int, ...]
    winner_team_id: int | None
    is_complete: bool
    active_leg_id: int | None
    current_leg: LegState
