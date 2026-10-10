#!/usr/bin/env bash
# Prepare a fresh Raspberry Pi OS (Bookworm) box to run the scorekeeper.
#
# Installs Docker Engine and the Compose plugin, creates the directories the
# bind mounts in deploy/compose.yaml expect, installs the env and compose files
# into /etc/darts, and enables Docker at boot. It does not start the app --
# deploying an image is #29's job.
#
# It also sets up #30's read-only snapshot share: Samba serving /srv/darts-share,
# Avahi advertising it, and a systemd timer that asks the app to republish the
# snapshot every five minutes. Samba runs on the host, not in a container,
# because it needs the host's network and files and gains nothing from Docker.
#
# And it puts HTTPS in front of the app (#71): Caddy from Debian's archive,
# terminating TLS on 443 with a certificate made on the Mac by
# scripts/make-cert.sh and handed over with --tls-from, and redirecting :80.
# The phone needs HTTPS for a secure context; see deploy/Caddyfile.
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
#   sudo scripts/bootstrap-pi.sh --tls-from ~/darts-tls   # first run, or a renewal
#   sudo scripts/bootstrap-pi.sh                          # any later run
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
SAMBA_DIR="${BOOTSTRAP_SAMBA_DIR:-/etc/samba}"
AVAHI_SERVICES_DIR="${BOOTSTRAP_AVAHI_SERVICES_DIR:-/etc/avahi/services}"
SYSTEMD_DIR="${BOOTSTRAP_SYSTEMD_DIR:-/etc/systemd/system}"
CADDY_DIR="${BOOTSTRAP_CADDY_DIR:-/etc/caddy}"

# Where the certificate and its key live on the host. Under /etc/darts with the
# env file, because like the env file they are the operator's, not the
# repository's: never committed, and never replaced unless --tls-from says so.
TLS_DIR="${ETC_DIR}/tls"

# The name the share is reached by: smb://darts.local/darts. Avahi publishes the
# host's own name, so this is a hostname check, not an Avahi setting.
SHARE_HOSTNAME="${BOOTSTRAP_HOSTNAME:-darts}"

# The account that gets docker-group access. uid 1000 on Raspberry Pi OS, which
# is the same uid the container runs as -- see deploy/Dockerfile.
BOOTSTRAP_USER="${BOOTSTRAP_USER:-pi}"

#: The container runs as this. The bind mounts have to be writable by it, and
#: the container cannot chown them itself because it is not root.
CONTAINER_UID=1000
CONTAINER_GID=1000

DRY_RUN=0

# A directory holding darts-leaf.crt and darts-leaf.key, as scripts/make-cert.sh
# writes them on the Mac. Given on the first run and on a renewal; not needed
# otherwise, because the installed copy is kept.
TLS_FROM=""

usage() {
  cat <<'EOF'
Usage: bootstrap-pi.sh [--dry-run] [--tls-from <dir>]

Prepare a fresh Raspberry Pi OS host to run the darts scorekeeper.

  --tls-from <dir>  Install the HTTPS certificate from <dir>, which holds
                    darts-leaf.crt and darts-leaf.key from
                    scripts/make-cert.sh. Required on the first run;
                    replaces the installed certificate on a renewal.
  --dry-run         Print the planned actions to stdout and exit without
                    changing anything. Requires no privileges.
  -h, --help        Show this message.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --tls-from)
      [ $# -ge 2 ] || die "--tls-from needs a directory"
      TLS_FROM="$2"
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

# Before anything changes, for the same reason as require_root: a first run
# with no certificate would otherwise install everything and then leave Caddy
# with nothing to serve. Checked as files, not parsed -- `caddy validate` reads
# them properly once they are installed.
check_tls_source() {
  local file
  if [ -n "$TLS_FROM" ]; then
    for file in darts-leaf.crt darts-leaf.key; do
      [ -f "${TLS_FROM}/${file}" ] ||
        die "--tls-from ${TLS_FROM}: no ${file} there; scripts/make-cert.sh on the Mac makes it"
    done
    return 0
  fi
  if [ -f "${TLS_DIR}/cert.pem" ] && [ -f "${TLS_DIR}/key.pem" ]; then
    return 0
  fi
  local message="no HTTPS certificate in ${TLS_DIR}: run scripts/make-cert.sh on the Mac, copy the leaf here, and re-run with --tls-from <dir> (docs/deploy.md -> HTTPS)"
  if [ "$DRY_RUN" = "1" ]; then
    warn "$message"
  else
    die "$message"
  fi
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

# --- The snapshot share (#30) ----------------------------------------------
#
# Everything below follows the same rule as compose.yaml: these are repository
# artefacts, not operator files, so they are replaced on every run and the
# service that reads them is reloaded on every run. That is how a changed
# smb.conf or timer reaches an already-bootstrapped Pi, and why none of them is
# guarded the way darts.env is.

package_installed() {
  # In an `if`, so pipefail cannot abort the script when dpkg-query is absent or
  # the package is unknown -- both of which simply mean "not installed".
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'
}

install_share_packages() {
  local missing=() package
  for package in samba avahi-daemon; do
    if package_installed "$package"; then
      skip "${package} is installed"
    else
      missing+=("$package")
    fi
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    run apt-get update
    run apt-get install -y "${missing[@]}"
  fi
}

# Checks the installed file the way smbd will read it. A broken smb.conf would
# otherwise surface only as a share that silently is not there.
check_samba_config() {
  testparm -s "$1" >/dev/null
}

install_samba_config() {
  local target="${SAMBA_DIR}/smb.conf"
  local original="${SAMBA_DIR}/smb.conf.debian-orig"
  local source="${repo_root}/deploy/smb-darts.conf"

  [ -f "$source" ] || die "missing: ${source}"

  # Debian's own file is kept once, before the first replacement, so that the
  # stock configuration can be recovered. Never overwritten afterwards: by the
  # second run smb.conf is ours, and "backing it up" would destroy the original.
  if [ -f "$original" ]; then
    skip "Debian's smb.conf kept at ${original}"
  elif [ -f "$target" ]; then
    run cp "$target" "$original"
  else
    skip "no stock smb.conf to keep"
  fi

  run install -m 0644 "$source" "$target"
  run check_samba_config "$target"
}

enable_service() {
  local unit="$1"
  if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
    skip "${unit} is enabled at boot"
  else
    run systemctl enable "$unit"
  fi
}

start_samba() {
  enable_service smbd
  # A full restart on every run, not a reload. Measured on the stand-in: the
  # package starts smbd with Debian's stock config, and a reload re-reads the
  # shares but does not rebind sockets -- so `smb ports = 445` never took effect
  # and smbd kept listening on 139 until something restarted it. A restart drops
  # any mounted clients for a moment; Finder reconnects, and bootstrap is rare.
  run systemctl restart smbd

  # smb-darts.conf turns NetBIOS off, which leaves nmbd with nothing to do.
  # Stopped rather than left to fail at every boot. Guarded both ways: absent on
  # a host whose Samba packaging has no nmbd, already disabled on a re-run.
  if systemctl is-enabled --quiet nmbd 2>/dev/null; then
    run systemctl disable --now nmbd
  else
    skip "nmbd is not enabled (NetBIOS is off)"
  fi
}

install_avahi_service() {
  local source="${repo_root}/deploy/avahi-darts.service"
  [ -f "$source" ] || die "missing: ${source}"

  # Avahi watches this directory and picks the file up without a restart.
  run install -m 0644 "$source" "${AVAHI_SERVICES_DIR}/darts.service"
  enable_service avahi-daemon
}

# Warned, not fixed. Since #71 this matters to the app as well as the share:
# deploy/Caddyfile and the certificate are for darts.local only.
#
# Renaming a live box also means rewriting /etc/hosts, and
# the right time to choose the name is when the SD card is flashed -- Raspberry
# Pi Imager asks for it. Everything else in #30 works under any name; only the
# address changes.
check_hostname() {
  local current=""
  current="$(hostname 2>/dev/null)" || current=""
  current="${current%%.*}"
  if [ "$current" = "$SHARE_HOSTNAME" ]; then
    skip "hostname is ${SHARE_HOSTNAME}, so ${SHARE_HOSTNAME}.local resolves"
  else
    warn "hostname is '${current:-unknown}', not '${SHARE_HOSTNAME}': the share will be at" \
      "smb://${current:-<this-pi>}.local/darts, not smb://${SHARE_HOSTNAME}.local/darts," \
      "and https://${SHARE_HOSTNAME}.local/ will not resolve -- the certificate covers no other name." \
      "Set the name in Raspberry Pi Imager, or: sudo raspi-config nonint do_hostname ${SHARE_HOSTNAME}"
  fi
}

install_snapshot_timer() {
  local unit
  for unit in darts-snapshot.service darts-snapshot.timer; do
    [ -f "${repo_root}/deploy/${unit}" ] || die "missing: ${repo_root}/deploy/${unit}"
    run install -m 0644 "${repo_root}/deploy/${unit}" "${SYSTEMD_DIR}/${unit}"
  done
  run systemctl daemon-reload
  enable_service darts-snapshot.timer
  # Restarted on every run so a changed schedule applies now. Restarting a
  # timer does not fire its service; the first run is still OnBootSec or
  # OnUnitActiveSec away.
  run systemctl restart darts-snapshot.timer
}

# --- HTTPS (#71) -----------------------------------------------------------
#
# Caddy terminates TLS on 443 and proxies to the app on 127.0.0.1:8000. Why
# Caddy, why on the host, and why no HSTS: deploy/Caddyfile. Why the
# certificate is made on the Mac and the Pi's clock does not matter:
# scripts/make-cert.sh.

install_caddy_package() {
  if package_installed caddy; then
    skip "caddy is installed"
  else
    run apt-get update
    run apt-get install -y caddy
  fi
}

# Only with --tls-from: an installed certificate is the operator's, like
# darts.env, and a re-run without one keeps it. With --tls-from it is replaced,
# because that is what a renewal is.
#
# The key is readable by Caddy's group and nobody else; Debian's package runs
# Caddy as the `caddy` user, which the package install above creates.
install_tls_files() {
  if [ -z "$TLS_FROM" ]; then
    skip "certificate in ${TLS_DIR} (left untouched; --tls-from replaces it)"
    return 0
  fi
  run install -d -m 0750 -o root -g caddy "$TLS_DIR"
  run install -m 0644 -o root -g root "${TLS_FROM}/darts-leaf.crt" "${TLS_DIR}/cert.pem"
  run install -m 0640 -o root -g caddy "${TLS_FROM}/darts-leaf.key" "${TLS_DIR}/key.pem"
}

check_caddy_config() {
  caddy validate --adapter caddyfile --config "$1" >/dev/null
}

install_caddyfile() {
  local source="${repo_root}/deploy/Caddyfile"
  local target="${CADDY_DIR}/Caddyfile"
  [ -f "$source" ] || die "missing: ${source}"

  # Replaced whole on every run, like smb.conf -- Debian's stock Caddyfile
  # serves a placeholder page on :80 and nothing worth keeping.
  run install -m 0644 "$source" "$target"
  # Validates the certificate too: Caddy loads it to check it.
  run check_caddy_config "$target"
}

start_caddy() {
  enable_service caddy
  # Restart, not reload: a reload goes through Caddy's admin API, and a restart
  # is the same on every run whether or not Caddy was running. It drops open
  # connections for a moment; the phone's next request reconnects.
  run systemctl restart caddy
}

# Warned, not fixed: renewing is a step on the Mac (scripts/make-cert.sh), and
# the phone is what would show the warning. Thirty days is notice enough for a
# board used weekly. Skipped where there is no openssl to ask.
warn_if_certificate_expiring() {
  local cert="${TLS_DIR}/cert.pem"
  [ -z "$TLS_FROM" ] || cert="${TLS_FROM}/darts-leaf.crt"
  [ -f "$cert" ] || return 0
  command -v openssl >/dev/null 2>&1 || return 0
  local expires=""
  expires="$(openssl x509 -noout -enddate -in "$cert" 2>/dev/null)" || expires=""
  if openssl x509 -noout -checkend $((30 * 86400)) -in "$cert" >/dev/null 2>&1; then
    skip "certificate valid until ${expires#notAfter=}"
  else
    warn "the HTTPS certificate expires ${expires#notAfter=}: run scripts/make-cert.sh on the Mac" \
      "and re-run this with --tls-from, or the phone will show a certificate warning"
  fi
}

# --- Result ----------------------------------------------------------------

# The point of the whole exercise: the URL to type into the phone.
#
# A name, never an address: the certificate is for darts.local and nothing
# else (#71), so https://<ip>/ fails the handshake. That is also why
# check_hostname matters more than it did -- under any other hostname the
# phone would be at a name the certificate does not cover.
print_lan_url() {
  # The name Avahi is actually publishing, which check_hostname has already
  # warned about if it is not the one the docs assume.
  local name=""
  name="$(hostname 2>/dev/null)" || name=""
  name="${name%%.*}"

  log ""
  log "Bootstrap complete. Once #29 has deployed an image, the app is at:"
  log ""
  log "    https://${SHARE_HOSTNAME}.local/"
  log ""
  log "and its snapshots, read-only, from Finder (Go > Connect to Server):"
  log ""
  log "    smb://${name:-${SHARE_HOSTNAME}}.local/darts"
  log ""
}

main() {
  require_root
  check_tls_source

  install_docker
  enable_docker_at_boot
  add_user_to_docker_group

  ensure_dir "$STATE_DIR"
  ensure_dir "${STATE_DIR}/backups"
  ensure_dir "$SHARE_DIR"

  ensure_etc_dir
  install_env_file
  install_compose_file

  install_share_packages
  install_samba_config
  start_samba
  install_avahi_service
  check_hostname
  install_snapshot_timer

  install_caddy_package
  install_tls_files
  install_caddyfile
  start_caddy
  warn_if_certificate_expiring

  print_lan_url
}

main "$@"
