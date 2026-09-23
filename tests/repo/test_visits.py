"""Visits: opening one, growing it, reading it back and removing it."""

import sqlite3

import pytest
from repofixtures import count, open_visit

from darts.repo.darts import NewDart, append_dart
from darts.repo.errors import NotFoundError
from darts.repo.matches import CreatedMatch
from darts.repo.visits import (
    NewVisit,
    create_visit,
    delete_visit,
    get_visit,
    update_visit,
    visits_for_leg,
)


def _visit(created: CreatedMatch, player_id: int, index: int, **changes: object) -> NewVisit:
    fields: dict[str, object] = {
        "leg_id": created.leg_id,
        "match_id": created.match_id,
        "team_id": created.team_ids[0],
        "player_id": player_id,
        "visit_index": index,
        "team_visit_index": index,
        "score_before": 501,
        "score_after": 501,
    }
    return NewVisit(**{**fields, **changes})


def test_create_returns_the_stored_visit(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    stored = create_visit(db, _visit(doubles, players[0], 0, score_after=441))

    assert stored.id
    assert stored.score_before == 501
    assert stored.score_after == 441
    assert stored.is_bust is False
    assert stored.is_complete is False
    assert get_visit(db, stored.id) == stored


def test_get_rejects_an_unknown_id(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no visit with id 404"):
        get_visit(db, 404)


def test_visits_come_back_in_visit_index_order(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    second = create_visit(db, _visit(doubles, players[2], 1))
    first = create_visit(db, _visit(doubles, players[0], 0))

    assert [v.id for v in visits_for_leg(db, doubles.leg_id)] == [first.id, second.id]


def test_visits_for_an_untouched_leg_is_empty(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    assert visits_for_leg(db, doubles.leg_id) == []


def test_update_rewrites_the_three_mutable_fields(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    stored = create_visit(db, _visit(doubles, players[0], 0, score_after=441))
    updated = update_visit(db, stored.id, score_after=501, is_bust=True, is_complete=True)

    assert updated.score_after == 501
    assert updated.is_bust is True
    assert updated.is_complete is True
    assert updated.score_before == stored.score_before
    assert get_visit(db, stored.id) == updated


def test_update_rejects_an_unknown_id(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no visit with id 404"):
        update_visit(db, 404, score_after=0, is_bust=False, is_complete=True)


def test_a_bust_must_leave_the_score_where_it_found_it(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    """The schema's own CHECK, reached through `update_visit`."""
    stored = create_visit(db, _visit(doubles, players[0], 0, score_after=441))
    with pytest.raises(sqlite3.IntegrityError):
        update_visit(db, stored.id, score_after=441, is_bust=True, is_complete=True)


def test_delete_takes_the_visits_darts_with_it(
    db: sqlite3.Connection, doubles: CreatedMatch, players: list[int]
) -> None:
    visit = open_visit(db, doubles, player_id=players[0])
    for index in range(3):
        append_dart(
            db,
            NewDart(
                visit_id=visit,
                leg_id=doubles.leg_id,
                team_id=doubles.team_ids[0],
                player_id=players[0],
                seq_in_leg=index,
                dart_index=index,
                segment=20,
                multiplier=1,
                counted=True,
                client_dart_id=f"gone-{index}",
            ),
        )
    assert count(db, "darts") == 3

    delete_visit(db, visit)

    assert count(db, "darts") == 0
    assert count(db, "visits") == 0
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_delete_rejects_an_unknown_id(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no visit with id 404"):
        delete_visit(db, 404)
