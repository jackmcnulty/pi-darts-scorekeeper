"""Helpers the API tests share, as plain functions rather than fixtures.

A fake Vite build lives here because `frontend/dist` is gitignored and CI's
backend job never builds it: a static test that read the real directory would
pass on a developer's machine and skip silently on CI, which is the opposite of
what a test is for.
"""

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from darts.api.errors import install_error_handlers
from darts.config import Settings
from darts.repo.errors import DuplicateNameError, InvalidMatchError, NotFoundError, RepoError
from darts.services.errors import LegCompleteError, ServiceError

#: Names matching what Vite actually emitted into `frontend/dist` on 2026-09-23.
HASHED_JS = "index-BKd04slA.js"
HASHED_CSS = "index-Dv5vcoGT.css"


def build_dist(root: Path) -> Path:
    """A minimal copy of a real Vite build: hashed assets plus unhashed roots."""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>darts</title><div id=root></div>")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (dist / "assets" / HASHED_JS).write_text("console.log('darts')")
    (dist / "assets" / HASHED_CSS).write_text("body{margin:0}")
    return dist


def make_settings(
    tmp_path: Path, *, static_dir: Path | None = None, sha: str = "cafe1234"
) -> Settings:
    """Settings that touch nothing outside `tmp_path`."""
    return Settings(
        db_path=tmp_path / "darts.db",
        backup_dir=tmp_path / "backups",
        snapshot_dir=tmp_path / "snapshots",
        static_dir=static_dir if static_dir is not None else tmp_path / "absent-dist",
        port=8000,
        git_sha=sha,
        log_level="INFO",
    )


def page_size(path: Path) -> int:
    return int.from_bytes(path.read_bytes()[16:18], "big") or 65536


def corrupt(path: Path) -> None:
    """Overwrite pages in the middle, leaving the header intact.

    The same damage `tests/db/test_integrity_recovery.py` inflicts, repeated
    here rather than imported because pytest puts only a test file's own
    directory on the path.
    """
    data = bytearray(path.read_bytes())
    page = page_size(path)
    start, end = page * 2, min(page * 6, len(data))
    assert end > start, "the database is too small to corrupt meaningfully"
    data[start:end] = b"\xde" * (end - start)
    path.write_bytes(bytes(data))


def wal(database: Path) -> Path:
    return database.with_name(database.name + "-wal")


class Payload(BaseModel):
    """A body with one required field, to make pydantic refuse something."""

    name: str
    score: int


def error_app() -> FastAPI:
    """A bare app whose only job is to raise one of everything.

    Built directly rather than through `create_app` so the routes are declared
    before any static mount and every handler is exercised in isolation.
    """
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/not-found")
    def not_found() -> None:
        raise NotFoundError("no player 7")

    @app.get("/api/duplicate")
    def duplicate() -> None:
        raise DuplicateNameError("Jack is already taken")

    @app.get("/api/invalid-match")
    def invalid_match() -> None:
        raise InvalidMatchError("a match needs two teams")

    @app.get("/api/repo-error")
    def repo_error() -> None:
        raise RepoError("something addressable went wrong")

    @app.get("/api/leg-complete")
    def leg_complete() -> None:
        raise LegCompleteError("that leg is already won")

    @app.get("/api/service-error")
    def service_error() -> None:
        raise ServiceError("refused")

    @app.get("/api/database")
    def database() -> None:
        raise sqlite3.OperationalError("attempt to write a readonly database")

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("a bug nobody planned for")

    @app.get("/api/teapot")
    def teapot() -> None:
        raise HTTPException(418, "short and stout")

    @app.get("/api/structured")
    def structured() -> None:
        raise HTTPException(409, detail={"reason": "structured"})

    @app.post("/api/validated")
    def validated(payload: Payload) -> Payload:
        return payload

    return app
