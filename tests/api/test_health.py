"""/api/healthz and /api/version: what the box says about itself.

Health has two independent failure conditions and both are tested against the
real thing: a database corrupted on disk before boot, and one made unwritable
after boot. Neither is simulated with a mock, because what is being tested is
precisely whether the endpoint notices reality.
"""

from pathlib import Path
from typing import Any

import pytest
from apifixtures import SCHEMA_VERSION, corrupt, make_settings
from fastapi.testclient import TestClient
from httpx import Response

from darts import __version__
from darts.api.health import probe
from darts.api.main import create_app
from darts.db.backup import create as create_backup
from darts.db.recovery import check_and_recover


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """A real, migrated database, ready to be damaged."""
    path = tmp_path / "darts.db"
    check_and_recover(path)
    return path


def health_of(response: Response) -> dict[str, Any]:
    """The report, whether it came back as a 200 body or a 503 detail."""
    body: dict[str, Any] = response.json()
    return dict(body["error"]["detail"]) if "error" in body else body


def test_a_healthy_box_reports_its_schema_and_build(client: TestClient) -> None:
    response = client.get("/api/healthz")
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "healthy"
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["git_sha"] == "cafe1234"
    assert report["version"] == __version__
    assert report["auto_restored"] is False
    assert report["checked_at"].endswith("Z")


def test_version_reports_the_build(client: TestClient) -> None:
    assert client.get("/api/version").json() == {"version": __version__, "git_sha": "cafe1234"}


def test_a_degraded_boot_is_a_503(tmp_path: Path, database: Path) -> None:
    """Corrupt, with no backup to fall back on: #12's degraded state."""
    corrupt(database)
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/api/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "service_unavailable"
    report = health_of(response)
    assert report["status"] == "degraded"
    assert report["auto_restored"] is False
    # The database was still made serviceable, so the schema is readable even
    # while the box reports itself unfit.
    assert report["schema_version"] == SCHEMA_VERSION


def test_an_automatic_restore_is_reported_but_still_serves(tmp_path: Path, database: Path) -> None:
    """A restore already fixed it; a monitor restarting the box would only lose it."""
    settings = make_settings(tmp_path)
    create_backup(database, backup_dir=settings.backup_dir)
    corrupt(database)

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/healthz")

    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "restored"
    assert report["auto_restored"] is True


def test_a_database_that_goes_unwritable_after_boot_is_a_503(
    tmp_path: Path, client: TestClient
) -> None:
    """The boot check cannot know the card went read-only an hour later."""
    assert client.get("/api/healthz").status_code == 200

    database = tmp_path / "darts.db"
    database.chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        response = client.get("/api/healthz")
    finally:
        tmp_path.chmod(0o755)
        database.chmod(0o644)

    assert response.status_code == 503
    report = health_of(response)
    assert report["status"] == "unavailable"
    assert report["schema_version"] is None
    assert "readonly" in str(report["detail"])


def test_a_database_that_disappears_after_boot_is_a_503(tmp_path: Path, client: TestClient) -> None:
    """Works the same when the path stops being a database at all."""
    database = tmp_path / "darts.db"
    for sidecar in ("", "-wal", "-shm"):
        database.with_name(database.name + sidecar).unlink(missing_ok=True)
    database.mkdir()

    response = client.get("/api/healthz")
    assert response.status_code == 503
    assert health_of(response)["status"] == "unavailable"


def test_an_app_whose_lifespan_never_ran_refuses_to_claim_health(
    stateless: TestClient,
) -> None:
    """The trap this endpoint is easiest to get wrong on.

    A bare `TestClient(app)` runs no lifespan, so no boot check has happened.
    Reporting `healthy` there would make every other health test able to pass
    without the startup hook working at all.
    """
    response = stateless.get("/api/healthz")
    assert response.status_code == 503
    report = health_of(response)
    assert report["status"] == "not_started"
    assert report["checked_at"] is None


def test_the_probe_costs_the_card_no_writes(tmp_path: Path, client: TestClient) -> None:
    """#28's HEALTHCHECK runs this every few seconds for the life of the card."""
    database = tmp_path / "darts.db"
    assert client.get("/api/healthz").status_code == 200
    before = database.stat().st_mtime_ns

    for _ in range(5):
        assert client.get("/api/healthz").status_code == 200
    assert database.stat().st_mtime_ns == before


def test_the_probe_reads_the_schema_version(database: Path) -> None:
    assert probe(database) == (SCHEMA_VERSION, None)


def test_the_probe_describes_a_failure_rather_than_raising(tmp_path: Path) -> None:
    """An unreachable database is the answer this endpoint exists to give."""
    unopenable = tmp_path / "not-a-database.db"
    unopenable.mkdir()
    schema_version, problem = probe(unopenable)
    assert schema_version is None
    assert problem is not None and problem.startswith("OperationalError")
