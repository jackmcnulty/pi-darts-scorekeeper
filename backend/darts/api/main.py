"""FastAPI application.

Scaffolding only. This exists so `scripts/dev.sh` has something to serve on :8000
and the Vite `/api` proxy can be exercised end to end. The real app factory,
lifespan hooks, logging, error envelope, health and static mounts arrive in #16.
"""

from typing import Any

from fastapi import FastAPI

from darts import __version__

app = FastAPI(title="darts", version=__version__, openapi_url="/api/openapi.json")


@app.get("/api/ping")
def ping() -> dict[str, Any]:
    """Liveness placeholder, replaced by /api/healthz in #16."""
    return {"pong": True, "version": __version__}
