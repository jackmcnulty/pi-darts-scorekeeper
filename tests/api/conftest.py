"""Fixtures for the API tests.

`client` is a context-managed TestClient on purpose. A bare `TestClient(app)`
never runs the lifespan, so the boot integrity check never happens and
`/api/healthz` would be answering about a database nothing had looked at --
a test that passes without testing anything. `stateless` exists for the one
test that asserts what that unstarted app reports.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from apifixtures import build_dist, make_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

from darts.api.main import create_app
from darts.config import Settings


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A fake Vite build, never the repository's own gitignored one."""
    return build_dist(tmp_path)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """No frontend build, which is the normal state in development and on CI."""
    return make_settings(tmp_path)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A client whose lifespan has run: startup checked the database."""
    with TestClient(app) as started:
        yield started


@pytest.fixture
def stateless(app: FastAPI) -> TestClient:
    """A client whose lifespan has *not* run."""
    return TestClient(app)


@pytest.fixture
def served(tmp_path: Path, dist: Path) -> Iterator[TestClient]:
    """An app with the fake build mounted."""
    with TestClient(create_app(make_settings(tmp_path, static_dir=dist))) as started:
        yield started
