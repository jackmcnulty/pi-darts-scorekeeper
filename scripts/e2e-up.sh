#!/usr/bin/env bash
# Run the built image on a throwaway database, for the Playwright E2E (#32).
#
#     scripts/e2e-up.sh [image]            # default image: darts:latest
#
# #32 asks for the E2E to run "against the real built image and a real backend
# on a temporary database", so this starts exactly what deploy.sh ships -- same
# image, same entrypoint, same non-root user, the frontend served by the app
# itself -- and changes only where the data lives. There is no dev server
# anywhere in an E2E run.
#
# The data directory is new on every run and owned by nobody the container
# cares about, which is #28's trap in a new place: the image runs as uid 1000,
# a CI runner is uid 1001, and a directory the runner created is not writable
# by the container, which then fails its boot check. So the directory is made
# world-writable. It is a temporary directory on a disposable machine; on the
# Pi, bootstrap-pi.sh chowns /var/lib/darts to 1000 instead.
#
# Automatic backups (#31) are left on, as shipped. The first start has no
# matches and is skipped; nothing restarts the container during a run, so in
# practice none is written.
#
# Settings, from the environment:
#   E2E_PORT       host port to publish (default 8000)
#   E2E_DATA_DIR   data directory to bind-mount (default: a new temporary one).
#                  On a Mac with colima, it must be under $HOME, the only
#                  directory colima shares with its VM.
#   E2E_CONTAINER  container name (default darts-e2e)
#
# Prints the data directory on stdout, and waits until /api/healthz answers.
# On a timeout it prints the container's log and exits 1. Stop it with
# `docker rm -f darts-e2e`.

set -Eeuo pipefail

# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

image="${1:-darts:latest}"
port="${E2E_PORT:-8000}"
name="${E2E_CONTAINER:-darts-e2e}"
data="${E2E_DATA_DIR:-}"

require_cmd docker
require_cmd curl

if [ -z "$data" ]; then
  parent="${RUNNER_TEMP:-$HOME/.cache}"
  mkdir -p "$parent"
  data="$(mktemp -d "${parent}/darts-e2e.XXXXXX")"
fi
mkdir -p "$data"
chmod 0777 "$data"

docker rm -f "$name" >/dev/null 2>&1 || true

log "starting ${image} as ${name} on port ${port}, data in ${data}"
docker run -d --name "$name" \
  -p "${port}:8000" \
  -v "${data}:/var/lib/darts" \
  -e DARTS_SNAPSHOT_DIR=/var/lib/darts/share \
  "$image" >/dev/null

url="http://localhost:${port}/api/healthz"
for _ in $(seq 1 60); do
  if body="$(curl -fsS --max-time 2 "$url" 2>/dev/null)"; then
    log "healthy: ${body}"
    printf '%s\n' "$data"
    exit 0
  fi
  sleep 1
done

docker logs "$name" >&2 || true
die "${url} did not answer within 60 s"
