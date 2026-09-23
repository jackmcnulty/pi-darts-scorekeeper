# pi-darts-scorekeeper

[![CI](https://github.com/jackmcnulty/pi-darts-scorekeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/jackmcnulty/pi-darts-scorekeeper/actions/workflows/ci.yml)

A phone-first score-entry webapp for a home dartboard, served from a Raspberry Pi on the LAN.

It plays 301/501/701 and American Cricket (standard, cut-throat, quick), solo or in teams, with
automatic checkout hints. Every individual dart is recorded to SQLite, so player statistics are
derived by query rather than stored as counters — new metrics can be added later and applied
retroactively without a migration.

## Hardware target

- **Raspberry Pi 5**, aarch64, **Raspberry Pi OS Bookworm (Debian 12)**.
- Home LAN only, no authentication, single-writer pass-and-play.
- Powered off and on constantly, so the database is configured for durability over speed.

The frontend targets Safari on an iPhone (402×874 CSS px), dark-first.

## Stack

| Layer    | Choice                                                            |
| -------- | ----------------------------------------------------------------- |
| Backend  | Python 3.11, [uv](https://docs.astral.sh/uv/), FastAPI, raw SQL    |
| Frontend | React + TypeScript + Vite, plain CSS, TanStack Query              |
| Database | SQLite (WAL), forward-only checksummed `.sql` migrations          |
| Tests    | pytest + hypothesis, Vitest + RTL + MSW, Playwright/webkit        |
| Deploy   | Docker image built on macOS arm64, shipped over SSH, Compose on Pi |

## Dev quickstart

`uv` is the only supported dependency manager. Do not add a `requirements.txt` or use bare `pip`.
Node is pinned by `.nvmrc` (Node 22).

```sh
# Backend
brew install uv
uv sync

# Frontend
nvm use          # reads .nvmrc
cd frontend && npm ci && cd ..

# Both dev servers together: Uvicorn on :8000, Vite on :5173 proxying /api to it.
# Ctrl-C stops both.
./scripts/dev.sh
```

The server creates its database at `var/darts.db` on first boot and checks it on
every one. Everything it touches is one environment variable, so relocating the
database is a config change and a file copy:

| Variable | Default | What it is |
| --- | --- | --- |
| `DARTS_DB_PATH` | `var/darts.db` | The live database. |
| `DARTS_BACKUP_DIR` | `backups/` beside the database | Where `darts-backup` writes. |
| `DARTS_SNAPSHOT_DIR` | `snapshots/` beside the database | The read-only share (#20, #30). |
| `DARTS_STATIC_DIR` | `frontend/dist` | The built frontend, if there is one. |
| `DARTS_PORT` | `8000` | The port to serve on. |
| `DARTS_GIT_SHA` | `unknown` | Stamped in by the image build. |
| `DARTS_LOG_LEVEL` | `INFO` | Root log level. |

`GET /api/healthz` reports the schema version, the build, and whether the last
boot had to restore from a backup; it returns 503 when the database is degraded
or unwritable. `GET /api/version` is the build alone, and the OpenAPI schema is
at `/api/openapi.json`. Running the backend without a frontend build is normal —
`/` then explains itself instead of serving the app.

### Checks

Initialize or upgrade the local SQLite schema with
`uv run darts-migrate /absolute/path/to/darts.db` (parent directory must exist).
See [the data model](docs/data-model.md) for transactions, migrations, and column definitions.

Back up and restore that database with `uv run darts-backup /path/to/darts.db` and
`uv run darts-restore /path/to/darts.db`. Backups default to a `backups/` directory
beside the database. See [durability](docs/durability.md) for the boot integrity
check, retention, and recovery behaviour.

Check a database against itself with `uv run darts-verify /path/to/darts.db`. It
replays every leg from its raw darts and reports anything derived that no longer
agrees — visit scores, dart flags, cricket effects and point events, the two
replay caches, and the leg and match winners. It exits 0 when everything agrees.

```sh
# Backend
uv run ruff check .
uv run ruff format --check .
uv run mypy backend
uv run pytest

# Frontend
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

Optionally install the pre-commit hooks (ruff check + ruff format):

```sh
uv run --with pre-commit pre-commit install
```

## Repository layout

```
backend/darts/     Python package (engine, database, API)
frontend/          Vite + React + TypeScript app
tests/             pytest suite
scripts/           dev and ops shell scripts
docs/              architecture and data-model notes
```

## Docs

- [Phase 1 validation and ticket coverage](docs/phase1-validation.md) — run
  `bash scripts/validate-phase1.sh` for the repeatable engine acceptance gate.
- [docs/architecture.md](docs/architecture.md)
- [docs/data-model.md](docs/data-model.md)
- [docs/durability.md](docs/durability.md) — shutdown checkpoint, boot integrity
  check, backup and restore.
