#!/usr/bin/env bash
# Reproduce the Phase 1 backend gate and produce a browsable coverage report.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$repo_root"

uv run ruff check .
uv run ruff format --check .
uv run mypy backend
uv run darts-gen-checkouts --check
uv run pytest --cov=backend/darts --cov-report=term-missing \
  --cov-report=html:htmlcov/phase1 --cov-fail-under=85 "$@"
