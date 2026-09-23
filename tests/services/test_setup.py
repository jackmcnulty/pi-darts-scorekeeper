"""Setup transactions commit together or leave no partial match."""

import sqlite3

import pytest
from servicefixtures import X01_301

from darts.engine.throws import Throw
from darts.repo import matches
from darts.repo.errors import NotFoundError
from darts.services import play, setup
from darts.services.errors import MatchAbandonedError


def test_create_match_rolls_back_partial_rows(
    db: sqlite3.Connection, players: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> int:
        raise RuntimeError("failed creating leg")

    monkeypatch.setattr(matches, "create_leg", fail)
    with pytest.raises(RuntimeError, match="failed creating leg"):
        setup.create_match(db, X01_301, [matches.TeamSpec((p,)) for p in players[:2]])
    assert not db.in_transaction
    for table in ("matches", "teams", "team_members", "legs"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_abandon_preserves_play_and_prevents_further_mutations(
    db: sqlite3.Connection,
    solo: matches.CreatedMatch,
) -> None:
    play.throw(db, leg_id=solo.leg_id, dart=Throw(20, 1), client_dart_id="before-abandon")
    tables = ("darts", "visits", "legs", "team_members", "leg_team_state")
    before = {t: [tuple(r) for r in db.execute(f"SELECT * FROM {t}")] for t in tables}
    abandoned = setup.abandon_match(db, solo.match_id)
    assert abandoned.status is matches.MatchStatus.ABANDONED
    with pytest.raises(MatchAbandonedError):
        play.throw(db, leg_id=solo.leg_id, dart=Throw(20, 1), client_dart_id="after-abandon")
    with pytest.raises(MatchAbandonedError):
        play.undo(db, solo.leg_id)
    assert setup.abandon_match(db, solo.match_id) == abandoned
    assert {t: [tuple(r) for r in db.execute(f"SELECT * FROM {t}")] for t in tables} == before
    # Retrying an existing dart remains a read-only idempotent operation.
    play.throw(db, leg_id=solo.leg_id, dart=Throw(20, 1), client_dart_id="before-abandon")


def test_missing_abandon_repository_row(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        matches.abandon_match(db, 999)
