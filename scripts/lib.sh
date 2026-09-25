# Shared shell helpers. Sourced, never executed -- hence no shebang and no
# `set` here: the options belong to the entry-point script, so that sourcing
# this file cannot quietly change how the caller handles errors.
#
# The division of labour that matters is the output channel. Everything human
# -- progress, warnings, failures -- goes to stderr. stdout carries only the
# plan that `--dry-run` produces, one action per line, which is what makes
# `bootstrap-pi.sh --dry-run` machine-readable without a parsing mode:
#
#     bootstrap-pi.sh --dry-run >plan.txt      # the plan, nothing else
#
# tests/deploy/test_bootstrap_dry_run.py asserts over exactly that stream.

# shellcheck shell=bash

log() {
  printf '==> %s\n' "$*" >&2
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  local cmd="$1"
  command -v -- "$cmd" >/dev/null 2>&1 ||
    die "required command not found: ${cmd}"
}

# The single chokepoint for anything that changes the host.
#
# Every mutation in bootstrap-pi.sh goes through here, which is what lets
# --dry-run be *provably* side-effect free rather than side-effect free by
# inspection: in dry-run this function returns before executing, so a mutation
# that forgot to use it is the only way to escape, and the dry-run test asserts
# the target directories are still absent afterwards. That assertion is not
# theatre -- CI runs as a user who could create /var/lib/darts for real.
run() {
  if [ "${DRY_RUN:-0}" = "1" ]; then
    printf 'plan: %s\n' "$*"
    return 0
  fi
  log "$*"
  "$@"
}

# Announce a step that is already satisfied. Idempotence is a hard requirement
# of #28 -- the script is run twice on a fresh image and must produce "no
# errors and no duplicate state" -- so every guarded action reports the skip
# rather than staying silent, making the second run's output the evidence.
skip() {
  if [ "${DRY_RUN:-0}" = "1" ]; then
    printf 'skip: %s\n' "$*"
    return 0
  fi
  log "already done: $*"
}
