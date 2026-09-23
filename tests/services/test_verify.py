"""Drift detection: what `darts-verify` finds when a derived row stops agreeing.

Each test breaks exactly one derived value with raw SQL -- the kind of damage
an interrupted write or a hand-edited row leaves -- and checks that the drift
is named rather than merely counted. A clean database reports nothing.
"""

import sqlite3

import pytest
from servicefixtures import (
    SCRIPT_301,
    SCRIPT_BUST,
    cricket,
    make_match,
    throw_into_match,
    throw_labels,
)

from darts.repo.darts import darts_for_leg
from darts.repo.legs import legs_for_match, set_leg_winner
from darts.repo.matches import CreatedMatch, set_match_winner
from darts.repo.visits import visits_for_leg
from darts.services import verify


def _subjects(drifts: list[verify.Drift]) -> list[str]:
    return [drift.subject for drift in drifts]


@pytest.fixture
def played(db: sqlite3.Connection, solo: CreatedMatch) -> CreatedMatch:
    """A 301 match with one finished leg and a bust in the leg after it."""
    throw_into_match(db, solo.leg_id, SCRIPT_301 + SCRIPT_BUST)
    return solo


def test_a_healthy_match_reports_nothing(db: sqlite3.Connection, played: CreatedMatch) -> None:
    assert verify.verify_match(db, played.match_id) == []
    assert verify.verify_database(db) == []
    assert verify.counts(db) == (1, 2)


def test_an_in_progress_cricket_match_reports_nothing(
    db: sqlite3.Connection, players: list[int]
) -> None:
    created = make_match(db, cricket("cutthroat"), tuple((p,) for p in players[:3]))
    throw_labels(db, created.leg_id, ["T20", "T20", "T19", "T20", "MISS"])

    assert verify.verify_leg(db, created.leg_id) == []


def test_a_wrong_counted_flag_is_found(db: sqlite3.Connection, played: CreatedMatch) -> None:
    busted = [d for d in darts_for_leg(db, played.leg_id)][0]
    db.execute("UPDATE darts SET counted = 0 WHERE id = ?", (busted.id,))

    drifts = verify.verify_leg(db, played.leg_id)

    assert _subjects(drifts) == [f"dart {busted.seq_in_leg}"]
    assert "False" in drifts[0].stored
    assert "True" in drifts[0].expected


def test_a_wrong_visit_score_is_found(db: sqlite3.Connection, played: CreatedMatch) -> None:
    visit = visits_for_leg(db, played.leg_id)[0]
    db.execute("UPDATE visits SET score_after = 999 WHERE id = ?", (visit.id,))

    assert _subjects(verify.verify_leg(db, played.leg_id)) == [f"visit {visit.visit_index}"]


def test_a_missing_visit_is_found(db: sqlite3.Connection, played: CreatedMatch) -> None:
    """A visit whose darts are gone: the count is checked before the contents,
    because without it every later comparison is noise."""
    open_leg = legs_for_match(db, played.match_id)[1]
    visits = visits_for_leg(db, open_leg.id)
    db.execute("DELETE FROM darts WHERE visit_id = ?", (visits[-1].id,))

    assert "visits" in _subjects(verify.verify_leg(db, open_leg.id))


def test_a_withdrawn_leg_winner_is_found(db: sqlite3.Connection, played: CreatedMatch) -> None:
    set_leg_winner(db, played.leg_id, None)

    drifts = verify.verify_leg(db, played.leg_id)

    assert _subjects(drifts) == ["legs.winner_team_id"]
    assert drifts[0].leg_id == played.leg_id


def test_a_missing_completion_timestamp_is_found(
    db: sqlite3.Connection, played: CreatedMatch
) -> None:
    db.execute("UPDATE legs SET completed_at = NULL WHERE id = ?", (played.leg_id,))

    assert _subjects(verify.verify_leg(db, played.leg_id)) == ["legs.completed_at"]


def test_a_stale_cache_is_found(db: sqlite3.Connection, played: CreatedMatch) -> None:
    open_leg = legs_for_match(db, played.match_id)[1]
    db.execute("UPDATE leg_team_state SET remaining = 7 WHERE leg_id = ?", (open_leg.id,))

    assert _subjects(verify.verify_leg(db, open_leg.id)) == ["leg cache"]


def test_a_cache_on_a_finished_leg_is_found(db: sqlite3.Connection, played: CreatedMatch) -> None:
    db.execute(
        "INSERT INTO leg_team_state(leg_id, team_id, match_id, remaining, is_open, darts_thrown, "
        "points) VALUES (?, ?, ?, 0, 1, 9, 0)",
        (played.leg_id, played.team_ids[0], played.match_id),
    )

    assert _subjects(verify.verify_leg(db, played.leg_id)) == ["leg cache"]


def test_a_wrong_match_winner_is_found(db: sqlite3.Connection, played: CreatedMatch) -> None:
    set_match_winner(db, played.match_id, played.team_ids[0])

    drifts = verify.verify_match(db, played.match_id)

    assert _subjects(drifts) == ["matches.winner_team_id"]
    assert drifts[0].leg_id is None
    assert "match 1: matches.winner_team_id is" in str(drifts[0])


def test_cricket_rows_on_an_x01_leg_are_found(db: sqlite3.Connection, played: CreatedMatch) -> None:
    dart = darts_for_leg(db, played.leg_id)[0]
    db.execute("INSERT INTO cricket_dart_effects VALUES (?, 20, 3, 0, 0)", (dart.id,))

    assert _subjects(verify.verify_leg(db, played.leg_id)) == ["cricket rows on an x01 leg"]


def test_a_missing_cricket_effect_is_found(db: sqlite3.Connection, players: list[int]) -> None:
    created = make_match(db, cricket("standard"), ((players[0],), (players[1],)))
    throw_labels(db, created.leg_id, ["T20", "T20"])
    dart = darts_for_leg(db, created.leg_id)[0]
    db.execute("DELETE FROM cricket_dart_effects WHERE dart_id = ?", (dart.id,))

    assert _subjects(verify.verify_leg(db, created.leg_id)) == [
        f"cricket effect on dart {dart.seq_in_leg}"
    ]


def test_a_missing_point_event_is_found(db: sqlite3.Connection, players: list[int]) -> None:
    created = make_match(db, cricket("cutthroat"), tuple((p,) for p in players[:3]))
    throw_labels(db, created.leg_id, ["T20", "T20"])
    db.execute("DELETE FROM cricket_point_events WHERE leg_id = ?", (created.leg_id,))

    drifts = verify.verify_leg(db, created.leg_id)

    assert _subjects(drifts) == ["cricket_point_events"]
    assert "[]" in drifts[0].stored


def test_a_point_event_sent_to_the_wrong_team_is_found(
    db: sqlite3.Connection, players: list[int]
) -> None:
    created = make_match(db, cricket("standard"), ((players[0],), (players[1],)))
    throw_labels(db, created.leg_id, ["T20", "T20"])
    db.execute("UPDATE cricket_point_events SET recipient_team_id = ?", (created.team_ids[1],))

    assert _subjects(verify.verify_leg(db, created.leg_id)) == ["cricket_point_events"]


def test_a_drift_reads_as_a_sentence(db: sqlite3.Connection, played: CreatedMatch) -> None:
    set_leg_winner(db, played.leg_id, None)

    (drift,) = verify.verify_leg(db, played.leg_id)

    assert str(drift) == f"leg {played.leg_id}: legs.winner_team_id is None, expected 1"
