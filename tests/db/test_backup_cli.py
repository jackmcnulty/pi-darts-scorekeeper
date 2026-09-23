"""darts-backup and darts-restore: argument handling, confirmation, exit codes."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from dbfixtures import add_visit, dump, scaffold

from darts.db.backup import create, default_backup_dir, discover
from darts.db.connection import connection, transaction
from darts.tools.backup import main as backup_main
from darts.tools.restore import main as restore_main


@pytest.fixture
def played(tmp_path: Path) -> Iterator[Path]:
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)
        for index in range(5):
            with transaction(conn):
                add_visit(conn, index)
    yield database


def answer(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    """Pretend a terminal is attached and that the operator typed `reply`."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: reply)


def test_backup_writes_beside_the_database_and_reports_what_it_did(
    played: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert backup_main([str(played)]) == 0

    out = capsys.readouterr().out
    (backup,) = discover(default_backup_dir(played), "darts")
    assert str(backup.path) in out
    assert "schema version 2" in out
    assert "pruned 0" in out
    manifest = json.loads(backup.manifest_path.read_text())
    assert manifest["row_counts"]["darts"] == 15


def test_backup_honours_an_explicit_destination_and_retention(
    played: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = played.parent / "elsewhere"
    assert backup_main([str(played), "--backup-dir", str(elsewhere)]) == 0
    assert backup_main([str(played), "--backup-dir", str(elsewhere)]) == 0

    # Both land in one hour bucket, so the second run prunes the first.
    assert len(discover(elsewhere, "darts")) == 1
    assert "pruned 1" in capsys.readouterr().out
    assert not default_backup_dir(played).exists()


def test_backup_can_be_told_to_keep_everything(played: Path) -> None:
    elsewhere = played.parent / "kept"
    for _ in range(3):
        assert backup_main([str(played), "--backup-dir", str(elsewhere), "--no-prune"]) == 0

    assert len(discover(elsewhere, "darts")) == 3


def test_backup_reports_a_missing_database_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert backup_main([str(tmp_path / "absent.db")]) == 1
    assert "backup failed: no database to back up" in capsys.readouterr().err


def test_restore_uses_the_newest_valid_backup_by_default(
    played: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = create(played, prune_old=False)
    before = dump(played)
    with connection(played) as conn, transaction(conn):
        add_visit(conn, 5)
    answer(monkeypatch, "y")

    assert restore_main([str(played)]) == 0

    assert dump(played) == before
    out = capsys.readouterr().out
    assert str(backup.backup.path) in out
    assert "previous database kept at" in out


def test_restore_accepts_an_explicit_backup(played: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backup = create(played, prune_old=False)
    played.unlink()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    # Nothing to overwrite, so no confirmation is asked for.
    assert restore_main([str(played), "--from", str(backup.backup.path)]) == 0
    assert dump(played) == dump(backup.backup.path)


def test_restore_aborts_when_the_operator_declines(
    played: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    create(played, prune_old=False)
    with connection(played) as conn, transaction(conn):
        add_visit(conn, 5)
    after = dump(played)
    answer(monkeypatch, "n")

    assert restore_main([str(played)]) == 1

    assert capsys.readouterr().err.endswith("aborted\n")
    assert dump(played) == after
    assert list(played.parent.glob("*.replaced-*")) == []


def test_restore_refuses_to_assume_consent_without_a_terminal(
    played: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """pytest leaves stdin non-interactive, which is how the Pi's timer runs too."""
    create(played, prune_old=False)

    assert restore_main([str(played)]) == 1

    assert "re-run with --force" in capsys.readouterr().err


def test_force_overwrites_without_asking(played: Path) -> None:
    backup = create(played, prune_old=False)
    with connection(played) as conn, transaction(conn):
        add_visit(conn, 5)

    assert restore_main([str(played), "--force"]) == 0
    assert dump(played) == dump(backup.backup.path)


def test_restore_reports_when_there_is_nothing_to_restore_from(
    played: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert restore_main([str(played), "--backup-dir", str(played.parent / "empty")]) == 1
    assert "no valid backup found" in capsys.readouterr().err


def test_restore_reports_a_damaged_backup_without_a_traceback(
    played: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rubbish = played.parent / "rubbish.db"
    rubbish.write_bytes(b"not a database")

    assert restore_main([str(played), "--from", str(rubbish), "--force"]) == 1

    assert "restore failed: refusing to restore a damaged backup" in capsys.readouterr().err
    assert dump(played)["visits"]
