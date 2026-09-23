"""Serving the built frontend, with an SPA fallback and honest cache headers.

Two things `StaticFiles` does not do on its own, both verified against a real
Vite build rather than assumed:

* `html=True` is **not** an SPA fallback. It serves `index.html` for a
  *directory* request, so `/` works and `/history/42` is a 404. A client-side
  route surviving a refresh needs the fallback below.
* `StaticFiles` sets no `Cache-Control` at all -- only `etag` and
  `last-modified`. Both halves of the caching story are added here.

Vite content-hashes what it emits, so `index-BKd04slA.js` may be cached
forever while `index.html`, which names it, must be revalidated every time.
Getting that pair backwards is how a deploy half-lands: a phone keeps an old
`index.html` pointing at assets that no longer exist.
"""

import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Scope

from darts.config import ENV_PREFIX

logger = logging.getLogger(__name__)

#: A Vite-style content hash: `index-BKd04slA.js`, `index-Dv5vcoGT.css`.
#: The name changes whenever the bytes do, which is what makes it cacheable
#: forever. Anything unhashed -- index.html, favicon.svg, icons.svg -- is not.
HASHED = re.compile(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+\Z")

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"

#: The fallback must never reach these: an unknown `/api` path is a client
#: error that deserves the JSON envelope, not a 200 of the app's HTML.
API_PREFIX = "api/"


class SpaStaticFiles(StaticFiles):
    """`StaticFiles` plus a single-page-app fallback and cache headers."""

    def __init__(self, *, directory: Path, excluded_prefixes: tuple[str, ...] = ()) -> None:
        super().__init__(directory=directory)
        self.excluded_prefixes = excluded_prefixes

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Serve the file if it exists; otherwise let the app route it.

        Only a 404 falls back. A 405 on a POST, or anything else, is re-raised
        so a write to a static path stays a write to a static path.
        """
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.startswith(self.excluded_prefixes):
                raise
            return await super().get_response("index.html", scope)

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        """Every served file leaves here with a deliberate caching decision."""
        response = super().file_response(full_path, stat_result, scope, status_code=status_code)
        cacheable = HASHED.search(Path(os.fspath(full_path)).name) is not None
        response.headers["cache-control"] = IMMUTABLE if cacheable else NO_CACHE
        return response


def mount_static(app: FastAPI, directory: Path) -> bool:
    """Mount the build at `/` if there is one, and say whether there was.

    `frontend/dist` is gitignored and CI never builds it into the backend job,
    so an absent build is the normal state in development and on every test
    run -- not an error. Nothing is mounted and the catch-all below explains
    itself, which beats mounting a placeholder that could mask a deploy that
    shipped no frontend.

    The mount goes on last so that `/api` routes, declared before it, keep
    winning: Starlette matches in declaration order.
    """
    if not (directory / "index.html").is_file():
        logger.warning("no frontend build", extra={"static_dir": directory})
        _mount_placeholder(app, directory)
        return False

    app.mount(
        "/",
        SpaStaticFiles(directory=directory, excluded_prefixes=(API_PREFIX,)),
        name="app",
    )
    logger.info("serving frontend", extra={"static_dir": directory})
    return True


def _mount_placeholder(app: FastAPI, directory: Path) -> None:
    """Answer every unrouted path with why there is nothing to serve."""

    @app.get("/{requested:path}", include_in_schema=False)
    def missing_build(requested: str) -> Response:
        if requested.startswith(API_PREFIX):
            raise HTTPException(404)
        raise HTTPException(
            404,
            f"No frontend build at {directory}: build the frontend "
            f"or point {ENV_PREFIX}STATIC_DIR at one",
        )
