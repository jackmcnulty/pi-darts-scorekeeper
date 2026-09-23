"""Per-request wiring: the settings, the boot status, and a connection.

**One connection per request**, opened in a dependency and closed when the
request ends. FastAPI may schedule dependency entry, endpoint execution and
dependency cleanup on different worker threads, so API connections explicitly
disable the thread-affinity check. The connection belongs exclusively to one
request and those stages run sequentially; no connection is shared between
requests or used by parallel work inside a request.
Opening a local file is five PRAGMAs and no network, and this box serves one
household, so the cost is not worth engineering around.

Endpoints take the connection and hand it to a service. The API layer never
opens a transaction of its own; `darts.services` owns those.
"""

import sqlite3
from collections.abc import Iterator
from typing import Annotated, cast

from fastapi import Depends
from starlette.requests import Request

from darts.config import Settings
from darts.db.connection import connection
from darts.db.recovery import RecoveryStatus


def get_settings(request: Request) -> Settings:
    """The settings this app was built with, not a fresh read of the environment."""
    return cast(Settings, request.app.state.settings)


def get_recovery(request: Request) -> RecoveryStatus | None:
    """What the boot integrity check found, or None if startup has not run."""
    return cast(RecoveryStatus | None, request.app.state.recovery)


def get_connection(request: Request) -> Iterator[sqlite3.Connection]:
    """A connection for the life of one request, closed however the request ends."""
    with connection(get_settings(request).db_path, check_same_thread=False) as conn:
        yield conn


#: Annotated aliases so endpoints read as `def route(conn: ConnectionDep)`,
#: which #17 onwards will use everywhere. `Depends` in a default argument is
#: the older spelling and trips ruff's B008.
SettingsDep = Annotated[Settings, Depends(get_settings)]
RecoveryDep = Annotated[RecoveryStatus | None, Depends(get_recovery)]
ConnectionDep = Annotated[sqlite3.Connection, Depends(get_connection)]
