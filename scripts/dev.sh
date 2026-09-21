#!/usr/bin/env bash
# Run both dev servers together.
#
#   Uvicorn  http://127.0.0.1:8000   the API
#   Vite     http://127.0.0.1:5173   the app, proxying /api to Uvicorn
#
# Ctrl-C stops both. Either one exiting stops the other.
set -Eeuo pipefail

# Job control puts each background job in its own process group, so cleanup can
# signal the whole tree (uv -> uvicorn, node -> vite workers) rather than just
# the direct child and leave orphans behind.
set -m

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$repo_root"

backend_pid=""
frontend_pid=""

# Signal a whole process group; the leading "-" on the pid is what makes it a group.
kill_group() {
  local signal="$1" pid="$2"
  [ -n "$pid" ] || return 0
  kill "-${signal}" -- "-${pid}" 2>/dev/null || true
}

cleanup() {
  trap - EXIT INT TERM
  kill_group TERM "$backend_pid"
  kill_group TERM "$frontend_pid"
  # Give them a moment to go down cleanly, then insist.
  local waited=0
  while [ "$waited" -lt 20 ]; do
    if ! kill -0 "$backend_pid" 2>/dev/null && ! kill -0 "$frontend_pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
    waited=$((waited + 1))
  done
  kill_group KILL "$backend_pid"
  kill_group KILL "$frontend_pid"
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ ! -d frontend/node_modules ]; then
  echo "frontend/node_modules is missing; run 'npm ci' in frontend/ first." >&2
  exit 1
fi

echo "backend  -> http://127.0.0.1:8000"
uv run uvicorn darts.api.main:app --host 127.0.0.1 --port 8000 --reload &
backend_pid=$!

echo "frontend -> http://127.0.0.1:5173"
# Run Vite's binary directly rather than through 'npm run dev', so the pid we
# hold is Vite itself and not an npm wrapper that may not forward signals.
(cd frontend && exec node_modules/.bin/vite --host 127.0.0.1 --port 5173 --strictPort) &
frontend_pid=$!

# bash 3.2 (the macOS system bash) has no 'wait -n', so poll instead.
while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done
