#!/usr/bin/env bash
# Copy the scorekeeper's database off the Pi, onto this Mac.
#
# The Pi keeps its own backups (docs/durability.md), but they live on the same SD
# card as the database, and SD cards in a Pi that is power-cycled constantly are
# exactly what dies. This puts a verified copy somewhere else. It is run by hand,
# whenever you want a copy -- there is no schedule (#31, by decision).
#
#   scripts/backup-pull.sh
#   scripts/backup-pull.sh --help
#
# What it does, in order:
#
#   1. Downloads GET /api/export/db, a copy the app takes the moment it is asked,
#      with its WAL collapsed into it. Nothing to mount, no SSH key, no
#      credentials. The download goes to a hidden temporary *in the destination
#      directory*, never over an existing copy.
#   2. Refuses it unless `PRAGMA integrity_check` says exactly `ok` and
#      `PRAGMA user_version` is above 0. The second check is not decoration: a
#      zero-byte file is a valid, empty SQLite database and passes
#      integrity_check with `ok` (measured with /usr/bin/sqlite3 3.51.0), and the
#      endpoint streams with no Content-Length, so a Pi that loses power
#      mid-download can hand curl a clean-looking end of body.
#   3. Renames it into place as darts-<YYYYMMDDTHHMMSSZ>.db -- the Pi's own
#      naming, so "newest" is read from the filename, never from mtime.
#   4. Deletes all but the newest BACKUP_PULL_KEEP copies. The newest is never
#      deleted.
#
# When the Pi is switched off -- the normal state of a dartboard -- it prints one
# informational line and exits 0. Any run that ends without a new copy also
# checks the newest one it already has, and adds one warning line when that is
# older than BACKUP_PULL_STALE_HOURS, so a Pi that has not been reachable for a
# week does not stay a secret.
#
# Exit status: 0 for a new copy or a switched-off Pi; 1 for everything else,
# including a download that was rejected. A rejected download never touches the
# copies already here.
#
# Runs with nothing but what ships with macOS -- /usr/bin/curl and
# /usr/bin/sqlite3 (3.37+ reads this schema's STRICT tables; macOS has 3.51) --
# under /bin/bash 3.2, so it keeps to bash 3.2. No Python and no uv. The
# decisions in it are pure functions in scripts/backup-pull-lib.sh, tested from
# tests/deploy/test_backup_pull.py, which also runs this script end to end.
#
# Recovering *from* these copies is docs/dr.md.
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=scripts/lib.sh
. "${repo_root}/scripts/lib.sh"
# shellcheck source=scripts/backup-pull-lib.sh
. "${repo_root}/scripts/backup-pull-lib.sh"

# BACKUP_PULL_ rather than DARTS_, for the reason bootstrap-pi.sh gives for
# BOOTSTRAP_: DARTS_ is the inventory of how the server is configured, and the
# server never sees these.
#
# The URL is overridable so the tests can hand the script a deliberately
# corrupted file through file://, which curl reads exactly as it reads HTTP, and
# so a rehearsal can point it at a stand-in.
URL="${BACKUP_PULL_URL:-https://darts.local/api/export/db}"
# The Pi serves HTTPS with a certificate from our own root (#71,
# scripts/make-cert.sh), which nothing on this Mac trusts by default. Handed to
# curl explicitly rather than added to the login keychain: /usr/bin/curl would
# read the keychain, but trusting a root there is a security setting for the
# whole Mac, and this script needs it for one URL. The default is where
# make-cert.sh keeps it, so on the Mac that made the certificate there is
# nothing to set. Used only for an https:// URL, and only if the file exists,
# so the tests' file:// URLs and a keychain-trusting setup both still work.
CACERT="${BACKUP_PULL_CACERT:-${HOME}/Library/Application Support/darts-tls/darts-root.crt}"
# Application Support rather than ~/Documents, ~/Desktop or ~/Downloads, which
# macOS's privacy controls (TCC) fence off from anything you have not granted
# access to -- and not anywhere iCloud syncs, because an off-site copy is out of
# #31's scope.
DEST="${BACKUP_PULL_DIR:-${HOME}/Library/Application Support/darts-backups}"
KEEP="${BACKUP_PULL_KEEP:-30}"
STALE_HOURS="${BACKUP_PULL_STALE_HOURS:-24}"

# How long to wait. Connecting includes resolving darts.local, and five seconds
# is what macOS's mDNS lookup of an absent .local name takes to give up anyway.
# The whole transfer gets far longer: a 50,000-dart database is ~15 MB.
CONNECT_TIMEOUT="${BACKUP_PULL_CONNECT_TIMEOUT:-5}"
MAX_TIME="${BACKUP_PULL_MAX_TIME:-300}"

usage() {
  cat <<EOF
Usage: scripts/backup-pull.sh

Pull a verified copy of the scorekeeper database from the Pi onto this Mac.

Settings (environment):
  BACKUP_PULL_URL          where to pull from     [${URL}]
  BACKUP_PULL_CACERT       root to trust for it   [${CACERT}]
  BACKUP_PULL_DIR          where copies are kept  [${DEST}]
  BACKUP_PULL_KEEP         copies to keep         [${KEEP}]
  BACKUP_PULL_STALE_HOURS  warn past this age     [${STALE_HOURS}]

Exits 0 when a copy was pulled, and when the Pi is simply switched off.
EOF
}

case "${1:-}" in
  '') ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    die "unexpected argument: $1"
    ;;
esac

case "$KEEP" in
  '' | *[!0-9]*) die "BACKUP_PULL_KEEP must be a whole number, got: ${KEEP}" ;;
esac
case "$STALE_HOURS" in
  '' | *[!0-9]*) die "BACKUP_PULL_STALE_HOURS must be a whole number, got: ${STALE_HOURS}" ;;
esac

require_cmd curl
require_cmd sqlite3

info() {
  printf 'info: %s\n' "$*" >&2
}

# The copies already here, as "$@"-ready lines. No arrays: bash 3.2.
list_copies() {
  local path
  for path in "$DEST"/darts-*.db; do
    [ -e "$path" ] || continue
    printf '%s\n' "${path##*/}"
  done
}

# Called on every run that ends without a new copy.
warn_if_stale() {
  local line
  # Word-splitting the list is safe: a copy's name is checked against a pattern
  # with no spaces in it before anything uses it.
  # shellcheck disable=SC2046
  line="$(staleness_warning "$(date -u +%s)" "$STALE_HOURS" $(list_copies))"
  if [ -n "$line" ]; then
    warn "$line"
  fi
}

mkdir -p -- "$DEST"

tmp="$(mktemp "${DEST}/.pull.XXXXXX")"
cleanup() {
  rm -f -- "$tmp"
}
trap cleanup EXIT

# --fail so an HTTP error is a curl exit (22), not an error page saved as a
# "database". The write-out is the connect time, which is what tells a Pi that
# is off from an app that accepted the request and hung -- see
# classify_curl_failure.
#
# The positional parameters carry the optional --cacert, because they are the
# one list bash 3.2 expands safely when empty under `set -u`.
set --
case "$URL" in
  https://*) [ ! -f "$CACERT" ] || set -- --cacert "$CACERT" ;;
esac
started="$(date -u +%s)"
curl_err="$(mktemp "${DEST}/.pull-err.XXXXXX")"
set +e
connect="$(curl --silent --show-error --fail "$@" \
  --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
  --output "$tmp" --write-out '%{time_connect}' \
  "$URL" 2>"$curl_err")"
code=$?
set -e
reason="$(head -n 1 -- "$curl_err" || true)"
rm -f -- "$curl_err"

if [ "$code" -ne 0 ]; then
  reason="${reason#curl: }"
  reason="curl exit ${code}: ${reason#\(*\) }"
  if [ "$(classify_curl_failure "$code" "$connect")" = unreachable ]; then
    info "the Pi is not reachable at ${URL} (${reason}); probably switched off -- nothing pulled"
    warn_if_stale
    exit 0
  fi
  warn "the pull from ${URL} failed (${reason})"
  # 60 is curl's "certificate not trusted". Said plainly, because the likely
  # cause is a Mac that has never run make-cert.sh, or a renewed root.
  if [ "$code" -eq 60 ]; then
    warn "the Pi's certificate is not trusted: is ${CACERT} the root from scripts/make-cert.sh?" \
      "(BACKUP_PULL_CACERT points elsewhere)"
  fi
  warn_if_stale
  exit 1
fi

# Verify before accepting. `-readonly` so checking cannot change the file, and
# the exported copy is in rollback-journal mode, so it leaves no sidecars.
# `|| true` because a file that is not a database at all makes sqlite3 exit
# non-zero, which is an answer to report, not a reason for set -e to leave
# silently.
check="$(sqlite3 -readonly "$tmp" 'PRAGMA integrity_check;' 2>&1 || true)"
version="$(sqlite3 -readonly "$tmp" 'PRAGMA user_version;' 2>/dev/null || true)"
size="$(wc -c <"$tmp" | tr -d ' ')"

if [ "$check" != ok ]; then
  warn "rejected the download (${size} bytes): integrity_check said: $(printf '%s' "$check" | head -n 3 | tr '\n' ' ')"
  warn "the copies already in ${DEST} are untouched"
  warn_if_stale
  exit 1
fi
case "$version" in
  '' | *[!0-9]* | 0)
    warn "rejected the download (${size} bytes): user_version is '${version}', so it is not a scorekeeper database"
    warn "the copies already in ${DEST} are untouched"
    warn_if_stale
    exit 1
    ;;
esac

name="darts-$(date -u +%Y%m%dT%H%M%SZ).db"
if [ -e "${DEST}/${name}" ]; then
  # Two pulls inside the same second. The first one's copy is as good.
  info "already have ${name}; nothing to add"
  exit 0
fi
mv -f -- "$tmp" "${DEST}/${name}"
elapsed=$(($(date -u +%s) - started))
log "pulled ${name} (${size} bytes, schema version ${version}, integrity ok) in ${elapsed}s"

# shellcheck disable=SC2046
for old in $(copies_to_prune "$KEEP" $(list_copies)); do
  rm -f -- "${DEST}/${old}"
  log "retention: deleted ${old} (keeping the newest ${KEEP})"
done
