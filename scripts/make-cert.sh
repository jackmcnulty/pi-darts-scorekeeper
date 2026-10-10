#!/usr/bin/env bash
# Make the certificate the Pi serves https://darts.local/ with. Run on the Mac.
#
# Why this exists (#71). Safari only gives a page `navigator.wakeLock` and
# service workers in a secure context, and http://darts.local:8000 is not one, so
# on the phone #24's wake lock and #21's offline shell silently did nothing. The
# fix is HTTPS with a certificate the iPhone trusts. Nothing public can vouch for
# a .local name, so this is a private certificate authority of our own: a root
# installed once on the phone, and a leaf certificate for darts.local signed by
# it, which Caddy on the Pi serves (deploy/Caddyfile).
#
# Why on the Mac, and why the CA key never leaves it. Whoever holds a trusted
# root's private key can mint a certificate the phone will believe. Two things
# keep that small:
#
#   * The root is name-constrained to darts.local (and no IP addresses), so a
#     phone that honours the constraint -- iOS has enforced nameConstraints since
#     iOS 9 -- will refuse anything it signs for any other name. Measured on
#     this Mac: `security verify-cert` refuses a leaf for another name signed by
#     this root, and so does curl. ops.md's checklist tests it on the phone.
#   * The key stays here, in a 0700 directory. The Pi gets only the leaf and the
#     leaf's key, so a compromised Pi can impersonate darts.local and nothing
#     else -- which it could do anyway, being darts.local.
#
# Why the Pi's clock does not matter. The Pi has no RTC battery: after a power
# cut it boots at 1970, fake-hwclock winds it forward to its last hourly save,
# and NTP corrects it ~40 s later (measured on the real Pi, 2026-10-10). Nothing
# on the Pi issues, renews or checks a certificate; it only serves this one. The
# phone checks the dates, against its own clock.
#
# Why 820 days. iOS 13+ refuses a TLS server certificate valid for more than 825
# days, user-installed root or not. Apple's 398-day limit applies only to
# certificates from roots Apple ships, and "will not affect certificates issued
# from user-added or administrator-added Root CAs" (support.apple.com/102028).
# Measured with macOS 26's own trust engine (`security verify-cert -p ssl`,
# which iOS shares) against a root made by this script: 825 days verifies, 826
# is refused. 820 leaves a margin. Renewing is this script again, then the copy
# to the Pi it prints; the root, and so the phone, is unchanged.
#
# Re-running reuses the root and issues a fresh leaf -- that is the renewal.
# To start over with a new root (a lost or leaked key), delete the directory;
# the phone then has to trust the new root too.
#
#   scripts/make-cert.sh
#   scripts/make-cert.sh --help
#
# Uses only /usr/bin/openssl, which macOS ships (LibreSSL), and works with
# OpenSSL 3 as well, which is what CI runs it under (tests/deploy/test_make_cert.py).
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=scripts/lib.sh
. "${repo_root}/scripts/lib.sh"

# MAKE_CERT_ rather than DARTS_, for the reason bootstrap-pi.sh gives for
# BOOTSTRAP_: DARTS_ is the inventory of how the server is configured.
#
# Application Support, not the repository: these are secrets, and the root's
# key is the most sensitive file anywhere in this project. Not anywhere iCloud
# syncs, either. Time Machine, if it is on, keeps a copy, which is the backup
# story for it -- losing it costs re-trusting a new root on the phone at the
# next renewal, never data.
DIR="${MAKE_CERT_DIR:-${HOME}/Library/Application Support/darts-tls}"
NAME="${MAKE_CERT_NAME:-darts.local}"
LEAF_DAYS="${MAKE_CERT_LEAF_DAYS:-820}"
ROOT_DAYS="${MAKE_CERT_ROOT_DAYS:-3650}"

usage() {
  cat <<EOF
Usage: scripts/make-cert.sh

Make (or renew) the HTTPS certificate for the Pi, signed by a private root
that lives only on this Mac.

Writes, in ${DIR}:
  darts-root.crt   the root -- AirDrop this to the iPhone, once
  darts-root.key   the root's private key -- never leaves this Mac
  darts-leaf.crt   the certificate the Pi serves
  darts-leaf.key   its private key -- copied to the Pi, nowhere else

Settings (environment):
  MAKE_CERT_DIR        where the files live   [${DIR}]
  MAKE_CERT_NAME       the name to certify    [${NAME}]
                       (must match deploy/Caddyfile's site address)
  MAKE_CERT_LEAF_DAYS  leaf lifetime, <= 825  [${LEAF_DAYS}]
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

case "$LEAF_DAYS" in
  '' | *[!0-9]*) die "MAKE_CERT_LEAF_DAYS must be a whole number, got: ${LEAF_DAYS}" ;;
esac
# Checked here because the phone would only say "not trusted", with no reason.
[ "$LEAF_DAYS" -le 825 ] ||
  die "MAKE_CERT_LEAF_DAYS is ${LEAF_DAYS}; iOS refuses a leaf valid for more than 825 days"

require_cmd openssl

root_key="${DIR}/darts-root.key"
root_crt="${DIR}/darts-root.crt"
leaf_key="${DIR}/darts-leaf.key"
leaf_crt="${DIR}/darts-leaf.crt"

umask 077
mkdir -p -- "$DIR"
chmod 700 "$DIR"

work="$(mktemp -d)"
cleanup() {
  rm -rf -- "$work"
}
trap cleanup EXIT

# Extensions in a config file rather than -addext, which LibreSSL 3.3 lacks.
#
# The root: a CA that may sign leaves only (pathlen:0), only for $NAME and names
# under it, and for no IP address at all -- excluding every v4 and v6 address
# is how a DNS-only constraint is made to cover IP SANs too, since a constraint
# of one type says nothing about the others. Critical, as RFC 5280 requires.
#
# The leaf: what iOS 13 requires of any TLS server certificate -- the name in
# the SAN (not just the CN), serverAuth in the EKU, SHA-256, a P-256 key.
cat >"${work}/openssl.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = Darts LAN root (${NAME} only)
[root]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical, permitted;DNS:${NAME}, excluded;IP:0.0.0.0/0.0.0.0, excluded;IP:0:0:0:0:0:0:0:0/0:0:0:0:0:0:0:0
[leaf]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:${NAME}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF

# A random serial: two leaves from the same root must never share one, and
# a counter file would be one more thing to lose. The leading 1 keeps it
# positive whatever the random bytes are.
serial() {
  printf '0x1%s' "$(openssl rand -hex 15)"
}

if [ -f "$root_key" ] && [ -f "$root_crt" ]; then
  log "reusing the root in ${DIR} (the phone already trusts it)"
else
  [ ! -f "$root_key" ] && [ ! -f "$root_crt" ] ||
    die "only half a root in ${DIR}; delete both darts-root.* files to start over"
  log "making a new root, valid ${ROOT_DAYS} days, for ${NAME} only"
  # P-256: smaller and faster than RSA on the Pi, and accepted by iOS.
  openssl ecparam -genkey -name prime256v1 -noout -out "$root_key"
  openssl req -new -x509 -sha256 -days "$ROOT_DAYS" -set_serial "$(serial)" \
    -key "$root_key" -config "${work}/openssl.cnf" -extensions root -out "$root_crt"
fi

log "issuing a leaf for ${NAME}, valid ${LEAF_DAYS} days"
openssl ecparam -genkey -name prime256v1 -noout -out "${work}/leaf.key"
openssl req -new -sha256 -key "${work}/leaf.key" -subj "/CN=${NAME}" -out "${work}/leaf.csr"
openssl x509 -req -sha256 -days "$LEAF_DAYS" -set_serial "$(serial)" \
  -in "${work}/leaf.csr" -CA "$root_crt" -CAkey "$root_key" \
  -extfile "${work}/openssl.cnf" -extensions leaf -out "${work}/leaf.crt" 2>/dev/null

# Checked before anything is replaced: a leaf that does not chain to the root
# would be a certificate warning on the phone and nothing more informative.
openssl verify -CAfile "$root_crt" "${work}/leaf.crt" >/dev/null ||
  die "the new leaf does not verify against ${root_crt}; nothing replaced"

mv -f -- "${work}/leaf.key" "$leaf_key"
mv -f -- "${work}/leaf.crt" "$leaf_crt"
chmod 600 "$root_key" "$leaf_key"
chmod 644 "$root_crt" "$leaf_crt"

expires="$(openssl x509 -noout -enddate -in "$leaf_crt")"
log "done: ${leaf_crt} (${expires#notAfter=})"
log ""
log "Next, to put it on the Pi (scripts/bootstrap-pi.sh installs it):"
log ""
log "    ssh jackm@${NAME} 'mkdir -p -m 700 darts-tls'"
log "    scp \"${leaf_crt}\" \"${leaf_key}\" jackm@${NAME}:darts-tls/"
log "    ssh jackm@${NAME}"
log "    sudo ~/pi-darts-scorekeeper/scripts/bootstrap-pi.sh --tls-from ~/darts-tls"
log "    rm -r ~/darts-tls"
log ""
log "The first time only, AirDrop ${root_crt} to the iPhone and trust it:"
log "docs/ops.md -> Trusting the Pi's certificate."
