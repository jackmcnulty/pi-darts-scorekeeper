"""The replay caches: written as a unit, cleared as a unit, read back sorted."""

import sqlite3

from repofixtures import count

from darts.engine.cricket import TARGETS
from darts.repo.legstate import TeamCache, clear_leg_state, leg_state_for, write_leg_state
from darts.repo.matches import CreatedMatch


def _x01(team_id: int, remaining: int = 501) -> TeamCache:
    return TeamCache(team_id=team_id, remaining=remaining, is_open=True, darts_thrown=3, points=0)


def _cricket(team_id: int, points: int = 0) -> TeamCache:
    return TeamCache(
        team_id=team_id,
        remaining=None,
        is_open=None,
        darts_thrown=3,
        points=points,
        marks=dict.fromkeys(TARGETS, 0),
    )


def test_an_uncached_leg_reads_back_empty(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    assert leg_state_for(db, doubles.leg_id) == []


def test_x01_rows_round_trip(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    rows = [_x01(doubles.team_ids[0], 441), _x01(doubles.team_ids[1], 360)]
    write_leg_state(db, doubles.leg_id, doubles.match_id, rows)

    assert leg_state_for(db, doubles.leg_id) == rows
    assert count(db, "cricket_leg_state") == 0


def test_cricket_rows_carry_one_marks_entry_per_target(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    rows = [_cricket(doubles.team_ids[0], points=60), _cricket(doubles.team_ids[1])]
    write_leg_state(db, doubles.leg_id, doubles.match_id, rows)

    stored = leg_state_for(db, doubles.leg_id)
    assert stored == rows
    assert stored[0].marks == dict.fromkeys(TARGETS, 0)
    assert count(db, "cricket_leg_state") == 2 * len(TARGETS)


def test_writing_replaces_rather_than_merges(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    write_leg_state(db, doubles.leg_id, doubles.match_id, [_cricket(doubles.team_ids[0])])
    write_leg_state(db, doubles.leg_id, doubles.match_id, [_x01(doubles.team_ids[1])])

    assert leg_state_for(db, doubles.leg_id) == [_x01(doubles.team_ids[1])]
    assert count(db, "cricket_leg_state") == 0


def test_clearing_takes_the_marks_with_it(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    write_leg_state(db, doubles.leg_id, doubles.match_id, [_cricket(doubles.team_ids[0])])

    clear_leg_state(db, doubles.leg_id)

    assert leg_state_for(db, doubles.leg_id) == []
    assert count(db, "cricket_leg_state") == 0
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_clearing_an_uncached_leg_is_harmless(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    clear_leg_state(db, doubles.leg_id)
    assert leg_state_for(db, doubles.leg_id) == []
