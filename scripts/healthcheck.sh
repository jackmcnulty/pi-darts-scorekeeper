#!/usr/bin/env bash
# Ask the target what it is running, and wait for it to be the right thing.
#
#   scripts/healthcheck.sh --host pi                        # what's running?
#   scripts/healthcheck.sh --host pi --expect-sha 1a2b3c4    # wait for that sha
#
# Prints the observed git sha on stdout and nothing else, so it composes:
#
#     running="$(scripts/healthcheck.sh --host pi --retries 1)" || running=""
#
# deploy.sh uses it three times -- to learn the sha before it changes anything,
# to confirm the new one, and to confirm the old one after a rollback -- and it
# is useful on its own when something looks wrong.
#
# Why the probe runs on the target over SSH rather than from here:
#
#   * It is the same code path on the Pi and on a stand-in VM whose published
#     port is not reachable from this Mac, so the rollback drill exercises the
#     real thing rather than a variant of it.
#   * `curl` on the target is not a new dependency -- bootstrap-pi.sh already
#     fetches Docker's signing key with it, and Raspberry Pi OS ships it. It is
#     emphatically *not* a dependency of the application: nothing in the
#     container needs it, which is what #29's "no system Python or Node" wording
#     is protecting.
#
# Why it polls the HTTP endpoint and not `docker ps` health:
#
#   * A broken image under `restart: unless-stopped` flaps between restarting
#     and running, so container state answers the wrong question.
#   * Docker's own HEALTHCHECK has `--start-period=20s`, which would eat half of
#     #29's 40-second rollback budget before reporting anything at all.
#   * The sha is the only signal that distinguishes "the new build is serving"
#     from "the old build is still serving" -- and a stale-but-healthy old
#     container answering 200 with the previous sha is exactly what a successful
#     rollback looks like.
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=scripts/lib.sh
. "${repo_root}/scripts/lib.sh"

HOST="${DARTS_DEPLOY_HOST:-}"
SSH_CONFIG="${DARTS_SSH_CONFIG:-}"
PORT="${DARTS_PORT:-8000}"
EXPECT_SHA=""
RETRIES=30

usage() {
  cat <<'EOF'
Usage: healthcheck.sh --host <host> [options]

Poll /api/healthz on the target and print the git sha it reports.

  --host <host>         SSH destination of the target, e.g. pi@darts.local.
                        Defaults to $DARTS_DEPLOY_HOST.
  --ssh-config <file>   Pass -F <file> to ssh. Defaults to $DARTS_SSH_CONFIG.
  --port <port>         Port the app is published on (default 8000).
  --expect-sha <sha>    Keep polling until the reported sha equals this.
                        Without it, any healthy response is a pass.
  --retries <n>         Attempts, one second apart (default 30).
  -h, --help            Show this message.

Exits 0 once the target answers healthily (and matches --expect-sha, if given),
1 if the attempts run out. The observed sha goes to stdout; progress goes to
stderr.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)
      HOST="${2:-}"
      shift
      ;;
    --ssh-config)
      SSH_CONFIG="${2:-}"
      shift
      ;;
    --port)
      PORT="${2:-}"
      shift
      ;;
    --expect-sha)
      EXPECT_SHA="${2:-}"
      shift
      ;;
    --retries)
      RETRIES="${2:-}"
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
  shift
done

[ -n "$HOST" ] || {
  usage >&2
  die "--host is required"
}

require_cmd ssh

# Built once, here, so every ssh invocation in this script is identical.
# BatchMode: a deploy must never block on a passphrase prompt half way through.
ssh_target() {
  if [ -n "$SSH_CONFIG" ]; then
    ssh -F "$SSH_CONFIG" -o BatchMode=yes "$HOST" "$@"
  else
    ssh -o BatchMode=yes "$HOST" "$@"
  fi
}

# `"git_sha":"abc1234"` out of the health payload, without jq.
#
# FastAPI emits compact JSON, but tolerating optional whitespace costs one
# character and means a future `indent=` on the response never turns this into a
# deploy that reports "unknown" and rolls back a perfectly good image.
extract_sha() {
  sed -n 's/.*"git_sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

probe() {
  local body sha
  # --fail so a 503 -- which /api/healthz returns while the database is
  # degraded, deliberately, rather than going silent -- is a failure here too.
  body="$(ssh_target "curl -fsS --max-time 4 http://127.0.0.1:${PORT}/api/healthz" 2>/dev/null)" || return 1
  sha="$(printf '%s' "$body" | extract_sha)" || return 1
  [ -n "$sha" ] || return 1
  if [ -n "$EXPECT_SHA" ] && [ "$sha" != "$EXPECT_SHA" ]; then
    return 1
  fi
  printf '%s\n' "$sha"
}

attempt=1
while :; do
  if result="$(probe)"; then
    printf '%s\n' "$result"
    exit 0
  fi
  if [ "$attempt" -ge "$RETRIES" ]; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ -n "$EXPECT_SHA" ]; then
  warn "target did not report sha ${EXPECT_SHA} after ${RETRIES} attempt(s)"
else
  warn "target did not answer healthily after ${RETRIES} attempt(s)"
fi
exit 1
