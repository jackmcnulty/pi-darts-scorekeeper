"""Two maintenance actions the box can be asked to perform on itself.

Both are safe to run at any time, including mid-game, and both are idempotent:
running either twice produces the same answer as running it once. That is what
makes them usable as buttons rather than as procedures.

Neither opens a transaction of its own. `snapshot.create` works off a separate
read-only copy, and `play.rebuild_caches` already owns one transaction per leg.
Rebuilding leg by leg rather than under one lock is deliberate: this can run
while somebody is throwing, and a single transaction spanning every leg would
hold the write lock for the whole sweep.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any

from darts.services import play

#: The disposable replay caches. `cricket_leg_state` cascades from
#: `leg_team_state`, so the two are counted together as the one cache they are.
_CACHE_TABLES = ("leg_team_state", "cricket_leg_state")


@dataclass(frozen=True, slots=True)
class RebuildReport:
    """What a sweep changed, in the terms an operator would ask in.

    `legs_changed` is the number that matters: on a healthy database it is 0,
    and anything else means the caches had drifted from the darts.
    """

    legs_visited: int
    legs_changed: int
    rows_before: int
    rows_after: int


def _cache_rows(conn: sqlite3.Connection, leg_id: int) -> tuple[tuple[Any, ...], ...]:
    """A leg's cache rows as a sorted, comparable value.

    Sorted rather than left in storage order, so an unchanged leg compares equal
    whatever order a rewrite happened to reinsert its rows in -- the question
    being asked is whether the *contents* moved. Table names come from
    `_CACHE_TABLES`, never from a caller.
    """
    rows: list[tuple[Any, ...]] = []
    for table in _CACHE_TABLES:
        found = conn.execute(f"SELECT * FROM {table} WHERE leg_id = ?", (leg_id,))
        rows.extend(sorted((table, *row) for row in found))
    return tuple(rows)


def rebuild_caches(conn: sqlite3.Connection) -> RebuildReport:
    """Recompute every leg's replay cache from its darts.

    Every leg, not only the unfinished ones. `play.rebuild_caches` deletes as
    readily as it writes, and the invariant it maintains -- documented in
    `tests/fixtures/test_rebuild_caches.py` -- is that only an *unfinished* leg
    holds cache rows at all. A sweep limited to unfinished legs could therefore
    never clean up a finished leg that wrongly held some, which is exactly the
    drift worth repairing. Over a correct database this changes nothing.
    """
    leg_ids = [int(row[0]) for row in conn.execute("SELECT id FROM legs ORDER BY id")]
    changed = 0
    before = after = 0
    for leg_id in leg_ids:
        was = _cache_rows(conn, leg_id)
        play.rebuild_caches(conn, leg_id)
        now = _cache_rows(conn, leg_id)
        before += len(was)
        after += len(now)
        if was != now:
            changed += 1
    return RebuildReport(len(leg_ids), changed, before, after)
