"""What #15 added to the dart ledger: lookup by key, voiding, deletion, cricket rows.

`test_darts.py` covers appending and reading. These are the play-path
operations that came with the service: finding a dart by its idempotency key,
taking back the verdict on a busted one, hard-deleting one, and the two cricket
tables that hang off a single dart.
"""

import sqlite3

import pytest
from repofixtures import CRICKET, count, open_visit

from darts.db.connection import transaction
from darts.repo.darts import (
    CricketEffect,
    NewDart,
    PointAward,
    append_dart,
    cricket_effects_for_leg,
    dart_for_client_id,
    darts_for_leg,
    delete_dart,
    get_dart,
    point_awards_for_leg,
    record_cricket_effect,
    record_point_awards,
    set_counted,
)
from darts.repo.errors import NotFoundError
from darts.repo.matches import CreatedMatch, TeamSpec, create_match


def _dart(
    created: CreatedMatch, player_id: int, visit_id: int, seq: int, **changes: object
) -> NewDart:
    fields: dict[str, object] = {
        "visit_id": visit_id,
        "leg_id": created.leg_id,
        "team_id": created.team_ids[0],
        "player_id": player_id,
        "seq_in_leg": seq,
        "dart_index": seq % 3,
        "segment": 20,
        "multiplier": 3,
        "counted": True,
        "client_dart_id": f"dart-{seq}",
    }
    return NewDart(**{**fields, **changes})


@pytest.fixture
def cricket_match(db: sqlite3.Connection, players: list[int]) -> CreatedMatch:
    """A three-way solo cricket match, so a dart can pay two opponents at once."""
    with transaction(db):
        return create_match(db, CRICKET, tuple(TeamSpec((p,)) for p in players[:3]))


def test_a_key_finds_its_dart(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, doubles, player_id=players[0])
    stored = append_dart(db, _dart(doubles, players[0], visit, 0))

    assert dart_for_client_id(db, "dart-0") == stored


def test_an_unused_key_finds_nothing(db: sqlite3.Connection) -> None:
    assert dart_for_client_id(db, "never-thrown") is None


def test_set_counted_takes_back_the_verdict(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, doubles, player_id=players[0])
    stored = append_dart(db, _dart(doubles, players[0], visit, 0))

    set_counted(db, stored.id, False)
    assert get_dart(db, stored.id).counted is False

    set_counted(db, stored.id, True)
    assert get_dart(db, stored.id).counted is True


def test_set_counted_rejects_an_unknown_id(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no dart with id 404"):
        set_counted(db, 404, False)


def test_delete_removes_one_dart_and_leaves_its_visit(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, doubles, player_id=players[0])
    first = append_dart(db, _dart(doubles, players[0], visit, 0))
    second = append_dart(db, _dart(doubles, players[0], visit, 1))

    delete_dart(db, second.id)

    assert [d.id for d in darts_for_leg(db, doubles.leg_id)] == [first.id]
    assert count(db, "visits") == 1


def test_delete_rejects_an_unknown_id(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no dart with id 404"):
        delete_dart(db, 404)


def test_cricket_rows_round_trip(
    db: sqlite3.Connection, cricket_match: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, cricket_match, player_id=players[0])
    dart = append_dart(db, _dart(cricket_match, players[0], visit, 0))
    effect = CricketEffect(
        dart_id=dart.id, target=20, counted_marks=2, surplus_marks=1, wasted=False
    )
    awards = [
        PointAward(dart.id, cricket_match.leg_id, cricket_match.match_id, team_id, 20)
        for team_id in cricket_match.team_ids[1:]
    ]
    record_cricket_effect(db, effect)
    record_point_awards(db, awards)

    assert cricket_effects_for_leg(db, cricket_match.leg_id) == {dart.id: effect}
    assert point_awards_for_leg(db, cricket_match.leg_id) == awards


def test_a_dart_that_hit_nothing_still_records_an_effect(
    db: sqlite3.Connection, cricket_match: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, cricket_match, player_id=players[0])
    dart = append_dart(db, _dart(cricket_match, players[0], visit, 0, segment=12, multiplier=1))
    effect = CricketEffect(
        dart_id=dart.id, target=None, counted_marks=0, surplus_marks=0, wasted=False
    )
    record_cricket_effect(db, effect)

    assert cricket_effects_for_leg(db, cricket_match.leg_id) == {dart.id: effect}


def test_recording_no_awards_writes_nothing(
    db: sqlite3.Connection, cricket_match: CreatedMatch
) -> None:
    record_point_awards(db, [])
    assert point_awards_for_leg(db, cricket_match.leg_id) == []


def test_deleting_the_dart_cascades_to_both_cricket_tables(
    db: sqlite3.Connection, cricket_match: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, cricket_match, player_id=players[0])
    dart = append_dart(db, _dart(cricket_match, players[0], visit, 0))
    record_cricket_effect(db, CricketEffect(dart.id, 20, 3, 0, False))
    record_point_awards(
        db,
        [
            PointAward(dart.id, cricket_match.leg_id, cricket_match.match_id, t, 20)
            for t in cricket_match.team_ids[1:]
        ],
    )
    assert count(db, "cricket_dart_effects") == 1
    assert count(db, "cricket_point_events") == 2

    delete_dart(db, dart.id)

    assert count(db, "cricket_dart_effects") == 0
    assert count(db, "cricket_point_events") == 0
    assert count(db, "visits") == 1
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
