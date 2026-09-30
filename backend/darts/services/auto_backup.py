"""The Pi backs itself up: once when the app starts, then every 24 hours.

Before this, `/var/lib/darts/backups/` was written only by `deploy.sh`, just
before a deploy. The boot check restores a damaged database from the newest
backup there, so a card that went bad weeks after the last deploy was repaired
by rolling back weeks. #31 closes that gap here, in the app, rather than with a
host timer: the copy is made by the process that already owns the database, as
the uid that already owns it (the WAL sidecar trap from #28 and #29 cannot
arise), it needs no host configuration and no bootstrap re-run, and nothing
about it is visible over HTTP.

"On boot" means every time the app starts, which on this Pi is every power-on,
every deploy and every crash restart. The Pi is power-cycled constantly, so
most backups will come from here rather than from the 24-hour timer; the
retention in `darts.db.backup` (newest per hour for a day, newest per day for a
month) collapses them. The interval is measured by the thread's own clock, not
the wall clock, because the Pi has no battery-backed clock and its time can be
wrong until NTP syncs after a power cut.

**A database with no matches is never backed up.** A missing database comes
back from the boot check as a new, empty, *healthy* one, and a degraded boot
leaves an empty one too. Backing either up would make an empty file the newest
valid backup, which is exactly what the next boot check would restore after
real corruption. A database with no matches has no history worth protecting,
and skipping it keeps the good backups ahead of it.

A failed backup is logged and swallowed. The app's job is scoring darts; a full
card or a permissions problem is worth an error in the log, not a server that
will not start.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from darts.db import backup
from darts.db.durability import read_only_uri

logger = logging.getLogger(__name__)

#: How long shutdown waits for a backup already in progress. A copy of a
#: 50,000-dart database takes about a tenth of a second on an M-series Mac and
#: will be slower on an SD card; this leaves most of Compose's 30-second grace
#: period for the WAL checkpoint that follows.
STOP_TIMEOUT = 20.0


def has_history(database: Path) -> bool:
    """Whether the database holds at least one match.

    Opened read-only through a URI, so asking can neither create a missing
    database nor change a present one's journal mode.
    """
    if not database.is_file():
        return False
    with closing(sqlite3.connect(read_only_uri(database), uri=True)) as conn:
        (present,) = conn.execute("SELECT EXISTS (SELECT 1 FROM matches)").fetchone()
    return bool(present)


def take(database: Path, backup_dir: Path) -> backup.BackupResult | None:
    """Back the database up and apply retention, or say why not. Never raises."""
    try:
        if not has_history(database):
            logger.info("automatic backup skipped: no matches yet", extra={"database": database})
            return None
        result = backup.create(database, backup_dir=backup_dir)
    except Exception:
        logger.exception("automatic backup failed", extra={"database": database})
        return None
    logger.info(
        "automatic backup",
        extra={
            "path": result.backup.path,
            "schema_version": result.manifest["schema_version"],
            "pruned": len(result.pruned),
        },
    )
    return result


class BackupScheduler:
    """Run a job now, then every `interval` seconds, on one daemon thread.

    A thread rather than an asyncio task because the job is blocking file I/O,
    and because stopping has to *wait* for a backup already in progress: the
    shutdown checkpoint that follows cannot truncate the WAL while a copy holds
    a read transaction open. `Event.wait` is both the sleep and the stop
    signal, so stopping never waits out the interval.
    """

    def __init__(self, job: Callable[[], object], interval: float) -> None:
        self._job = job
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="darts-backup", daemon=True)
        #: Completed runs, including failed ones.
        self.runs = 0

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                self._job()
            except Exception:
                logger.exception("scheduled job failed")
            self.runs += 1
            if self._stop.wait(self._interval):
                return

    def stop(self, timeout: float = STOP_TIMEOUT) -> bool:
        """Stop, waiting up to `timeout` for a run in progress. True if it stopped."""
        self._stop.set()
        self._thread.join(timeout)
        stopped = not self._thread.is_alive()
        if not stopped:
            logger.error("automatic backup still running at shutdown", extra={"timeout": timeout})
        return stopped
