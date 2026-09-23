"""darts-snapshot: where it writes, what it prints, and how it fails.

This is what a cron job or #30's setup will actually invoke, so the exit code
and the message matter as much as the file: an unattended caller reads the
former, and a person reading a log reads the latter.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from dbfixtures import add_visit, scaffold

from darts.db.connection import connection, transaction
from darts.services.snapshot import default_snapshot_dir
from darts.tools.snapshot import main


@pytest.fixture
def played(tmp_path: Path) -> Iterator[Path]:
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)
        for index in range(5):
            with transaction(conn):
                add_visit(conn, index)
    yield database


def test_it_writes_beside_the_database_and_reports_what_it_did(
    played: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(played)]) == 0

    directory = default_snapshot_dir(played)
    snapshot = directory / "darts-latest.db"
    manifest = directory / "snapshot.json"
    assert snapshot.is_file()
    assert manifest.is_file()

    out = capsys.readouterr().out
    assert str(snapshot) in out
    assert str(manifest) in out
    assert "schema version 2" in out
    # The row total it reports is the manifest's own, not a number typed here.
    total = sum(json.loads(manifest.read_text(encoding="utf-8"))["row_counts"].values())
    assert f"{total} row(s)" in out
    assert total >= 15, "the fixture wrote 5 visits of 3 darts"


def test_the_manifest_counts_what_the_snapshot_holds(played: Path) -> None:
    assert main([str(played)]) == 0

    manifest = json.loads(
        (default_snapshot_dir(played) / "snapshot.json").read_text(encoding="utf-8")
    )
    assert manifest["snapshot"] == "darts-latest.db"
    assert manifest["schema_version"] == 2
    assert manifest["row_counts"]["darts"] == 15
    assert manifest["row_counts"]["visits"] == 5
    assert manifest["source"] == str(played)


def test_the_destination_can_be_overridden(played: Path, tmp_path: Path) -> None:
    """#30 points this at whatever directory Samba is sharing."""
    elsewhere = tmp_path / "share"

    assert main([str(played), "--snapshot-dir", str(elsewhere)]) == 0

    assert (elsewhere / "darts-latest.db").is_file()
    assert not default_snapshot_dir(played).exists()


def test_running_it_twice_replaces_rather_than_accumulates(played: Path) -> None:
    """A cron job every ten minutes must not fill the card."""
    assert main([str(played)]) == 0
    assert main([str(played)]) == 0

    assert len(list(default_snapshot_dir(played).iterdir())) == 2


def test_a_missing_database_is_a_message_on_stderr_and_a_nonzero_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unattended caller has only the exit code to go on, so it must be right."""
    assert main([str(tmp_path / "absent.db")]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "snapshot failed" in captured.err
    assert "no database to snapshot" in captured.err


def test_a_damaged_database_does_not_publish_a_snapshot(
    played: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("darts.db.artifact.verify_file", lambda path: "injected damage")

    assert main([str(played)]) == 1

    assert "snapshot failed" in capsys.readouterr().err
    assert list(default_snapshot_dir(played).iterdir()) == []
