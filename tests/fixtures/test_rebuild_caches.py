"""`rebuild_caches` over the seeded fixture changes zero rows.

Lives here rather than in `tests/services` so the schema job runs it: what it
really asserts is that the cache tables the schema defines hold what the raw
darts say they should, over a dataset written by a different ticket without the
service's help.

The invariant is narrower than it looks. Six of the seed's eight legs are
finished and hold *no* cache rows at all, because #13 decided these tables
exist only to resume an interrupted leg. So a naive rebuild that wrote state
for every leg would add rows to six legs and fail here.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from seed import MATCHES, build, digest, dump

from darts.repo.legstate import leg_state_for
from darts.services import play, verify


@pytest.fixture
def seeded(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = build(tmp_path / "seeded.db")
    try:
        yield conn
    finally:
        conn.close()


def _leg_ids(conn: sqlite3.Connection) -> list[int]:
    return [int(row[0]) for row in conn.execute("SELECT id FROM legs ORDER BY id")]


def test_rebuild_is_noop(seeded: sqlite3.Connection) -> None:
    """Every row of the whole database, not just the two cache tables."""
    before = dump(seeded)

    for leg_id in _leg_ids(seeded):
        play.rebuild_caches(seeded, leg_id)

    assert dump(seeded) == before


def test_rebuild_is_noop_by_digest(seeded: sqlite3.Connection) -> None:
    """The same claim as a single hash, which is what a failure report reads."""
    before = digest(seeded)

    for leg_id in _leg_ids(seeded):
        play.rebuild_caches(seeded, leg_id)

    assert digest(seeded) == before


def test_only_unfinished_legs_hold_cache_rows(seeded: sqlite3.Connection) -> None:
    """The invariant `rebuild_caches` has to respect, stated directly."""
    cached, uncached = [], []
    for row in seeded.execute("SELECT id, winner_team_id FROM legs ORDER BY id"):
        (uncached if row["winner_team_id"] is not None else cached).append(row["id"])

    assert len(cached) == 2
    assert len(uncached) == 6
    assert all(leg_state_for(seeded, leg_id) != [] for leg_id in cached)
    assert all(leg_state_for(seeded, leg_id) == [] for leg_id in uncached)


def test_rebuild_restores_a_cache_that_was_wiped(seeded: sqlite3.Connection) -> None:
    """Not vacuously a no-op: it does write, when there is something to write."""
    unfinished = seeded.execute(
        "SELECT id FROM legs WHERE winner_team_id IS NULL ORDER BY id"
    ).fetchone()["id"]
    expected = leg_state_for(seeded, unfinished)
    seeded.execute("DELETE FROM leg_team_state WHERE leg_id = ?", (unfinished,))
    assert leg_state_for(seeded, unfinished) == []

    play.rebuild_caches(seeded, unfinished)

    assert leg_state_for(seeded, unfinished) == expected


def test_rebuild_removes_a_cache_a_finished_leg_should_not_have(
    seeded: sqlite3.Connection,
) -> None:
    finished = seeded.execute(
        "SELECT id, match_id FROM legs WHERE winner_team_id IS NOT NULL ORDER BY id"
    ).fetchone()
    seeded.execute(
        "INSERT INTO leg_team_state(leg_id, team_id, match_id, remaining, is_open, darts_thrown, "
        "points) VALUES (?, (SELECT id FROM teams WHERE match_id = ? LIMIT 1), ?, 7, 1, 3, 0)",
        (finished["id"], finished["match_id"], finished["match_id"]),
    )
    assert leg_state_for(seeded, finished["id"]) != []

    play.rebuild_caches(seeded, finished["id"])

    assert leg_state_for(seeded, finished["id"]) == []


def test_the_seeded_database_has_no_drift_at_all(seeded: sqlite3.Connection) -> None:
    """What `darts-verify` would report: every derived value, not just the caches."""
    assert verify.verify_database(seeded) == []
    assert verify.counts(seeded) == (len(MATCHES), 8)
