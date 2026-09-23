"""`darts-verify`: exit status, and what it prints on each stream."""

import sqlite3
from pathlib import Path

import pytest
from seed import build
from servicefixtures import SCRIPT_301, throw_labels

from darts.repo.legs import set_leg_winner
from darts.repo.matches import CreatedMatch
from darts.tools.verify import main


def _path(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA database_list").fetchone()["file"])


def test_a_clean_database_exits_zero_and_says_so(
    db: sqlite3.Connection, solo: CreatedMatch, capsys: pytest.CaptureFixture[str]
) -> None:
    throw_labels(db, solo.leg_id, SCRIPT_301)

    assert main([_path(db)]) == 0

    captured = capsys.readouterr()
    assert "no drift" in captured.out
    assert "2 leg(s) across 1 match(es)" in captured.out
    assert captured.err == ""


def test_quiet_says_nothing_when_there_is_nothing_to_say(
    db: sqlite3.Connection, solo: CreatedMatch, capsys: pytest.CaptureFixture[str]
) -> None:
    throw_labels(db, solo.leg_id, SCRIPT_301)

    assert main([_path(db), "--quiet"]) == 0

    assert capsys.readouterr().out == ""


def test_the_seeded_fixture_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conn = build(tmp_path / "seeded.db")
    try:
        path = _path(conn)
    finally:
        conn.close()

    assert main([path]) == 0
    assert "8 leg(s) across 5 match(es)" in capsys.readouterr().out


def test_drift_exits_one_and_names_every_value(
    db: sqlite3.Connection, solo: CreatedMatch, capsys: pytest.CaptureFixture[str]
) -> None:
    throw_labels(db, solo.leg_id, SCRIPT_301)
    set_leg_winner(db, solo.leg_id, None)

    assert main([_path(db), "--quiet"]) == 1

    err = capsys.readouterr().err
    assert "legs.winner_team_id" in err
    assert "drifted value(s) across 1 match(es)" in err


def test_a_database_that_cannot_be_opened_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope" / "absent.db"

    assert main([str(missing)]) == 1

    assert "verify failed" in capsys.readouterr().err
