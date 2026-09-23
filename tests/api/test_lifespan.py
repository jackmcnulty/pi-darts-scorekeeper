"""Startup and shutdown: the boot check, and the WAL checkpoint on the way out.

The SIGTERM test runs a real uvicorn in a real process and signals it for real,
following the precedent in `tests/db/test_durability.py`. A `with TestClient()`
block proves the shutdown hook runs; it does not prove SIGTERM reaches it, and
SIGTERM is what Docker sends when the Pi is switched off.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from apifixtures import make_settings, wal
from fastapi.testclient import TestClient

from darts.api.main import create_app
from darts.config import ENV_PREFIX
from darts.db.connection import connect, connection
from darts.db.recovery import DatabaseState, check_and_recover

#: Generous: a cold interpreter plus uvicorn's startup on a loaded CI runner.
BOOT_TIMEOUT = 60.0
EXIT_TIMEOUT = 30.0


def test_startup_runs_the_boot_check(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    assert app.state.recovery is None

    with TestClient(app):
        assert app.state.recovery is not None
        assert app.state.recovery.state is DatabaseState.HEALTHY
    assert (tmp_path / "darts.db").is_file(), "startup should have created the database"


def test_startup_repairs_before_serving(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Whatever recovery did, the database is serviceable by the first request."""
    with (
        caplog.at_level(logging.INFO, logger="darts.api.main"),
        TestClient(create_app(make_settings(tmp_path))) as client,
    ):
        assert client.get("/api/healthz").status_code == 200

    (record,) = [r for r in caplog.records if r.getMessage() == "boot check complete"]
    assert record.state is DatabaseState.HEALTHY  # type: ignore[attr-defined]


def test_shutdown_truncates_the_wal(tmp_path: Path) -> None:
    """The file left on the card is self-contained, with nothing to replay."""
    settings = make_settings(tmp_path)
    check_and_recover(settings.db_path)

    # An idle connection held open across the shutdown, so SQLite does not
    # simply delete the WAL when the server's own connection closes -- which
    # would leave this test passing without any checkpoint having happened.
    with connection(settings.db_path):
        with TestClient(create_app(settings)):
            pass
        assert wal(settings.db_path).exists()
        assert wal(settings.db_path).stat().st_size == 0


def test_a_busy_checkpoint_is_reported_rather_than_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """SQLite returns busy without error, so a silent hook would lie about it."""
    settings = make_settings(tmp_path)
    check_and_recover(settings.db_path)

    blocker = connect(settings.db_path)
    try:
        blocker.execute("BEGIN")
        blocker.execute("SELECT count(*) FROM players").fetchone()
        with (
            caplog.at_level(logging.INFO, logger="darts.api.main"),
            TestClient(create_app(settings)),
        ):
            pass
    finally:
        blocker.close()

    (record,) = [r for r in caplog.records if r.getMessage() == "shutdown checkpoint"]
    assert record.levelno == logging.ERROR
    assert record.busy is True  # type: ignore[attr-defined]
    assert record.truncated is False  # type: ignore[attr-defined]


def test_a_shutdown_that_cannot_open_the_database_still_shuts_down(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unclean shutdown is worth logging, never worth failing the exit over."""
    settings = make_settings(tmp_path)
    with caplog.at_level(logging.ERROR, logger="darts.api.main"), TestClient(create_app(settings)):
        for sidecar in ("", "-wal", "-shm"):
            settings.db_path.with_name(settings.db_path.name + sidecar).unlink(missing_ok=True)
        settings.db_path.mkdir()

    (record,) = [r for r in caplog.records if r.getMessage() == "shutdown checkpoint failed"]
    assert "unable to open database file" in record.error  # type: ignore[attr-defined]


def read_until(process: "subprocess.Popen[str]", marker: str, timeout: float) -> list[str]:
    """Collect the child's output until `marker` shows up, or give up loudly."""
    assert process.stdout is not None
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        lines.append(line)
        if marker in line:
            return lines
    raise AssertionError(f"never saw {marker!r} in:\n{''.join(lines)}")


def test_sigterm_checkpoints_the_wal_before_the_process_exits(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    check_and_recover(settings.db_path)

    environment = os.environ | {
        f"{ENV_PREFIX}DB_PATH": str(settings.db_path),
        f"{ENV_PREFIX}BACKUP_DIR": str(settings.backup_dir),
        f"{ENV_PREFIX}STATIC_DIR": str(tmp_path / "absent-dist"),
        f"{ENV_PREFIX}LOG_LEVEL": "INFO",
    }
    # Port 0 takes whatever is free: nothing here connects over HTTP, and a
    # fixed port would collide with whatever else the runner is doing.
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "darts.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Held open across the signal for two reasons: it leaves pages in the WAL
    # for the checkpoint to actually fold back, and it stops SQLite from
    # deleting the WAL when the server's own connection closes -- which would
    # leave this test passing with no checkpoint having happened at all.
    observer = connect(settings.db_path)
    try:
        read_until(server, "boot check complete", BOOT_TIMEOUT)
        observer.execute("INSERT INTO players(display_name) VALUES ('checkpoint me')")
        populated = wal(settings.db_path).stat().st_size
        assert populated > 0, "nothing for the checkpoint to do"

        server.send_signal(signal.SIGTERM)
        assert server.stdout is not None
        remaining = server.stdout.read()
        # Uvicorn shuts down gracefully and then lets SIGTERM take its default
        # course, so -15 is a clean exit here; Docker reports it as 143.
        assert server.wait(timeout=EXIT_TIMEOUT) in (0, -signal.SIGTERM), remaining

        checkpoint = [
            line for line in remaining.splitlines() if 'msg="shutdown checkpoint"' in line
        ]
        assert checkpoint, f"no checkpoint on SIGTERM:\n{remaining}"
        assert "truncated=true" in checkpoint[0]
        # SQLite zeroes its own frame counters once a TRUNCATE succeeds, so the
        # file is the evidence: it held pages, and only the signalled process
        # could have folded them back -- this connection never checkpoints, and
        # its being open is what stopped the WAL from simply being deleted.
        assert wal(settings.db_path).stat().st_size == 0
    finally:
        observer.close()
        if server.poll() is None:  # pragma: no cover - only on a failed run
            server.kill()
            server.wait(timeout=EXIT_TIMEOUT)
