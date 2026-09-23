"""Match setup and history metadata. Live game state belongs to the play API."""

from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from darts.api.deps import ConnectionDep
from darts.repo import matches
from darts.repo.config import GameConfig
from darts.services import setup

router = APIRouter(prefix="/api/matches", tags=["matches"])
MatchId = Annotated[int, Path(gt=0)]


class TeamWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    player_ids: Annotated[list[Annotated[int, Field(gt=0)]], Field(min_length=1)]
    name: str | None = None


class MatchWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: GameConfig
    teams: Annotated[list[TeamWrite], Field(min_length=2)]

    @field_validator("teams")
    @classmethod
    def validate_composition(cls, teams: list[TeamWrite], info: ValidationInfo) -> list[TeamWrite]:
        ids = [player_id for team in teams for player_id in team.player_ids]
        if len(ids) != len(set(ids)):
            raise ValueError("player_ids must be unique across all teams")
        config = info.data.get("config")
        if config is not None and config.fixed_team >= len(teams):
            raise ValueError("config.fixed_team must name an existing team index")
        return teams


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


class MatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    config: GameConfig
    status: matches.MatchStatus
    created_at: str
    completed_at: str | None
    abandoned_at: str | None
    winner_team_id: int | None
    current_leg_id: int | None
    teams: list[TeamResponse]


class MatchPage(BaseModel):
    items: list[MatchResponse]
    total: int
    limit: int
    offset: int


@router.post("", response_model=MatchResponse, status_code=201)
def create_match(payload: MatchWrite, conn: ConnectionDep) -> matches.Match:
    teams = [matches.TeamSpec(tuple(team.player_ids), team.name) for team in payload.teams]
    return setup.create_match(conn, payload.config, teams)


@router.get("", response_model=MatchPage)
def list_matches(
    conn: ConnectionDep,
    status: matches.MatchStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchPage:
    items, total = setup.list_matches(conn, status=status, limit=limit, offset=offset)
    return MatchPage(
        items=[MatchResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{match_id}", response_model=MatchResponse)
def get_match(match_id: MatchId, conn: ConnectionDep) -> matches.Match:
    return matches.get_match(conn, match_id)


@router.post("/{match_id}/abandon", response_model=MatchResponse)
def abandon_match(match_id: MatchId, conn: ConnectionDep) -> matches.Match:
    return setup.abandon_match(conn, match_id)
