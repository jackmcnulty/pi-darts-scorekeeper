"""The dart ledger: appending one, and reading a leg back in throwing order."""

import sqlite3

import pytest
from repofixtures import open_visit

from darts.db.connection import transaction
from darts.repo.darts import Dart, NewDart, append_dart, darts_for_leg, get_dart
from darts.repo.errors import NotFoundError
from darts.repo.legs import create_leg
from darts.repo.matches import CreatedMatch


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
        "multiplier": 1,
        "counted": True,
        "client_dart_id": f"dart-{seq}",
    }
    return NewDart(**{**fields, **changes})


def test_append_returns_the_stored_dart(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, doubles, player_id=players[0])
    stored = append_dart(db, _dart(doubles, players[0], visit, 0))

    assert stored.id
    assert stored.thrown_at
    assert stored.segment == 20
    assert stored.multiplier == 1
    assert stored.counted is True
    assert stored.caused_bust is False
    assert stored.was_checkout_attempt is False
    assert stored.client_dart_id == "dart-0"
    assert get_dart(db, stored.id) == stored


def test_score_is_the_raw_board_value(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, doubles, player_id=players[0])
    treble_twenty = append_dart(db, _dart(doubles, players[0], visit, 0, multiplier=3))
    assert treble_twenty.score == 60

    miss = append_dart(db, _dart(doubles, players[0], visit, 1, segment=0, multiplier=0))
    assert miss.score == 0


def test_flags_round_trip_as_booleans(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    """SQLite stores 0 and 1; the repository hands back True and False."""
    visit = open_visit(db, doubles, player_id=players[0])
    stored = append_dart(
        db,
        _dart(
            doubles,
            players[0],
            visit,
            0,
            counted=False,
            caused_bust=True,
            was_checkout_attempt=True,
        ),
    )
    assert stored.counted is False
    assert stored.caused_bust is True
    assert stored.was_checkout_attempt is True
    assert isinstance(stored.counted, bool)


def test_darts_load_in_seq_in_leg_order(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    """The acceptance criterion. Inserted out of order to make the sort matter.

    Two visits, because `UNIQUE (visit_id, dart_index)` and the 0..2 range on
    `dart_index` together cap a visit at three darts.
    """
    visits = {
        0: open_visit(db, doubles, player_id=players[0], visit_index=0),
        1: open_visit(db, doubles, player_id=players[0], visit_index=1),
    }
    for seq in (2, 0, 1, 5, 3, 4):
        append_dart(db, _dart(doubles, players[0], visits[seq // 3], seq))

    loaded = darts_for_leg(db, doubles.leg_id)
    assert [dart.seq_in_leg for dart in loaded] == [0, 1, 2, 3, 4, 5]
    assert all(isinstance(dart, Dart) for dart in loaded)


def test_darts_span_visits_and_players_in_one_order(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    """seq_in_leg is the leg's counter, so it orders across visits too."""
    first = open_visit(db, doubles, player_id=players[0], visit_index=0)
    second = open_visit(db, doubles, player_id=players[2], visit_index=1)
    append_dart(db, _dart(doubles, players[2], second, 3))
    append_dart(db, _dart(doubles, players[0], first, 0))

    loaded = darts_for_leg(db, doubles.leg_id)
    assert [(d.seq_in_leg, d.player_id) for d in loaded] == [(0, players[0]), (3, players[2])]


def test_darts_of_a_leg_exclude_other_legs(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, doubles, player_id=players[0])
    append_dart(db, _dart(doubles, players[0], visit, 0))
    with transaction(db):
        other = create_leg(
            db, match_id=doubles.match_id, leg_index=1, starting_team_id=doubles.team_ids[1]
        )

    assert len(darts_for_leg(db, doubles.leg_id)) == 1
    assert darts_for_leg(db, other) == []


def test_empty_leg_returns_no_darts(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    assert darts_for_leg(db, doubles.leg_id) == []


def test_get_missing_dart_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no dart with id 99"):
        get_dart(db, 99)


def test_client_dart_id_is_unique(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    """#15 leans on this for idempotency; the column enforces it from the start."""
    visit = open_visit(db, doubles, player_id=players[0])
    append_dart(db, _dart(doubles, players[0], visit, 0))
    with pytest.raises(sqlite3.IntegrityError):
        append_dart(db, _dart(doubles, players[0], visit, 1, client_dart_id="dart-0"))


def test_seq_in_leg_is_unique_within_a_leg(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, doubles, player_id=players[0])
    append_dart(db, _dart(doubles, players[0], visit, 0))
    with pytest.raises(sqlite3.IntegrityError):
        append_dart(db, _dart(doubles, players[0], visit, 0, client_dart_id="other", dart_index=1))
