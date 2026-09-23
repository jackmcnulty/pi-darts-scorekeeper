"""Boot-time integrity check with automatic restore and a degraded fallback.

The FastAPI lifespan calls check_and_recover at startup and reports the status
it returns through /api/healthz for the life of the process. This module owns
the decision, not the transport.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from darts.db.backup import newest_valid, restore
from darts.db.connection import connection
from darts.db.durability import quarantine, verify_live
from darts.db.migrate import migrate
from darts.db.views import install_views

logger = logging.getLogger(__name__)


class DatabaseState(StrEnum):
    HEALTHY = "healthy"
    RESTORED = "restored"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class RecoveryStatus:
    """What the last boot found, for the health endpoint to surface."""

    state: DatabaseState
    checked_at: str
    detail: str | None = None
    quarantined_to: Path | None = None
    restored_from: Path | None = None

    @property
    def degraded(self) -> bool:
        return self.state is DatabaseState.DEGRADED

    @property
    def auto_restored(self) -> bool:
        return self.restored_from is not None


def _prepare(database: Path) -> None:
    """Bring a database up to the current schema, creating it if absent.

    Every outcome ends here, so a restored backup taken at an older schema
    version is serviceable the moment recovery returns. Views are reinstalled
    too, and for the same reason: they are not carried by the migration ledger,
    so a backup restored from an older build would otherwise come back with its
    tables but no query surface, and the failure would not show until the first
    statistics request.
    """
    database.parent.mkdir(parents=True, exist_ok=True)
    with connection(database) as conn:
        migrate(conn)
        install_views(conn)


def check_and_recover(
    database: Path, *, backup_dir: Path | None = None, now: datetime | None = None
) -> RecoveryStatus:
    """Verify the database at boot and repair it if it is damaged.

    Corruption never raises: this Pi is power-cycled constantly and must come
    up even with nothing salvageable, so the worst case is an empty database
    and a degraded report rather than a crash loop. Failures that are *not*
    corruption -- a permissions problem, an exhausted disk, an unsupported
    migration history -- propagate untouched, because quarantining a healthy
    database over an environmental fault would cause the very loss this
    guards against.
    """
    checked_at = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not database.exists():
        _prepare(database)
        logger.info("no database at %s; created an empty one", database)
        return RecoveryStatus(DatabaseState.HEALTHY, checked_at, detail="created a new database")

    problem = verify_live(database)
    if problem is None:
        _prepare(database)
        return RecoveryStatus(DatabaseState.HEALTHY, checked_at)

    quarantined = quarantine(database, now=now)
    logger.error(
        "database %s failed its boot check (%s); moved aside to %s",
        database,
        problem,
        quarantined,
    )

    candidate = newest_valid(database, backup_dir=backup_dir)
    if candidate is None:
        _prepare(database)
        logger.error("no valid backup for %s; starting empty and DEGRADED", database)
        return RecoveryStatus(
            DatabaseState.DEGRADED, checked_at, detail=problem, quarantined_to=quarantined
        )

    restore(database, candidate.path, now=now)
    _prepare(database)
    logger.error(
        "restored %s from the backup taken at %s (%s)",
        database,
        candidate.stamp,
        candidate.path,
    )
    return RecoveryStatus(
        DatabaseState.RESTORED,
        checked_at,
        detail=problem,
        quarantined_to=quarantined,
        restored_from=candidate.path,
    )
