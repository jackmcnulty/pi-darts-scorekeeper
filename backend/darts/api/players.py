"""Player management, with request validation before any service write."""

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, StringConstraints

from darts.api.deps import ConnectionDep
from darts.repo import players
from darts.services import setup

router = APIRouter(prefix="/api/players", tags=["players"])
PlayerId = Annotated[int, Path(gt=0)]


class PlayerWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str
    is_archived: bool
    created_at: str


@router.get("", response_model=list[PlayerResponse])
def list_players(conn: ConnectionDep, include_archived: bool = False) -> list[players.Player]:
    return players.list_players(conn, include_archived=include_archived)


@router.post("", response_model=PlayerResponse, status_code=201)
def create_player(payload: PlayerWrite, conn: ConnectionDep) -> players.Player:
    return setup.create_player(conn, payload.display_name)


@router.patch("/{player_id}", response_model=PlayerResponse)
def update_player(player_id: PlayerId, payload: PlayerWrite, conn: ConnectionDep) -> players.Player:
    return setup.update_player(conn, player_id, payload.display_name)


@router.post("/{player_id}/archive", response_model=PlayerResponse)
def archive_player(player_id: PlayerId, conn: ConnectionDep) -> players.Player:
    return setup.archive_player(conn, player_id)
