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

### Checks

Initialize or upgrade the local SQLite schema with
`uv run darts-migrate /absolute/path/to/darts.db` (parent directory must exist).
See [the data model](docs/data-model.md) for transactions, migrations, and column definitions.

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
