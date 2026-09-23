"""What the box will say when someone asks whether it is working.

Two conditions make this unhealthy and they are not the same condition. The
boot integrity check from #12 knows whether this process started degraded, and
only it knows that; it cannot know that the SD card went read-only an hour
later. So health is the cached boot status *plus* a live probe on every
request.

The probe opens a connection and reads `PRAGMA user_version` -- which is the
schema version the endpoint has to report anyway. Opening is itself the
writability test: `connect()` sets `PRAGMA journal_mode = WAL`, which fails on
a database that cannot be written. So the check costs no write, which matters
because #28's Docker HEALTHCHECK will run it every few seconds for years on a
card that wears out.
"""

import sqlite3
from enum import StrEnum
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.responses import JSONResponse, Response

from darts import __version__
from darts.api.deps import RecoveryDep, SettingsDep
from darts.api.errors import ErrorCode, envelope
from darts.config import Settings
from darts.db.connection import connection
from darts.db.recovery import RecoveryStatus

router = APIRouter(prefix="/api", tags=["system"])


class HealthState(StrEnum):
    """The three states of #12, plus the two only the server can be in."""

    HEALTHY = "healthy"
    RESTORED = "restored"
    DEGRADED = "degraded"
    #: The boot check passed, or never ran, but the database will not open now.
    UNAVAILABLE = "unavailable"
    #: Lifespan startup has not run, so nothing has checked the database at all.
    NOT_STARTED = "not_started"


#: Anything else is a 503. `restored` is deliberately a 200: the restore
#: already happened, the database is serviceable, and a monitor that restarted
#: the box over it would only throw away the recovery.
SERVING = frozenset({HealthState.HEALTHY, HealthState.RESTORED})


class HealthReport(BaseModel):
    """The body of `/api/healthz`, and the `detail` of its 503."""

    status: HealthState
    schema_version: int | None
    git_sha: str
    version: str
    auto_restored: bool
    checked_at: str | None
    detail: str | None


class VersionReport(BaseModel):
    """What is deployed, for the footer of the app and for a deploy to verify."""

    version: str
    git_sha: str


def probe(database: Path) -> tuple[int | None, str | None]:
    """The live half: `(schema_version, problem)`, exactly one of them set.

    Every failure is caught and described rather than raised, because an
    unreachable database is the answer this endpoint exists to give.
    """
    try:
        with connection(database) as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0]), None
    except (sqlite3.Error, OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def report(settings: Settings, recovery: RecoveryStatus | None) -> HealthReport:
    """Fold the cached boot status and the live probe into one answer."""
    schema_version, problem = probe(settings.db_path)
    status: HealthState
    detail: str | None

    if recovery is None:
        status, detail = HealthState.NOT_STARTED, "startup has not run"
    elif problem is not None:
        status, detail = HealthState.UNAVAILABLE, problem
    else:
        status, detail = HealthState(recovery.state), recovery.detail

    return HealthReport(
        status=status,
        schema_version=schema_version,
        git_sha=settings.git_sha,
        version=__version__,
        auto_restored=recovery is not None and recovery.auto_restored,
        checked_at=recovery.checked_at if recovery else None,
        detail=detail,
    )


@router.get(
    "/healthz",
    response_model=HealthReport,
    summary="Database and build health",
    responses={503: {"description": "Degraded, unwritable, or not yet started"}},
)
def healthz(settings: SettingsDep, recovery: RecoveryDep) -> Response:
    """200 while the database is serving, 503 otherwise.

    The 503 goes out in the standard error envelope like every other `/api`
    failure, with the whole report under `detail` so the reason survives.
    """
    health = report(settings, recovery)
    if health.status in SERVING:
        return JSONResponse(health.model_dump(mode="json"))
    return envelope(
        503,
        ErrorCode.SERVICE_UNAVAILABLE,
        f"The database is {health.status}",
        health.model_dump(mode="json"),
    )


@router.get("/version", response_model=VersionReport, summary="Deployed build")
def version(settings: SettingsDep) -> VersionReport:
    return VersionReport(version=__version__, git_sha=settings.git_sha)
