"""The FastAPI application: boot, shutdown, and how the pieces are wired.

Routing order is load-bearing. Starlette matches routes in the order they are
declared, so `/api` routes and the OpenAPI schema are registered before the
static mount at `/` and keep winning against it; the single-page-app fallback
inside the mount is what makes a client route survive a refresh.

The lifespan owns the two ends of the database's day:

* **startup** runs #12's boot integrity check, which repairs or restores a
  damaged database and returns the status `/api/healthz` reports until the
  process exits.
* **shutdown** folds the WAL back into the database, so the file left on the
  card is self-contained and the next boot has nothing to replay.

Both are logged, including the checkpoint's own verdict: SQLite reports a busy
checkpoint rather than failing, so a hook that ignored the result would claim a
clean shutdown it never achieved.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from darts import __version__
from darts.api.errors import install_error_handlers
from darts.api.health import router as health_router
from darts.api.logging_conf import RequestContextMiddleware, configure_logging
from darts.api.matches import router as matches_router
from darts.api.players import router as players_router
from darts.api.static import mount_static
from darts.config import Settings
from darts.db.connection import connection
from darts.db.durability import checkpoint_truncate
from darts.db.recovery import check_and_recover

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    status = check_and_recover(settings.db_path, backup_dir=settings.backup_dir)
    app.state.recovery = status
    logger.info(
        "boot check complete",
        extra={
            "database": settings.db_path,
            "state": status.state,
            "auto_restored": status.auto_restored,
            "detail": status.detail,
        },
    )

    yield

    _checkpoint(settings)


def _checkpoint(settings: Settings) -> None:
    """Truncate the WAL on the way out, and never take the process down with it.

    A shutdown that cannot checkpoint is worth an error in the log, but raising
    here would turn an unclean shutdown into a failed one -- and the committed
    data is durable either way; #11's `synchronous = FULL` saw to that. The WAL
    is simply replayed on the next boot instead.
    """
    try:
        with connection(settings.db_path) as conn:
            result = checkpoint_truncate(conn)
    except Exception as exc:  # pragma: no cover - defensive; the box is going down
        logger.error("shutdown checkpoint failed", extra={"error": str(exc)})
        return

    log = logger.info if result.truncated else logger.error
    log(
        "shutdown checkpoint",
        extra={
            "truncated": result.truncated,
            "busy": result.busy,
            "wal_pages": result.wal_pages,
            "checkpointed_pages": result.checkpointed_pages,
        },
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app from explicit settings, or from the environment.

    Tests build their own so that nothing here depends on the machine it runs
    on; `uvicorn darts.api.main:app` gets the environment-derived one.
    """
    resolved = settings if settings is not None else Settings.from_env()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="darts",
        version=__version__,
        summary="Phone-first darts scorekeeper",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    # Nothing has checked the database until lifespan startup runs. Health
    # reports that honestly rather than assuming the best, which is what stops
    # a test that skipped the lifespan from passing for the wrong reason.
    app.state.recovery = None

    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(players_router)
    app.include_router(matches_router)
    mount_static(app, resolved.static_dir)
    return app


app = create_app()
