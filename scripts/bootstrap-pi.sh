#!/usr/bin/env bash
# Prepare a fresh Raspberry Pi OS (Bookworm) box to run the scorekeeper.
#
# Installs Docker Engine and the Compose plugin, creates the directories the
# bind mounts in deploy/compose.yaml expect, installs the env and compose files
# into /etc/darts, and enables Docker at boot. It does not start the app --
# deploying an image is #29's job.
#
# Everything that lives on the host is owned by this script, so that a deploy is
# only ever about images and the container. That division is why scripts/deploy.sh
# needs no source checkout on the Pi and no root at any point.
#
# Idempotent by construction: every action that would create something is
# guarded by a check for the state it would create, and reports `already done`
# instead when that state is present. Running it twice on a fresh image is a
# requirement of #28, and the second run's output is the evidence.
#
# The exception is `chown`, which is re-applied every run rather than guarded.
# It is idempotent in the stronger sense -- the second run is a no-op with the
# same end state -- and re-applying it repairs ownership drift, which is the
# failure this script is most likely to be re-run to fix.
#
#   sudo scripts/bootstrap-pi.sh
#   scripts/bootstrap-pi.sh --dry-run    # print the plan, change nothing
#
# --dry-run needs no privileges and touches nothing: every mutation goes
# through `run` in scripts/lib.sh, which returns early when DRY_RUN=1.
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$repo_root"

# shellcheck source=scripts/lib.sh
. "${repo_root}/scripts/lib.sh"

# Where everything goes. Overridable from the environment so the dry-run test
# can point the whole script at a temporary directory and then assert that
# nothing was created there.
#
# BOOTSTRAP_ rather than DARTS_ on purpose: `env | grep DARTS_` is meant to be
# a complete inventory of how the *server process* is configured, and these are
# knobs belonging to a host-setup script the server never sees.
STATE_DIR="${BOOTSTRAP_STATE_DIR:-/var/lib/darts}"
SHARE_DIR="${BOOTSTRAP_SHARE_DIR:-/srv/darts-share}"
ETC_DIR="${BOOTSTRAP_ETC_DIR:-/etc/darts}"

# The account that gets docker-group access. uid 1000 on Raspberry Pi OS, which
# is the same uid the container runs as -- see deploy/Dockerfile.
BOOTSTRAP_USER="${BOOTSTRAP_USER:-pi}"

#: The container runs as this. The bind mounts have to be writable by it, and
#: the container cannot chown them itself because it is not root.
CONTAINER_UID=1000
CONTAINER_GID=1000

DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: bootstrap-pi.sh [--dry-run]

Prepare a fresh Raspberry Pi OS host to run the darts scorekeeper.

  --dry-run   Print the planned actions to stdout and exit without
              changing anything. Requires no privileges.
  -h, --help  Show this message.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
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

# Root is needed to write /var, /srv and /etc and to drive apt. Checked here
# rather than failing halfway through, which would leave the host half set up.
require_root() {
  if [ "${DRY_RUN}" = "1" ]; then
    return 0
  fi
  [ "$(id -u)" -eq 0 ] ||
    die "must run as root (try: sudo $0)"
}

# --- Docker ----------------------------------------------------------------

# Both of these fall back to a placeholder rather than failing when the tool is
# absent, so that --dry-run works on a developer machine that is not Debian.
# On the Pi -- the only host where the install path actually executes -- they
# return the real values.
deb_arch() {
  if command -v dpkg >/dev/null 2>&1; then
    dpkg --print-architecture
  else
    echo "<arch>"
  fi
}

os_codename() {
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091  # a host file, not something in this repository
    (. /etc/os-release && echo "${VERSION_CODENAME:-<codename>}")
  else
    echo "<codename>"
  fi
}

# Docker's own apt repository rather than Debian's docker.io package, because
# the Compose *plugin* (`docker compose`, not `docker-compose`) is only
# packaged there, and compose.yaml's `stop_grace_period` needs a current one.
install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    skip "Docker Engine and the Compose plugin are installed"
    return 0
  fi

  local keyring="/etc/apt/keyrings/docker.asc"
  local list="/etc/apt/sources.list.d/docker.list"

  if [ -f "$keyring" ]; then
    skip "Docker apt signing key present at ${keyring}"
  else
    run install -m 0755 -d /etc/apt/keyrings
    run curl -fsSL https://download.docker.com/linux/debian/gpg -o "$keyring"
    run chmod a+r "$keyring"
  fi

  if [ -f "$list" ]; then
    skip "Docker apt source present at ${list}"
  else
    local entry
    entry="deb [arch=$(deb_arch) signed-by=${keyring}] https://download.docker.com/linux/debian $(os_codename) stable"
    run write_file "$list" "$entry"
  fi

  run apt-get update
  run apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin
}

# A named function so that writing a file is a single `run` action, and so the
# dry-run plan shows the content that would be written rather than a shell
# redirection it cannot represent.
write_file() {
  local path="$1" content="$2"
  printf '%s\n' "$content" >"$path"
}

enable_docker_at_boot() {
  # This, plus `restart: unless-stopped`, is the entire boot story. If the
  # Docker service is not enabled, nothing brings the app back after a power
  # cut and the first acceptance criterion fails.
  if systemctl is-enabled --quiet docker 2>/dev/null; then
    skip "docker.service is enabled at boot"
  else
    run systemctl enable docker
  fi
}

add_user_to_docker_group() {
  if ! id -u "$BOOTSTRAP_USER" >/dev/null 2>&1; then
    warn "user ${BOOTSTRAP_USER} does not exist; skipping docker group membership"
    return 0
  fi
  if id -nG "$BOOTSTRAP_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    skip "${BOOTSTRAP_USER} is in the docker group"
  else
    run usermod -aG docker "$BOOTSTRAP_USER"
    warn "${BOOTSTRAP_USER} must log out and back in before docker works without sudo"
  fi
}

# --- Directories and configuration -----------------------------------------

# Created and chowned to the container's uid, because the container runs
# non-root and cannot do it itself. The server would create the database's
# parent on its own, but backups and snapshots have no such fallback, and an
# unwritable mount would surface as a degraded box rather than a clear error.
ensure_dir() {
  local path="$1"
  if [ -d "$path" ]; then
    skip "directory ${path} exists"
  else
    run mkdir -p "$path"
  fi
  run chown "${CONTAINER_UID}:${CONTAINER_GID}" "$path"
}

# Left root-owned, unlike the bind mounts. Nothing in here is written by the
# container -- these two files are read by Compose, which runs as the operator.
ensure_etc_dir() {
  if [ -d "$ETC_DIR" ]; then
    skip "directory ${ETC_DIR} exists"
  else
    run mkdir -p "$ETC_DIR"
  fi
}

install_env_file() {
  local target="${ETC_DIR}/darts.env"
  local example="${repo_root}/deploy/darts.env.example"

  [ -f "$example" ] || die "missing template: ${example}"

  # Never overwritten. This file is the one thing on the host an operator is
  # expected to edit, and clobbering it on a re-run would be exactly the
  # "duplicate state" the idempotence criterion is about.
  if [ -f "$target" ]; then
    skip "env file ${target} exists (left untouched)"
  else
    run cp "$example" "$target"
  fi
}

# The compose file deploy.sh drives the container with.
#
# It lives here, next to the env file, so that a deploy needs no source checkout
# on the Pi -- which is the whole point of #29 -- and no write access to /etc,
# which is what keeps a deploy from ever needing sudo. Running anything on the
# Pi as root would leave WAL sidecars the container cannot write; see the
# warning at the top of docs/deploy.md.
#
# Copied on **every** run, unlike darts.env, and the difference is the point:
# darts.env is the operator's file and re-running must not clobber their edits,
# whereas compose.yaml is a repository artefact and the only correct copy is the
# current one. This is the same reasoning as the `chown`s -- re-applying it is
# how ownership drift gets repaired -- and it is how a changed compose.yaml
# reaches an already-bootstrapped Pi.
install_compose_file() {
  local target="${ETC_DIR}/compose.yaml"
  local source="${repo_root}/deploy/compose.yaml"

  [ -f "$source" ] || die "missing: ${source}"

  run cp "$source" "$target"
}

# --- Result ----------------------------------------------------------------

# The point of the whole exercise: the URL to type into the phone.
print_lan_url() {
  local port="${DARTS_PORT:-8000}"
  local address=""

  # hostname -I lists every address; the first is the LAN one on a Pi with a
  # single active interface. The `|| address=""` is load-bearing: -I is a
  # Linux-only flag, and under `set -o pipefail` a failing `hostname` would
  # otherwise make this assignment non-zero and `set -e` would abort the
  # script here -- after all the real work, at the one step that exists to
  # tell the operator where the app is.
  if command -v hostname >/dev/null 2>&1; then
    address="$(hostname -I 2>/dev/null | awk '{print $1}')" || address=""
  fi
  [ -n "$address" ] || address="<this-pi>"

  log ""
  log "Bootstrap complete. Once #29 has deployed an image, the app is at:"
  log ""
  log "    http://${address}:${port}/"
  log ""
}

main() {
  require_root

  install_docker
  enable_docker_at_boot
  add_user_to_docker_group

  ensure_dir "$STATE_DIR"
  ensure_dir "${STATE_DIR}/backups"
  ensure_dir "$SHARE_DIR"

  ensure_etc_dir
  install_env_file
  install_compose_file

  print_lan_url
}

main "$@"
