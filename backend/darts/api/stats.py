"""Statistics: one player, one match, or everybody ranked.

Every number here is derived from raw darts at the moment it is asked for. There
is no counter to fall out of step with the darts, so these endpoints are always
telling the truth about the whole history, including history recorded before the
metric existed.

Three decisions worth knowing before reading a response
-------------------------------------------------------
**Scoring metrics are per-player, from the player's own darts.** A 2v2 match and
four solo matches containing the same darts produce the same per-player numbers;
team play never distorts an individual statistic. The one deliberate exception is
`legs_won` / `matches_won`, which are team outcomes: a leg is won by a team, and
every member of that team is credited with it. Four solo matches award four wins
where one 2v2 awards one win to two players, and no arrangement of the numbers
makes those the same.

**x01 metrics are always over x01 darts, cricket metrics over cricket darts.**
`?game_type=` narrows further, but it is not what makes an average meaningful:
a 3-dart average mixing 501 darts with cricket darts would be a number about
nothing, so the two blocks are separated whether or not the caller asked.

**`?since=` cuts on the match's `created_at`**, so a match is never split across
the boundary -- stats since Monday are whole matches that started on or after
Monday, not the tail of Sunday night's game.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from darts.api.deps import ConnectionDep
from darts.engine.cricket import Variant
from darts.repo.config import GameType
from darts.services import stats as service
from darts.stats.queries import StatsFilter, stamp

router = APIRouter(prefix="/api/stats", tags=["stats"])

PlayerId = Annotated[int, Path(gt=0)]
MatchId = Annotated[int, Path(gt=0)]

#: The default threshold for the leaderboard. Without one, a player who has
#: thrown three darts and hit one 180 tops the table on an average of 180.
DEFAULT_MIN_DARTS = 50


class Filter(BaseModel):
    """The four query parameters, parsed and validated during request parsing.

    Typed rather than free text so that a bad `game_type` or an unparseable
    `since` is a field-level 422 from FastAPI, not a `ValueError` raised inside
    a handler -- which would be a 500, and a lie about whose fault it was.
    `extra="forbid"` for the same reason: `?gametype=x01` is a typo that would
    otherwise be silently ignored and answered with unfiltered numbers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    game_type: GameType | None = None
    variant: Variant | None = None
    since: datetime | None = None
    match_id: Annotated[int | None, Field(gt=0)] = None

    def to_stats_filter(self) -> StatsFilter:
        return StatsFilter(
            game_type=None if self.game_type is None else str(self.game_type),
            variant=None if self.variant is None else str(self.variant),
            since=None if self.since is None else stamp(self.since),
            match_id=self.match_id,
        )


class LeaderboardFilter(Filter):
    """The same four, plus the threshold.

    `min_darts` lives in the model rather than beside it because FastAPI only
    expands a Pydantic query model when it is the route's *only* query
    parameter; add a second one and the model silently becomes a scalar
    parameter called `applied` that every request is then missing.
    """

    min_darts: Annotated[int, Field(ge=0)] = DEFAULT_MIN_DARTS


FilterDep = Annotated[Filter, Query()]
LeaderboardFilterDep = Annotated[LeaderboardFilter, Query()]


class FilterResponse(BaseModel):
    """What the request was narrowed to, echoed back so a client can label a chart."""

    game_type: GameType | None
    variant: Variant | None
    since: str | None
    match_id: int | None


class BandsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    one_eighties: int
    one_forty_plus: int
    hundred_plus: int
    sixty_plus: int


class X01Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    darts_thrown: int
    visits: int
    points_scored: int
    three_dart_average: float | None
    first_nine_average: float | None
    first_nine_darts: int
    highest_visit: int | None
    average_visit: float | None
    bands: BandsResponse
    checkout_attempts: int
    checkouts_hit: int
    checkout_percentage: float | None
    best_checkout: int | None


class TargetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    target: int
    hits: int
    marks: int
    hit_rate: float | None


class CricketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    darts_thrown: int
    marks: int
    darts_on_target: int
    wasted_darts: int
    marks_per_round: float | None
    targets: list[TargetResponse]


class SegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    segment: int
    multiplier: int
    darts: int


class PlayerStatsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    player_id: int
    display_name: str
    is_archived: bool
    darts_thrown: int
    legs_played: int
    legs_won: int
    matches_played: int
    matches_won: int
    x01: X01Response
    cricket: CricketResponse
    segments: list[SegmentResponse]


class PlayerReportResponse(BaseModel):
    filter: FilterResponse
    player: PlayerStatsResponse


class LegLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    leg_id: int
    leg_index: int
    player_id: int
    darts_thrown: int
    won: bool
    three_dart_average: float | None
    marks_per_round: float | None


class MatchStatsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    match_id: int
    game_type: GameType
    variant: Variant | None
    players: list[PlayerStatsResponse]
    legs: list[LegLineResponse]


class MatchReportResponse(BaseModel):
    filter: FilterResponse
    match: MatchStatsResponse


class LeaderboardRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    player_id: int
    display_name: str
    darts_thrown: int
    three_dart_average: float | None
    highest_visit: int | None
    one_eighties: int
    checkout_attempts: int
    checkouts_hit: int
    checkout_percentage: float | None
    best_checkout: int | None


class LeaderboardResponse(BaseModel):
    filter: FilterResponse
    min_darts: int
    ranked_by: Literal["three_dart_average"]
    rows: list[LeaderboardRowResponse]


def echo_filter(applied: Filter) -> FilterResponse:
    """The filter as applied, with `since` in the text form the queries compared.

    Public because #20's exports echo the same four fields the same way; a
    second spelling of this would be a second chance to disagree.
    """
    return FilterResponse(
        game_type=applied.game_type,
        variant=applied.variant,
        since=None if applied.since is None else stamp(applied.since),
        match_id=applied.match_id,
    )


@router.get("/players/{player_id}", response_model=PlayerReportResponse)
def player_stats(
    player_id: PlayerId, conn: ConnectionDep, applied: FilterDep
) -> PlayerReportResponse:
    """One player. A player who has never thrown gets zeroes and nulls, not a 404."""
    stats = service.player_stats(conn, player_id, applied.to_stats_filter())
    return PlayerReportResponse(
        filter=echo_filter(applied), player=PlayerStatsResponse.model_validate(stats)
    )


@router.get("/leaderboard", response_model=LeaderboardResponse)
def leaderboard(conn: ConnectionDep, applied: LeaderboardFilterDep) -> LeaderboardResponse:
    """Everybody who has thrown at least `min_darts` x01 darts, best average first.

    Archived players are left off, following #17's pickers: a leaderboard is a
    thing you are currently on. They keep every other statistic, and their own
    endpoint still answers.
    """
    ranking = service.leaderboard(conn, applied.to_stats_filter(), applied.min_darts)
    return LeaderboardResponse(
        filter=echo_filter(applied),
        min_darts=ranking.min_darts,
        ranked_by="three_dart_average",
        rows=[LeaderboardRowResponse.model_validate(row) for row in ranking.rows],
    )


@router.get("/matches/{match_id}", response_model=MatchReportResponse)
def match_stats(match_id: MatchId, conn: ConnectionDep, applied: FilterDep) -> MatchReportResponse:
    """One match, per player and per leg.

    `?match_id=` is accepted here for uniformity with the other two endpoints,
    but it may only name the match already in the path. Silently ignoring a
    contradiction, or answering about the other match, are both worse than
    saying so.
    """
    if applied.match_id is not None and applied.match_id != match_id:
        raise HTTPException(422, "match_id must name the match in the path, or be omitted")
    stats = service.match_stats(conn, match_id, applied.to_stats_filter())
    return MatchReportResponse(
        filter=echo_filter(applied), match=MatchStatsResponse.model_validate(stats)
    )
