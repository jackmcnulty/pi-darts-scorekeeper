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

```sh
# Install uv (macOS)
brew install uv

# Create the venv and install dependencies from the committed lockfile
uv sync

# Lint, format check, type check, test
uv run ruff check .
uv run ruff format --check .
uv run mypy backend
uv run pytest
```

Optionally install the pre-commit hooks (ruff check + ruff format):

```sh
uv run --with pre-commit pre-commit install
```

## Repository layout

```
backend/darts/     Python package (engine, database, API)
tests/             pytest suite
docs/              architecture and data-model notes
```

## Docs

- [docs/architecture.md](docs/architecture.md)
- [docs/data-model.md](docs/data-model.md)
