#!/usr/bin/env bash
# Put a new build on the Pi, and take it off again if it does not work.
#
#   scripts/deploy.sh --host pi@darts.local
#   scripts/deploy.sh --host pi@darts.local --dry-run     # print the plan
#
# Runs from a development machine. CI never touches the Pi -- GitHub's runners
# cannot reach a box on a home LAN, and #29 says so explicitly.
#
# The design is driven by one observation: the rollback path is the only part of
# a deploy script that runs exclusively when something is already wrong, so it is
# the part least likely to have been exercised and most expensive to get wrong.
# Everything below is arranged so that rollback is not a special case bolted on
# after the happy path, but the same two operations the happy path uses --
# retag `latest`, `up -d` -- pointed at a different sha.
#
# That is why images are sha-tagged and `darts:latest` is a moving alias:
# deploy/compose.yaml hardcodes `image: darts:latest` and is shipped to the
# target as a constant, with no per-host state to drift out of sync. Compose
# interpolates `${VAR}` from the shell environment or a `.env` file beside the
# compose file and *not* from `env_file:` -- a distinction #28 proved the hard
# way -- so the alternative would have meant inventing a second piece of host
# state that has to stay correct for a rollback to work at all.
#
# Ordering, and the one place it is not the order the ticket lists:
#
#   The container is stopped *before* the backup, not after it. Stopping sends
#   SIGTERM, which is what the FastAPI lifespan hook needs to run
#   `PRAGMA wal_checkpoint(TRUNCATE)` (docs/durability.md), so the backup is
#   taken against a database with no writer attached and no outstanding WAL
#   frames. Backing up and then migrating underneath a live server would be a
#   few seconds quicker and would be doing DDL on a database another process has
#   open. A home scorekeeper can afford the downtime; it cannot afford the
#   database.
#
# What rollback does and does not cover -- read this before trusting it:
#
#   It restores the *image*. It does not undo the *migration*, because the
#   migration already ran against the live database by the time health is
#   checked. Migrations here are additive, so an older image generally still
#   serves a newer schema, but a genuinely bad migration is not something an
#   automatic image rollback can repair. The backup from step 4 is the escape
#   hatch for that case, and `darts-restore` is how you use it.
set -Eeuo pipefail

# Overridable so tests/deploy/test_deploy_dry_run.py can point the script at a
# throwaway git repository and control whether the worktree is dirty -- the same
# reason bootstrap-pi.sh takes BOOTSTRAP_STATE_DIR. Nothing in normal operation
# sets it.
repo_root="${DEPLOY_REPO_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/lib.sh
. "${script_dir}/lib.sh"
# shellcheck source=scripts/deploy-lib.sh
. "${script_dir}/deploy-lib.sh"

HOST="${DARTS_DEPLOY_HOST:-}"
SSH_CONFIG="${DARTS_SSH_CONFIG:-}"
PORT="${DARTS_PORT:-8000}"

#: Where the compose file is shipped to, relative to the SSH user's home.
#:
#: Not /etc/darts, which bootstrap-pi.sh creates root-owned: the env file there
#: is host configuration an operator edits by hand, whereas the compose file is
#: an artefact this script overwrites on every deploy. Keeping it under the
#: deploy user's home means no step of a deploy needs sudo, which is worth more
#: than tidiness -- see the note about uid 1000 and WAL sidecars below.
REMOTE_DIR="${DARTS_REMOTE_DIR:-darts}"

#: The bind-mounted state directory from deploy/compose.yaml.
STATE_DIR="/var/lib/darts"

#: #29: poll /api/healthz 30 x 1s.
HEALTH_RETRIES=30

#: #29: image tags never exceed 5 on the Pi.
KEEP=5

ALLOW_DIRTY=0
SKIP_TESTS=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: deploy.sh --host <host> [options]

Build the image here, ship it to the target, migrate, restart, and roll back
automatically if the new build does not come up healthy.

  --host <host>          SSH destination of the target, e.g. pi@darts.local.
                         Defaults to $DARTS_DEPLOY_HOST.
  --ssh-config <file>    Pass -F <file> to ssh and scp. Defaults to
                         $DARTS_SSH_CONFIG.
  --port <port>          Port the app is published on (default 8000). Must match
                         the published port in deploy/compose.yaml.
  --remote-dir <path>    Directory on the target for the compose file, relative
                         to the SSH user's home (default "darts").
  --keep <n>             Sha-tagged images to keep on the target (default 5).
                         Older ones are deleted; the running image and the
                         rollback target are never deleted.
  --health-retries <n>   Health poll attempts after restarting, one second
                         apart (default 30). A failed poll triggers rollback.
  --allow-dirty          Deploy even though the worktree has uncommitted
                         changes. Without it, a dirty worktree is refused:
                         the image is stamped with HEAD's sha and would
                         otherwise claim to be a commit that it is not.
  --skip-tests           Skip the backend test suite in preflight.
  --dry-run              Print the planned actions to stdout and exit without
                         changing anything, locally or on the target. Needs no
                         reachable target.
  -h, --help             Show this message.

Exit status is 0 for a healthy deploy, and non-zero if the deploy failed --
including when the rollback that followed it succeeded.
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
    --keep)
      KEEP="${2:-}"
      shift
      ;;
    --health-retries)
      HEALTH_RETRIES="${2:-}"
      shift
      ;;
    --allow-dirty)
      ALLOW_DIRTY=1
      ;;
    --skip-tests)
      SKIP_TESTS=1
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

cd -- "$repo_root"

# --- Talking to the target --------------------------------------------------

# BatchMode: a deploy must fail on a missing key rather than block forever on a
# passphrase prompt at step 6 of 9.
SSH_OPTS=(-o BatchMode=yes)
HEALTH_ARGS=(--host "$HOST" --port "$PORT")
if [ -n "$SSH_CONFIG" ]; then
  SSH_OPTS=(-F "$SSH_CONFIG" -o BatchMode=yes)
  HEALTH_ARGS+=(--ssh-config "$SSH_CONFIG")
fi

# Named wrappers rather than inline ssh, so that `run on_target docker ...`
# produces a --dry-run plan line that reads like the thing it will do.
on_target() {
  # ssh joins its arguments into one string and the remote shell re-parses it.
  # That is relied on deliberately -- several commands here carry pipes and
  # redirections -- and every value interpolated into them is a git sha or a
  # path this script produced, not anything that came from the target.
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "$HOST" "$@"
}

copy_to_target() {
  local src="$1" dest="$2"
  scp "${SSH_OPTS[@]}" -- "$src" "${HOST}:${dest}"
}

# A read-only query. Deliberately *not* routed through `run`: queries have to
# actually execute for the script to make decisions, and in --dry-run they are
# skipped entirely rather than run against a target that may not exist. The
# placeholders that stand in for their answers are the same trick
# bootstrap-pi.sh uses when `dpkg` is absent.
query_target() {
  on_target "$@" 2>/dev/null
}

# The slowest step of a deploy by a wide margin, and deliberately *not* piped
# through gzip.
#
# The intuition says compress it: the image is 267 MB and the wire is a home LAN
# feeding an SD card. The measurement says otherwise. `docker save` does not emit
# an uncompressed tar of the filesystem -- it emits the layer blobs, which are
# already compressed -- so the stream is 57.7 MB before gzip and 57.2 MB after
# it: 0.93%, in exchange for compressing 57 MB on this Mac and decompressing it
# on a Pi. The 267 MB figure is the unpacked on-disk size and never crosses the
# network at all.
#
# Written as a function because `run` takes a command, not a pipeline.
push_image() {
  local tag="$1"
  docker save "$tag" | on_target "docker load"
}

# --- Preflight --------------------------------------------------------------

preflight() {
  require_cmd git
  require_cmd docker
  require_cmd ssh
  require_cmd scp

  [ -f "${repo_root}/deploy/Dockerfile" ] ||
    die "missing ${repo_root}/deploy/Dockerfile"
  [ -f "${repo_root}/deploy/compose.yaml" ] ||
    die "missing ${repo_root}/deploy/compose.yaml"

  check_worktree
  check_ssh
  check_bootstrapped
  run_tests
}

# Refuse to deploy to a host bootstrap-pi.sh has never run on.
#
# Not a courtesy check. Docker *creates* a missing bind-mount source directory
# by itself, owned by root:root, so `up -d` against an unprepared host does not
# fail -- it succeeds, and produces a container running as uid 1000 that cannot
# write the database. That surfaces as a degraded /api/healthz and, on the way
# there, as `attempt to write a readonly database`: a message that names the
# database rather than the ownership of the directory holding it. Catching it
# here costs one ssh round trip and turns a confusing afternoon into one line.
check_bootstrapped() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'plan: verify %s exists on the target, owned by uid 1000\n' "$STATE_DIR"
    printf 'plan: verify /etc/darts/darts.env exists on the target\n'
    return 0
  fi

  local owner=""
  owner="$(query_target "stat -c %u ${STATE_DIR}")" || owner=""
  [ -n "$owner" ] ||
    die "${STATE_DIR} does not exist on ${HOST}; run scripts/bootstrap-pi.sh there first"
  [ "$owner" = "1000" ] ||
    die "${STATE_DIR} on ${HOST} is owned by uid ${owner}, not 1000; the container cannot write it"

  query_target "test -f /etc/darts/darts.env" ||
    die "/etc/darts/darts.env is missing on ${HOST}; run scripts/bootstrap-pi.sh there first"

  log "${STATE_DIR} is present and owned by uid 1000"
}

# The image is stamped with HEAD's short sha and reports it from /api/healthz
# and /api/version. Deploying uncommitted work would put a build on the Pi that
# claims to be a commit it is not -- and since rollback selection is driven by
# the sha the box reports, two different images answering with the same sha is
# precisely the state in which rollback picks the wrong one.
check_worktree() {
  local dirty=""
  dirty="$(git -C "$repo_root" status --porcelain 2>/dev/null)" || dirty=""

  if [ -z "$dirty" ]; then
    log "worktree is clean"
    return 0
  fi
  if [ "$ALLOW_DIRTY" = "1" ]; then
    warn "worktree is dirty; deploying anyway because --allow-dirty was passed"
    warn "the image will be stamped ${SHA}, which does not describe what is in it"
    return 0
  fi
  printf '%s\n' "$dirty" >&2
  die "worktree has uncommitted changes; commit them or pass --allow-dirty"
}

check_ssh() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'plan: verify ssh reachability of %s and docker on the target\n' "$HOST"
    return 0
  fi
  on_target true ||
    die "cannot reach ${HOST} over ssh"
  on_target "command -v docker >/dev/null" ||
    die "docker is not installed on ${HOST}; run scripts/bootstrap-pi.sh there first"
  on_target "docker compose version >/dev/null" ||
    die "the docker compose plugin is missing on ${HOST}; run scripts/bootstrap-pi.sh there first"
  log "ssh and docker are available on ${HOST}"
}

# The backend suite only. The frontend's typecheck and production build run
# inside the image build itself -- `npm run build` is `tsc -b && vite build` --
# so a broken frontend fails this deploy at the build step rather than here, and
# running its tests twice would add minutes to every deploy for no new signal.
run_tests() {
  if [ "$SKIP_TESTS" = "1" ]; then
    warn "skipping the test suite because --skip-tests was passed"
    return 0
  fi
  require_cmd uv
  run uv run pytest -q tests
}

# --- The deploy -------------------------------------------------------------

# The label is not decoration; see list_remote_tags for why it has to exist.
BUILT_AT_LABEL="org.darts.built-at"

build_image() {
  run docker build \
    -f "${repo_root}/deploy/Dockerfile" \
    --build-arg "GIT_SHA=${SHA}" \
    --label "${BUILT_AT_LABEL}=$(date -u +%s)" \
    -t "darts:${SHA}" \
    "$repo_root"
}

# What is serving right now, read before anything is touched.
#
# This is the rollback target in the normal case, and taking it from the running
# application rather than from Docker's image metadata is the point: it is the
# build that was demonstrably answering requests a moment ago, which is a
# stronger thing to know than which image was created most recently.
read_running_sha() {
  if [ "$DRY_RUN" = "1" ]; then
    RUNNING_SHA="<running-sha>"
    printf 'plan: read the currently-serving git sha from /api/healthz\n'
    return 0
  fi
  RUNNING_SHA="$("${script_dir}/healthcheck.sh" "${HEALTH_ARGS[@]}" --retries 1 2>/dev/null)" ||
    RUNNING_SHA=""

  if [ -n "$RUNNING_SHA" ]; then
    log "currently serving: ${RUNNING_SHA}"
  else
    warn "nothing is serving on ${HOST}:${PORT} right now (first deploy, or the box is down)"
  fi
}

# Sha tags present on the target, most recently built first.
#
# The obvious implementation -- `docker image ls`, which sorts by creation
# descending -- does not work, and the way it fails is silent. Measured on
# Engine 29.5.2: seven distinct darts images, built from four different commits
# over twenty minutes, all reported `Created` as
# 2026-09-25T12:34:10.61878831-04:00. BuildKit takes the config timestamp from
# the cached parent rather than from when the image was assembled, so once the
# layer cache is warm every build claims the same instant. Sorting by it gives
# an arbitrary order that *looks* chronological.
#
# So build order is recorded explicitly, as a label this script sets at build
# time. A label lives in the image config, which means it survives
# `docker save` / `docker load` and describes the artefact rather than the host
# it happens to be sitting on -- unlike a deploy log on the target, which would
# be one more piece of state that has to stay correct for a rollback to work.
#
# The label is read back by image id and joined on it, rather than relying on
# `docker image inspect` emitting one line per argument in argument order. It
# does not do that, in two ways that were both measured here: given an image
# whose config carries no `Labels` key at all, `{{index .Config.Labels "x"}}`
# raises a template error, writes it to stderr, and emits *no line* for that
# image; and the surviving lines come back in an order of their own, not the
# order asked for. Pairing by position therefore silently attributes one
# image's build time to another. Hence `{{with index .Config "Labels"}}`, which
# tolerates the missing key, and an explicit join on `{{.Id}}`.
#
# Images with no label sort last, which is right: they predate this and are
# therefore older than anything that has one.
#
# `latest` is filtered out because it is an alias for one of these, not a
# further image.
list_remote_tags() {
  if [ "$DRY_RUN" = "1" ]; then
    REMOTE_TAGS=""
    printf 'plan: list darts:* image tags on the target, most recently built first\n'
    return 0
  fi

  # "<tag> <full image id>", one per line, `latest` dropped.
  local pairs=""
  pairs="$(
    query_target "docker image ls 'darts' --no-trunc --format '{{.Tag}} {{.ID}}'" |
      grep -v -e '^latest ' -e '^<none> ' || true
  )"

  if [ -z "$pairs" ]; then
    REMOTE_TAGS=""
    return 0
  fi

  local refs="" tag="" id=""
  while IFS=' ' read -r tag id; do
    [ -n "$id" ] || continue
    refs="${refs} ${id}"
  done <<<"$pairs"

  # "<full image id> <build stamp or nothing>", one per line.
  local stamps=""
  stamps="$(
    query_target "docker image inspect --format '{{.Id}} {{with index .Config \"Labels\"}}{{index . \"${BUILT_AT_LABEL}\"}}{{end}}'${refs}"
  )" || stamps=""

  # Join on the id, then sort by stamp descending. A missing or non-numeric
  # stamp becomes 0 so it sorts last rather than aborting the sort.
  local combined="" stamp=""
  while IFS=' ' read -r tag id; do
    [ -n "$tag" ] || continue
    stamp="$(printf '%s\n' "$stamps" | awk -v want="$id" '$1 == want { print $2; exit }')"
    case "$stamp" in
      '' | *[!0-9]*) stamp=0 ;;
    esac
    combined="${combined}${stamp} ${tag}"$'\n'
  done <<<"$pairs"

  REMOTE_TAGS="$(printf '%s' "$combined" | sort -k1,1rn | awk '{ print $2 }')"
}

transfer_image() {
  if [ "$DRY_RUN" = "1" ]; then
    # Spelled out rather than left as `plan: push_image ...`, because this is the
    # step that moves ~58 MB and the one an operator most wants to see before
    # letting the script near a Pi.
    printf 'plan: docker save darts:%s | ssh %s "docker load"\n' "$SHA" "$HOST"
    printf 'plan: skip that transfer entirely if darts:%s is already on the target\n' "$SHA"
    return 0
  fi
  # Image *ids*, not merely "is the tag there".
  #
  # Rebuilding an unchanged commit does not produce the same image: the build
  # embeds timestamps and an attestation manifest, so darts:<sha> locally can be
  # a different image from darts:<sha> on the target while both honestly claim
  # that commit. Skipping on tag presence alone would then leave the target
  # running the older build of the right commit and report success -- harmless
  # in effect, since the source matches, but a divergence that would be
  # thoroughly confusing to debug and a lie in the skip message.
  #
  # Comparing ids makes the skip truthful: it fires only when the target already
  # has this exact image, which is the case worth saving ~58 MB for.
  local local_id="" remote_id=""
  local_id="$(docker image inspect --format '{{.Id}}' "darts:${SHA}" 2>/dev/null)" || local_id=""
  remote_id="$(query_target "docker image inspect --format '{{.Id}}' darts:${SHA}")" || remote_id=""

  if [ -n "$local_id" ] && [ "$local_id" = "$remote_id" ]; then
    skip "darts:${SHA} on the target is already this exact image"
    return 0
  fi
  run push_image "darts:${SHA}"
}

install_compose_file() {
  run on_target mkdir -p "$REMOTE_DIR"
  run copy_to_target "${repo_root}/deploy/compose.yaml" "${REMOTE_DIR}/compose.yaml"
}

# SIGTERM, a drained uvicorn, and the WAL checkpoint the lifespan hook runs on
# the way out. `stop` rather than `down` so the container definition survives to
# be compared against the new image.
stop_container() {
  run on_target docker compose -f "${REMOTE_DIR}/compose.yaml" stop
}

# Every database command runs *inside a container*, as the image's uid 1000,
# with --entrypoint overriding the uvicorn launcher. Two reasons, and they point
# the same way:
#
#   * #29 requires the Pi to have no system Python dependency on the app. There
#     is no darts-backup on the host to call.
#   * Opening a WAL-mode database creates darts.db-wal and darts.db-shm owned by
#     whoever opened it, even read-only. Run any of this under sudo and the
#     container cannot write the sidecars on its next start, and SQLite reports
#     `attempt to write a readonly database` -- naming the database rather than
#     the two files actually at fault. docs/deploy.md leads with this.
db_command() {
  local entrypoint="$1"
  shift
  run on_target docker run --rm \
    -v "${STATE_DIR}:${STATE_DIR}" \
    --entrypoint "$entrypoint" \
    "darts:${SHA}" "$@"
}

backup_database() {
  if [ "$DRY_RUN" != "1" ] && ! query_target "test -f ${STATE_DIR}/darts.db"; then
    skip "no database on the target yet; nothing to back up"
    return 0
  fi
  db_command darts-backup "${STATE_DIR}/darts.db"
}

# From the *new* image, never `docker compose exec`. Migrations ship inside the
# image, so exec would run the old container's migration set against a database
# the new code is about to open.
migrate_database() {
  db_command darts-migrate "${STATE_DIR}/darts.db"
}

# Point `latest` at a sha and bring the container up to match.
#
# Plain `up -d`, not `--force-recreate`. Compose compares the running
# container's image id against what the service's image resolves to now, so
# moving the `latest` tag is a change it acts on -- measured: `Container darts
# Recreated`. That is what makes rollback work with no extra flag.
#
# Note what this does *not* mean. A same-sha redeploy also recreates, because
# rebuilding an unchanged commit yields a different image id (see
# transfer_image). "Idempotent" here is a claim about the end state -- same
# commit serving, same data -- and not a claim that the container is left
# untouched.
activate() {
  local tag="$1"
  run on_target docker tag "darts:${tag}" darts:latest
  run on_target docker compose -f "${REMOTE_DIR}/compose.yaml" up -d
}

await_sha() {
  local tag="$1" retries="$2"
  if [ "$DRY_RUN" = "1" ]; then
    printf 'plan: poll /api/healthz up to %s times, one second apart, for sha %s\n' \
      "$retries" "$tag"
    return 0
  fi
  "${script_dir}/healthcheck.sh" "${HEALTH_ARGS[@]}" \
    --expect-sha "$tag" --retries "$retries" >/dev/null
}

prune_images() {
  local protected="${SHA} ${ROLLBACK_TAG}"
  local doomed=""

  if [ "$DRY_RUN" = "1" ]; then
    printf 'plan: keep the newest %s sha tags plus the rollback target, delete the rest\n' "$KEEP"
    return 0
  fi

  # REMOTE_TAGS is a newline-separated list and splitting it into positional
  # arguments is exactly what is wanted.
  # shellcheck disable=SC2086
  doomed="$(tags_to_prune "$KEEP" "$protected" $REMOTE_TAGS)"

  if [ -z "$doomed" ]; then
    log "no image tags to prune (keeping at most ${KEEP})"
    return 0
  fi

  # Collected into an array *before* anything talks to the target, rather than
  # calling ssh from inside the read loop.
  #
  # ssh reads its own stdin, and inside `while read ... <<<"$list"` that stdin is
  # the remainder of the list. The first `docker image rm` therefore swallows
  # every tag after it and the loop ends after one iteration -- which is exactly
  # what happened here: a deploy that should have pruned three images pruned one,
  # reported success, and left six tags on a box with a budget of five. Nothing
  # failed, so nothing said so.
  local -a doomed_tags=()
  local tag
  while IFS= read -r tag; do
    [ -n "$tag" ] && doomed_tags+=("$tag")
  done <<<"$doomed"

  for tag in ${doomed_tags[@]+"${doomed_tags[@]}"}; do
    run on_target docker image rm "darts:${tag}"
  done
}

rollback() {
  local started="$1"

  if [ -z "$ROLLBACK_TAG" ]; then
    warn "the new build is not healthy and there is no previous image to return to"
    die "deploy failed with nothing to roll back to; the target is down"
  fi

  warn "the new build did not report ${SHA} in time; rolling back to ${ROLLBACK_TAG}"
  activate "$ROLLBACK_TAG"

  if await_sha "$ROLLBACK_TAG" "$HEALTH_RETRIES"; then
    local elapsed=$(($(date +%s) - started))
    warn "rolled back to ${ROLLBACK_TAG}; /api/healthz reports it after ${elapsed}s"
    die "deploy of ${SHA} failed and was rolled back to ${ROLLBACK_TAG}"
  fi

  warn "the rollback to ${ROLLBACK_TAG} did not come up healthy either"
  die "deploy failed and rollback failed; the target needs hands"
}

main() {
  SHA="$(git -C "$repo_root" rev-parse --short HEAD)"
  log "deploying ${SHA} to ${HOST}"

  preflight
  build_image

  read_running_sha
  list_remote_tags

  transfer_image
  install_compose_file

  stop_container
  backup_database
  migrate_database

  # Chosen before anything is activated, so the decision is made from the state
  # that existed *before* this deploy rather than from whatever wreckage a
  # broken image leaves behind.
  # shellcheck disable=SC2086
  ROLLBACK_TAG="$(select_rollback_tag "$SHA" "$RUNNING_SHA" $REMOTE_TAGS)"
  if [ -n "$ROLLBACK_TAG" ]; then
    log "rollback target if this fails: ${ROLLBACK_TAG}"
  else
    warn "no rollback target available; a failed deploy will leave the box down"
  fi

  local live_at
  live_at="$(date +%s)"
  activate "$SHA"

  if ! await_sha "$SHA" "$HEALTH_RETRIES"; then
    rollback "$live_at"
  fi

  # Both branches are planned, because the branch worth reviewing before running
  # this against a Pi is the one that only executes when the deploy has failed.
  if [ "$DRY_RUN" = "1" ]; then
    printf 'plan: on a failed poll, retag latest to the rollback target, up -d, and re-poll\n'
    printf 'plan: on a failed poll, exit non-zero even though the rollback succeeded\n'
  fi

  log "deployed ${SHA}; /api/healthz reports it after $(($(date +%s) - live_at))s"
  prune_images
}

main "$@"
