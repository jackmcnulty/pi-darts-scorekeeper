"""`client_dart_id`: a retry is free, a reused key is not.

A double tap on a phone, or a retry after the Pi's wifi blinks, must not record
two darts. The key is the frontend's promise that two requests are the same
throw, and the service holds it to that promise in both directions -- the same
key with a different payload is a bug, not a retry.
"""

import sqlite3

import pytest
from servicefixtures import throw_labels

from darts.engine.throws import Throw
from darts.repo.darts import darts_for_leg
from darts.repo.matches import CreatedMatch
from darts.repo.visits import visits_for_leg
from darts.services import play
from darts.services.errors import IdempotencyConflictError


def test_the_same_key_twice_inserts_one_row_and_returns_the_same_state(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    first = play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="same")
    second = play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="same")

    assert second == first
    assert len(darts_for_leg(db, solo.leg_id)) == 1
    assert len(visits_for_leg(db, solo.leg_id)) == 1


def test_two_keys_with_identical_payloads_record_two_darts(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    """Two real T20s in a row are ordinary darts; only the key makes a retry."""
    play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="one")
    state = play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="two")

    assert len(darts_for_leg(db, solo.leg_id)) == 2
    assert state.current_leg.teams[0].remaining == 181


def test_a_retry_mid_leg_is_still_a_no_op(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    throw_labels(db, solo.leg_id, ["T20", "T20", "T20", "T19"])
    before = play.state(db, solo.leg_id)

    repeat = play.throw(
        db, leg_id=solo.leg_id, dart=Throw.parse("T19"), client_dart_id=f"k-{solo.leg_id}-3"
    )

    assert repeat == before
    assert len(darts_for_leg(db, solo.leg_id)) == 4


def test_a_reused_key_with_a_different_target_is_a_conflict(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="reused")

    with pytest.raises(IdempotencyConflictError, match="different dart"):
        play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T19"), client_dart_id="reused")

    assert len(darts_for_leg(db, solo.leg_id)) == 1


def test_a_reused_key_with_a_different_multiplier_is_a_conflict(
    db: sqlite3.Connection, solo: CreatedMatch
) -> None:
    play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="reused")

    with pytest.raises(IdempotencyConflictError, match="different dart"):
        play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("D20"), client_dart_id="reused")


def test_a_key_from_another_leg_is_a_conflict_rather_than_an_integrity_error(
    db: sqlite3.Connection, solo: CreatedMatch, players: list[int]
) -> None:
    """The column is unique database-wide, so the lookup is too: a key that
    turns up on the wrong leg is reported, not left to SQLite."""
    from servicefixtures import X01_301, make_match

    other = make_match(db, X01_301, ((players[2],), (players[3],)))
    play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="crossed")

    with pytest.raises(IdempotencyConflictError, match="different dart"):
        play.throw(db, leg_id=other.leg_id, dart=Throw.parse("T20"), client_dart_id="crossed")

    assert darts_for_leg(db, other.leg_id) == []


def test_an_undone_key_can_be_used_again(db: sqlite3.Connection, solo: CreatedMatch) -> None:
    """Undo is a hard delete, so the key goes with the dart and is free again."""
    play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T20"), client_dart_id="recycled")
    play.undo(db, solo.leg_id)

    state = play.throw(db, leg_id=solo.leg_id, dart=Throw.parse("T19"), client_dart_id="recycled")

    assert state.current_leg.teams[0].remaining == 244
    assert len(darts_for_leg(db, solo.leg_id)) == 1
