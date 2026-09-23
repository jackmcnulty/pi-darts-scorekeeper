"""The `darts-openapi` console script: dump the schema the app really serves.

`frontend/scripts/gen-api.mjs` runs this and feeds the result to
`openapi-typescript`, so the committed `schema.d.ts` describes the same
contract a running server does.

The schema is fetched through `GET /api/openapi.json` rather than read off
`app.openapi()`, because the route is what a client can actually reach, and
routing order is load-bearing in this app -- the static mount at `/` would
happily shadow a schema declared after it (see `api.main`). Asking the object
would not notice; asking the route would.

**It never touches a real database.** The app is built with settings pointing
into a throwaway directory that is deleted on the way out, and the lifespan is
run so startup does to that temporary file exactly what it does to the real one.
Generation is something a developer and CI run casually and often; it must not
be able to migrate, repair or checkpoint the database somebody is playing on.

A development tool: `TestClient` needs httpx, which is in the dev dependency
group, so this runs under `uv run` and not off a bare runtime install of the
package. Nothing the Pi serves calls it.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from darts.api.main import create_app
from darts.config import Settings

SCHEMA_ROUTE = "/api/openapi.json"


def _throwaway_settings(root: Path) -> Settings:
    """Settings confined to `root`, with no frontend build to serve."""
    return Settings(
        db_path=root / "openapi.db",
        backup_dir=root / "backups",
        snapshot_dir=root / "snapshots",
        static_dir=root / "absent-dist",
        port=8000,
        git_sha="openapi",
        log_level="WARNING",
    )


def schema() -> dict[str, Any]:
    """The served OpenAPI document, fetched from a disposable instance of the app."""
    with tempfile.TemporaryDirectory(prefix="darts-openapi-") as tmp:
        app = create_app(_throwaway_settings(Path(tmp)))
        # Context-managed so the lifespan runs both ends: startup checks the
        # temporary database and shutdown checkpoints it, leaving the directory
        # in a state `TemporaryDirectory` can remove without a held handle.
        with TestClient(app) as client:
            response = client.get(SCHEMA_ROUTE)
            response.raise_for_status()
            document: dict[str, Any] = response.json()
            return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darts-openapi", description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="file to write the schema to",
    )
    args = parser.parse_args(argv)

    # A file rather than stdout, and not negotiable: `configure_logging` sends
    # the application log to stdout, so a piped schema would arrive with boot
    # lines in front of it. Offering a `-` mode that silently produced invalid
    # JSON would be worse than not offering one.
    #
    # Sorted keys and a trailing newline so two dumps of the same app are the
    # same bytes, which is what makes the drift check a comparison rather than
    # a diff to interpret.
    document = json.dumps(schema(), indent=2, sort_keys=True) + "\n"
    args.output.write_text(document, encoding="utf-8")
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
