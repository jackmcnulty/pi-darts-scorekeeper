"""Getting the data off the Pi: two CSVs, a statistics document, and the file.

The three questions this module answers differently from every other router
-----------------------------------------------------------------------------
**Why the CSV routes open their own connection.** Everywhere else, a handler
takes `deps.ConnectionDep` and hands it to a service. That cannot work for a
stream. A `StreamingResponse` body is consumed *after* the endpoint has
returned, by which time `deps.get_connection`'s `with connection(...)` block has
closed the connection and the cursor behind it. So the streaming routes open a
connection inside the generator that produces the body, and close it when
iteration ends -- normally, on an error, or when Starlette closes the generator
because the client went away mid-download. `check_same_thread=False` for the
usual reason: Starlette drives a sync generator through the threadpool, so
successive `next()` calls can land on different workers. The connection is still
used strictly sequentially by exactly one request, which is the rule `deps`
states. `stats.json` is not a stream and uses `ConnectionDep` like everything
else.

**Why the streaming routes have no response model.** A CSV body and a SQLite
file are not pydantic models, so `response_model` is meaningless and
`response_class` plus an explicit `responses=` entry is what documents them.
`tests/api/test_openapi.py` pins their media types rather than a `$ref`;
`stats.json` is JSON and is pinned the same way every other route is.

**Why `/export/db` copies rather than serving the file.** The live database is
in WAL mode, so its most recent committed pages live in a `-wal` sidecar the
client would not receive. Handing back `darts.db` would hand back a file missing
the most recent play, which no desktop tool can open without the sidecar
anyway. So each request takes a fresh `Connection.backup()` copy with its WAL
collapsed, streams that, and deletes it afterwards -- including when the
download is abandoned. It deliberately does not serve, or touch, the published
`darts-latest.db`: a download must never be able to leave the shared snapshot
half-written.
"""

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from darts.api.deps import ConnectionDep, SettingsDep
from darts.api.stats import (
    Filter,
    FilterDep,
    FilterResponse,
    LeaderboardRowResponse,
    PlayerStatsResponse,
    echo_filter,
)
from darts.config import Settings
from darts.db.connection import connection
from darts.services import export as service
from darts.services import snapshot

router = APIRouter(prefix="/api/export", tags=["export"])

#: `charset=utf-8` because display names are not ASCII-only, and a spreadsheet
#: left to guess the encoding will guess wrong exactly once.
CSV_MEDIA_TYPE: Final = "text/csv; charset=utf-8"

#: The IANA registration for a SQLite database file.
SQLITE_MEDIA_TYPE: Final = "application/vnd.sqlite3"

#: What the downloaded copy is called. Not `darts-latest.db`: that name belongs
#: to the published snapshot, and a download is a different artifact.
DOWNLOAD_NAME: Final = "darts.db"

#: Download copies are built in the snapshot directory -- the configured scratch
#: area for exactly this, on the same filesystem as the database and sized for
#: it, where `/tmp` on a Pi may be a RAM disk. The dot keeps a copy in flight out
#: of a directory listing, and `stream_copy` removes it however the download ends.
DOWNLOAD_PREFIX: Final = ".darts-download-"

#: 64 KiB, the size Starlette's own file responses read in.
_CHUNK: Final = 64 * 1024

#: OpenAPI cannot describe a CSV body as a model, so the media type is the
#: contract and these say so explicitly. Typed loosely because that is the shape
#: FastAPI declares for `responses=`.
_CSV_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {"content": {"text/csv": {"schema": {"type": "string"}}}}
}
_DB_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {"content": {SQLITE_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}}
}


class ExportFilter(Filter):
    """#19's four filters, plus the export's own leaderboard threshold.

    `min_darts` lives inside the model rather than beside it because FastAPI
    only expands a Pydantic query model when it is the route's *only* query
    parameter; a second one would silently turn this into a scalar called
    `applied` and 422 every request. It defaults to 0 rather than
    `/api/stats/leaderboard`'s 50: a ranking needs a threshold, an export does
    not, and a fresh Pi would otherwise export an empty table. Only
    `stats.json` takes it -- neither CSV has a ranking to threshold.
    """

    min_darts: Annotated[int, Field(ge=0)] = service.DEFAULT_EXPORT_MIN_DARTS


ExportFilterDep = Annotated[ExportFilter, Query()]


class LeaderboardBlock(BaseModel):
    min_darts: int
    ranked_by: Literal["three_dart_average"]
    rows: list[LeaderboardRowResponse]


class StatsExportResponse(BaseModel):
    """The whole statistical picture, as one document that can be saved to disk.

    Self-describing on purpose: something read off #30's share months later has
    to be able to say what it is, when it was true and what it was filtered to.
    """

    generated_at: str
    schema_version: int
    filter: FilterResponse
    leaderboard: LeaderboardBlock
    players: list[PlayerStatsResponse]


def _streamed(
    settings: Settings, produce: Callable[[sqlite3.Connection], Iterator[str]]
) -> Iterator[str]:
    """Drive a service generator from a connection this route owns.

    The `with` block opens on the first `next()` and closes on the last, on an
    exception, or when the generator is closed under it -- which is what
    Starlette does when a client disconnects mid-download.
    """
    with connection(settings.db_path, check_same_thread=False) as conn:
        yield from produce(conn)


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


@router.get(
    "/matches.csv",
    response_class=StreamingResponse,
    responses=_CSV_RESPONSES,
    summary="Every match as one CSV row",
)
def matches_csv(settings: SettingsDep, applied: FilterDep) -> StreamingResponse:
    """One line per match, with its teams and players flattened into two cells."""
    stats_filter = applied.to_stats_filter()
    return StreamingResponse(
        _streamed(settings, lambda conn: service.matches_csv(conn, stats_filter)),
        media_type=CSV_MEDIA_TYPE,
        headers=_attachment("matches.csv"),
    )


@router.get(
    "/darts.csv",
    response_class=StreamingResponse,
    responses=_CSV_RESPONSES,
    summary="Every recorded dart as one CSV row",
)
def darts_csv(settings: SettingsDep, applied: FilterDep) -> StreamingResponse:
    """One line per recorded dart, under the header documented in docs/data-model.md."""
    stats_filter = applied.to_stats_filter()
    return StreamingResponse(
        _streamed(settings, lambda conn: service.darts_csv(conn, stats_filter)),
        media_type=CSV_MEDIA_TYPE,
        headers=_attachment("darts.csv"),
    )


@router.get("/stats.json", response_model=StatsExportResponse)
def stats_json(conn: ConnectionDep, applied: ExportFilterDep) -> StatsExportResponse:
    """Every player's report and the leaderboard, read as one consistent whole.

    One object per player rather than one per dart, so this is small enough to
    build in memory and is an ordinary JSON response rather than a stream.
    """
    document = service.stats_document(conn, applied.to_stats_filter(), applied.min_darts)
    return StatsExportResponse(
        generated_at=document.generated_at,
        schema_version=document.schema_version,
        filter=echo_filter(applied),
        leaderboard=LeaderboardBlock(
            min_darts=document.min_darts,
            ranked_by="three_dart_average",
            rows=[LeaderboardRowResponse.model_validate(row) for row in document.leaderboard.rows],
        ),
        players=[PlayerStatsResponse.model_validate(player) for player in document.players],
    )


def stream_copy(copy: Path) -> Iterator[bytes]:
    """Read a temporary copy out in chunks and delete it, however this ends.

    `finally` rather than a trailing `unlink`, because the common way for this
    to end is not the loop finishing: it is the client cancelling the download,
    which closes the generator and raises `GeneratorExit` at the `yield`. A
    temporary left behind by every abandoned download would fill the card.
    """
    try:
        with copy.open("rb") as stream:
            while chunk := stream.read(_CHUNK):
                yield chunk
    finally:
        copy.unlink(missing_ok=True)


@router.get(
    "/db",
    response_class=StreamingResponse,
    responses=_DB_RESPONSES,
    summary="Download a fresh point-in-time copy of the database",
)
def export_db(settings: SettingsDep) -> StreamingResponse:
    """A fresh copy, taken now, with its WAL collapsed into it.

    The copy is taken here rather than inside the generator so that a failure to
    produce it is an error response, not a truncated download.
    """
    copy = snapshot.copy_of(settings.db_path, settings.snapshot_dir, prefix=DOWNLOAD_PREFIX)
    return StreamingResponse(
        stream_copy(copy),
        media_type=SQLITE_MEDIA_TYPE,
        headers=_attachment(DOWNLOAD_NAME),
    )
