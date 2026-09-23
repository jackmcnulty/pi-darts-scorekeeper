"""Player management, with request validation before any service write."""

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from darts.api.deps import ConnectionDep
from darts.repo import players
from darts.services import setup

router = APIRouter(prefix="/api/players", tags=["players"])
PlayerId = Annotated[int, Path(gt=0)]

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortName = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=players.SHORT_NAME_MAX)
]
#: One-based into #4's accent palette, which is eight colours wide. The colours
#: live in `frontend/src/styles/tokens.css`; only the index crosses the wire.
AccentIndex = Annotated[int, Field(ge=1, le=players.ACCENT_COUNT)]


class PlayerWrite(BaseModel):
    """The editable half of a player, read slightly differently by each verb.

    `display_name` is required either way. The other two are optional, and what
    leaving one out means depends on the verb:

    * `POST` -- no accent means "choose one", and the server picks a colour no
      active player is using. No short name means there isn't one.
    * `PATCH` -- a field that is absent is left exactly as it was, and a field
      sent as `null` is cleared. Those are different requests, which is why the
      route reads `model_fields_set` rather than treating `None` as both.
    """

    model_config = ConfigDict(extra="forbid")
    display_name: DisplayName
    short_name: ShortName | None = None
    accent_index: AccentIndex | None = None


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str
    short_name: str | None
    accent_index: int | None
    is_archived: bool
    created_at: str


@router.get("", response_model=list[PlayerResponse])
def list_players(conn: ConnectionDep, include_archived: bool = False) -> list[players.Player]:
    return players.list_players(conn, include_archived=include_archived)


@router.post("", response_model=PlayerResponse, status_code=201)
def create_player(payload: PlayerWrite, conn: ConnectionDep) -> players.Player:
    return setup.create_player(
        conn,
        payload.display_name,
        short_name=payload.short_name,
        accent_index=payload.accent_index,
    )


@router.patch("/{player_id}", response_model=PlayerResponse)
def update_player(player_id: PlayerId, payload: PlayerWrite, conn: ConnectionDep) -> players.Player:
    sent = payload.model_fields_set
    return setup.update_player(
        conn,
        player_id,
        payload.display_name,
        short_name=payload.short_name if "short_name" in sent else players.UNSET,
        accent_index=payload.accent_index if "accent_index" in sent else players.UNSET,
    )


@router.post("/{player_id}/archive", response_model=PlayerResponse)
def archive_player(player_id: PlayerId, conn: ConnectionDep) -> players.Player:
    return setup.archive_player(conn, player_id)
