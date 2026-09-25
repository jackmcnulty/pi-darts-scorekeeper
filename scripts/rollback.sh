#!/usr/bin/env bash
# Put the previous image back, by hand.
#
#   scripts/rollback.sh --host pi@darts.local              # to the previous sha
#   scripts/rollback.sh --host pi@darts.local --to 1a2b3c4 # to a specific one
#   scripts/rollback.sh --host pi@darts.local --list       # what is available
#
# deploy.sh already rolls back on its own when a new build fails its health
# check. This script is for the case that one does not cover: a build that comes
# up healthy and is then discovered to be wrong -- scores rendering incorrectly,
# a checkout suggestion that is nonsense, anything that a 200 from /api/healthz
# cannot see. That is a judgement a human makes minutes or hours later, by which
# time the deploy that installed it has long since exited 0.
#
# It shares deploy.sh's two primitives, retag `latest` and `up -d`, and its
# target selection comes from the same pure function in scripts/deploy-lib.sh,
# so "the previous sha" means the same thing in both places.
#
# It deliberately does *not* touch the database. Rolling the image back does not
# un-migrate a schema, and pretending otherwise by restoring a backup
# automatically would turn a reversible action into a destructive one. If the
# problem is a migration rather than the code, use darts-restore against the
# backup deploy.sh took, and do it deliberately.
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/lib.sh
. "${script_dir}/lib.sh"
# shellcheck source=scripts/deploy-lib.sh
. "${script_dir}/deploy-lib.sh"
# shellcheck source=scripts/deploy-remote.sh
. "${script_dir}/deploy-remote.sh"

HOST="${DARTS_DEPLOY_HOST:-}"
SSH_CONFIG="${DARTS_SSH_CONFIG:-}"
PORT="${DARTS_PORT:-8000}"
REMOTE_DIR="${DARTS_REMOTE_DIR:-darts}"
TARGET_SHA=""
HEALTH_RETRIES=30
LIST_ONLY=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: rollback.sh --host <host> [options]

Point the target back at its previous image and restart.

  --host <host>          SSH destination of the target, e.g. pi@darts.local.
                         Defaults to $DARTS_DEPLOY_HOST.
  --ssh-config <file>    Pass -F <file> to ssh. Defaults to $DARTS_SSH_CONFIG.
  --port <port>          Port the app is published on (default 8000).
  --remote-dir <path>    Directory on the target holding the compose file,
                         relative to the SSH user's home (default "darts").
  --to <sha>             Roll back to this sha instead of the previous one. The
                         image must already be on the target.
  --list                 List the sha-tagged images on the target, newest
                         first, and exit without changing anything.
  --health-retries <n>   Health poll attempts after restarting, one second
                         apart (default 30).
  --dry-run              Print the planned actions to stdout and exit without
                         changing anything.
  -h, --help             Show this message.

Exits 0 once the target reports the rolled-back sha, non-zero otherwise.
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
    --remote-dir)
      REMOTE_DIR="${2:-}"
      shift
      ;;
    --to)
      TARGET_SHA="${2:-}"
      shift
      ;;
    --list)
      LIST_ONLY=1
      ;;
    --health-retries)
      HEALTH_RETRIES="${2:-}"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
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
export DRY_RUN

[ -n "$HOST" ] || {
  usage >&2
  die "--host is required"
}

require_cmd ssh

remote_init

HEALTH_ARGS=(--host "$HOST" --port "$PORT")
if [ -n "$SSH_CONFIG" ]; then
  HEALTH_ARGS+=(--ssh-config "$SSH_CONFIG")
fi

# The same ordering deploy.sh uses, so that "the previous sha" means one thing
# across both scripts rather than two things that usually agree.
list_remote_tags() {
  if [ "$DRY_RUN" = "1" ]; then
    REMOTE_TAGS=""
    printf 'plan: list darts:* image tags on the target, most recently built first\n'
    return 0
  fi
  REMOTE_TAGS="$(order_tags_by_stamp "$(remote_tag_ids)" "$(remote_build_stamps)")"
}

running_sha() {
  if [ "$DRY_RUN" = "1" ]; then
    RUNNING_SHA="<running-sha>"
    printf 'plan: read the currently-serving git sha from /api/healthz\n'
    return 0
  fi
  RUNNING_SHA="$("${script_dir}/healthcheck.sh" "${HEALTH_ARGS[@]}" --retries 1 2>/dev/null)" ||
    RUNNING_SHA=""
}

main() {
  list_remote_tags

  if [ "$LIST_ONLY" = "1" ]; then
    printf '%s\n' "$REMOTE_TAGS"
    return 0
  fi

  running_sha

  if [ -n "$TARGET_SHA" ]; then
    # An explicit target still has to exist on the box. Retagging `latest` to a
    # sha that was pruned away would take the app down rather than back.
    if [ "$DRY_RUN" != "1" ] &&
      ! on_target "docker image inspect darts:${TARGET_SHA} >/dev/null" 2>/dev/null; then
      die "image darts:${TARGET_SHA} is not on ${HOST}; scripts/rollback.sh --list shows what is"
    fi
  else
    # "The previous sha" is whatever select_rollback_tag would have chosen had
    # the running build just failed its health check -- the newest image that is
    # not the one currently serving.
    # shellcheck disable=SC2086
    TARGET_SHA="$(select_rollback_tag "$RUNNING_SHA" "" $REMOTE_TAGS)"

    if [ -z "$TARGET_SHA" ] && [ "$DRY_RUN" = "1" ]; then
      # A dry run has not asked the target what it holds, so it cannot name the
      # sha. It can still show the shape of what would happen, which is the
      # point of the mode -- the same stand-in trick bootstrap-pi.sh uses when
      # dpkg is absent.
      TARGET_SHA="<previous-sha>"
    fi

    [ -n "$TARGET_SHA" ] ||
      die "no previous image on ${HOST} to roll back to"
  fi

  if [ -n "$RUNNING_SHA" ] && [ "$TARGET_SHA" = "$RUNNING_SHA" ]; then
    die "${TARGET_SHA} is already what is running"
  fi

  log "rolling ${HOST} back from ${RUNNING_SHA:-nothing} to ${TARGET_SHA}"

  run on_target docker tag "darts:${TARGET_SHA}" darts:latest
  run on_target docker compose -f "${REMOTE_DIR}/compose.yaml" up -d

  if [ "$DRY_RUN" = "1" ]; then
    printf 'plan: poll /api/healthz up to %s times, one second apart, for sha %s\n' \
      "$HEALTH_RETRIES" "$TARGET_SHA"
    return 0
  fi

  "${script_dir}/healthcheck.sh" "${HEALTH_ARGS[@]}" \
    --expect-sha "$TARGET_SHA" --retries "$HEALTH_RETRIES" >/dev/null ||
    die "rolled back to ${TARGET_SHA} but it did not come up healthy"

  log "rolled back to ${TARGET_SHA}"
}

main "$@"
