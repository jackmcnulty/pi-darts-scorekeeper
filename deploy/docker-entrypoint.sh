#!/usr/bin/env bash
# The container's PID 1.
#
# This exists for one reason: `exec`. Uvicorn has to *be* PID 1, not a child of
# it, because the whole durability story depends on SIGTERM reaching it. Docker
# sends SIGTERM to PID 1 on `compose down` and when the Pi is switched off;
# uvicorn handles it, finishes the graceful shutdown, and the FastAPI lifespan
# hook checkpoints the WAL (see docs/durability.md). A shell sitting in between
# would absorb the signal and the checkpoint would never run -- and it would
# fail silently, leaving a populated WAL rather than an error anyone would see.
#
# It is a script rather than a bare exec-form CMD because the port is
# configurable and exec form does not expand environment variables.
set -Eeuo pipefail

# ${DARTS_PORT:-8000} rather than a bare expansion, so that an empty value
# means the default. config.py makes the same promise -- unset and empty are
# the same thing -- so that a Compose env file full of blank `DARTS_*=` lines
# behaves like one that omits them.
exec uvicorn darts.api.main:app \
  --host 0.0.0.0 \
  --port "${DARTS_PORT:-8000}"
