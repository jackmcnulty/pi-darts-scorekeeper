"""#31: the Pi backs itself up on every start, then every 24 hours.

Four claims. A start takes a backup of the database the boot check approved,
without the first request waiting for it. The job repeats on its interval and
stops promptly, waiting for a copy in progress so the shutdown checkpoint does
not run under it. A database with no matches is never backed up, because an
empty backup would become the newest valid one and the next boot check would
restore it. And a backup that fails is logged, never raised.

The 24-hour interval itself is not waited out: the scheduler takes its interval
in seconds and is exercised at a hundredth of one.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from apifixtures import corrupt, make_settings
from fastapi.testclient import TestClient
from playfixtures import X01_501, new_match

from darts.api.main import create_app
from darts.db import backup
from darts.db.durability import read_only_uri
from darts.services import auto_backup


def _backups(directory: Path) -> list[Path]:
    return sorted(directory.glob("darts-*.db")) if directory.exists() else []


def _with_a_match(tmp_path: Path) -> Path:
    """A database with one match in it, left behind by an app with backups off."""
    with TestClient(create_app(make_settings(tmp_path))) as client:
        new_match(client, X01_501)
    return tmp_path / "darts.db"


def _matches_in(path: Path) -> int:
    conn = sqlite3.connect(read_only_uri(path), uri=True)
    try:
        return int(conn.execute("SELECT count(*) FROM matches").fetchone()[0])
    finally:
        conn.close()


# --- Startup and shutdown --------------------------------------------------


def test_every_start_takes_a_backup(tmp_path: Path) -> None:
    _with_a_match(tmp_path)
    settings = make_settings(tmp_path, backup_interval_hours=24)

    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/healthz").status_code == 200
        assert app.state.backups is not None
    # Shutdown waits for the run in progress, so it has finished by now.
    (taken,) = _backups(settings.backup_dir)
    assert _matches_in(taken) == 1
    assert taken.with_name(taken.name + ".json").is_file()


def test_each_start_backs_up_and_retention_collapses_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The Pi is power-cycled constantly. Every start takes a backup, and the
    hourly bucket keeps only the newest, so a burst of boots costs one file."""
    _with_a_match(tmp_path)
    settings = make_settings(tmp_path, backup_interval_hours=24)
    with caplog.at_level(logging.INFO, logger="darts.services.auto_backup"):
        for _ in range(3):
            with TestClient(create_app(settings)):
                pass
    assert caplog.messages.count("automatic backup") == 3
    assert len(_backups(settings.backup_dir)) == 1


def test_the_backup_is_of_what_the_boot_check_restored(tmp_path: Path) -> None:
    """A damaged database is repaired first, and the repair is what gets saved."""
    database = _with_a_match(tmp_path)
    settings = make_settings(tmp_path, backup_interval_hours=24)
    (older,) = (backup.create(database, backup_dir=settings.backup_dir).backup.path,)
    corrupt(database)

    app = create_app(settings)
    with TestClient(app):
        assert app.state.recovery.auto_restored is True

    newest = _backups(settings.backup_dir)[-1]
    assert newest != older
    assert _matches_in(newest) == 1


def test_zero_turns_automatic_backups_off(tmp_path: Path) -> None:
    _with_a_match(tmp_path)
    settings = make_settings(tmp_path, backup_interval_hours=0)
    app = create_app(settings)
    with TestClient(app):
        assert app.state.backups is None
    assert _backups(settings.backup_dir) == []


def test_a_wiped_card_is_not_backed_up(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The boot check makes an empty, healthy database; it must not become a backup."""
    settings = make_settings(tmp_path, backup_interval_hours=24)
    with (
        caplog.at_level(logging.INFO, logger="darts.services.auto_backup"),
        TestClient(create_app(settings)),
    ):
        pass
    assert _backups(settings.backup_dir) == []
    assert "automatic backup skipped: no matches yet" in caplog.messages


# --- What gets backed up ---------------------------------------------------


def test_a_database_with_a_match_is_backed_up(tmp_path: Path) -> None:
    database = _with_a_match(tmp_path)
    result = auto_backup.take(database, tmp_path / "backups")
    assert result is not None
    assert result.backup.path.parent == tmp_path / "backups"


def test_players_alone_are_not_history(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.post("/api/players", json={"display_name": "Ana"}).status_code == 201
    assert auto_backup.has_history(tmp_path / "darts.db") is False
    assert auto_backup.take(tmp_path / "darts.db", tmp_path / "backups") is None
    assert not (tmp_path / "backups").exists()


def test_asking_never_creates_a_database(tmp_path: Path) -> None:
    missing = tmp_path / "darts.db"
    assert auto_backup.has_history(missing) is False
    assert auto_backup.take(missing, tmp_path / "backups") is None
    assert list(tmp_path.iterdir()) == []


def test_a_failed_backup_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = _with_a_match(tmp_path)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("a file where the backup directory should be")

    with caplog.at_level(logging.ERROR, logger="darts.services.auto_backup"):
        assert auto_backup.take(database, blocked) is None
    assert "automatic backup failed" in caplog.messages


# --- The scheduler ---------------------------------------------------------


def _wait_for(condition, timeout: float = 10.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def test_it_runs_at_once_and_then_on_its_interval() -> None:
    calls: list[float] = []
    scheduler = auto_backup.BackupScheduler(lambda: calls.append(time.monotonic()), 0.01)
    scheduler.start()
    try:
        _wait_for(lambda: len(calls) >= 3)
    finally:
        assert scheduler.stop(timeout=5) is True
    gaps = [b - a for a, b in zip(calls, calls[1:], strict=False)]
    assert all(gap >= 0.009 for gap in gaps), gaps


def test_the_first_run_does_not_wait_for_the_interval() -> None:
    ran = threading.Event()
    scheduler = auto_backup.BackupScheduler(ran.set, 24 * 3600)
    scheduler.start()
    assert ran.wait(5)
    started = time.monotonic()
    assert scheduler.stop(timeout=5) is True
    assert time.monotonic() - started < 1, "stopping waited out the 24-hour interval"
    assert scheduler.runs == 1


def test_a_failing_job_does_not_stop_the_schedule(caplog: pytest.LogCaptureFixture) -> None:
    def explode() -> None:
        raise RuntimeError("disk full")

    scheduler = auto_backup.BackupScheduler(explode, 0.01)
    with caplog.at_level(logging.ERROR, logger="darts.services.auto_backup"):
        scheduler.start()
        try:
            _wait_for(lambda: scheduler.runs >= 2)
        finally:
            scheduler.stop(timeout=5)
    assert "scheduled job failed" in caplog.messages


def test_stopping_waits_for_a_run_in_progress(caplog: pytest.LogCaptureFixture) -> None:
    inside = threading.Event()
    release = threading.Event()

    def slow() -> None:
        inside.set()
        release.wait(10)

    scheduler = auto_backup.BackupScheduler(slow, 3600)
    scheduler.start()
    assert inside.wait(5)

    with caplog.at_level(logging.ERROR, logger="darts.services.auto_backup"):
        assert scheduler.stop(timeout=0.05) is False
    assert "automatic backup still running at shutdown" in caplog.messages

    release.set()
    assert scheduler.stop(timeout=5) is True
    assert scheduler.runs == 1
