"""Per-request connections work across FastAPI's sequential worker handoffs.

Concurrent setup requests in test_players_api cover cross-worker execution.
"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from apifixtures import make_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

from darts.api.deps import ConnectionDep
from darts.api.errors import install_error_handlers
from darts.api.main import create_app
from darts.db.recovery import check_and_recover


@pytest.fixture
def wired(tmp_path: Path) -> Iterator[TestClient]:
    """An app with one endpoint that does what #17's endpoints will do."""
    settings = make_settings(tmp_path)
    app = FastAPI()
    app.state.settings = settings
    app.state.recovery = None

    @app.get("/api/rows")
    def rows(conn: ConnectionDep) -> dict[str, object]:
        return {
            "players": conn.execute("SELECT count(*) FROM players").fetchone()[0],
            "thread": threading.current_thread().name,
        }

    check_and_recover(settings.db_path)
    with TestClient(app) as client:
        yield client


def test_a_request_gets_a_working_connection(wired: TestClient) -> None:
    response = wired.get("/api/rows")
    assert response.status_code == 200
    assert response.json()["players"] == 0


def test_sync_endpoint_runs_in_a_worker(wired: TestClient) -> None:
    """Synchronous handlers run off the main thread."""
    assert wired.get("/api/rows").json()["thread"] != "MainThread"


def test_consecutive_requests_do_not_share_a_connection(wired: TestClient) -> None:
    """Each request opens and closes its own, so nothing outlives the request."""
    for _ in range(5):
        assert wired.get("/api/rows").status_code == 200


def test_settings_come_from_the_app_not_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DARTS_GIT_SHA", "from-the-environment")
    with TestClient(create_app(make_settings(tmp_path, sha="from-the-app"))) as client:
        assert client.get("/api/version").json()["git_sha"] == "from-the-app"


def test_an_unopenable_database_surfaces_as_a_503(tmp_path: Path) -> None:
    """The dependency raising must not become an opaque 500 in #17."""
    settings = make_settings(tmp_path)
    settings.db_path.mkdir(parents=True)
    app = FastAPI()
    install_error_handlers(app)
    app.state.settings = settings

    @app.get("/api/unreachable")
    def unreachable(conn: ConnectionDep) -> None:  # pragma: no cover - never entered
        raise AssertionError("the dependency should have failed first")

    with TestClient(app) as client:
        response = client.get("/api/unreachable")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
