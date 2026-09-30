# The decisions scripts/backup-pull.sh makes that are worth testing on their own.
#
# Sourced, never executed -- like scripts/lib.sh and scripts/deploy-lib.sh, and
# for the same reason: the `set` options belong to the entry-point script.
#
# Everything in here is a *pure function*: names, stamps and numbers go in as
# arguments, answers come out on stdout. No curl, no sqlite3, no filesystem, and
# -- the part that matters most -- no `date` parsing. backup-pull.sh runs on the
# Mac, where `date` and `stat` are BSD; the tests run on the Linux CI runner,
# where they are GNU; and BSD `date -j -f` quietly parses a UTC stamp as local
# time unless TZ=UTC is set. Timestamps are therefore handled as the
# `YYYYMMDDTHHMMSSZ` strings they are named with, which sort lexically, and
# converted to epoch seconds by arithmetic below rather than by `date`. "Now" is
# always passed in, so every answer is deterministic.
#
# Written for bash 3.2, because that is /bin/bash on macOS and the bash this runs
# under there: no arrays at all (an empty "${arr[@]}" is an unbound-variable error
# under `set -u` before bash 4.4, and an empty backup directory is exactly that
# case), no `declare -A`, no `mapfile`, no `${var,,}`. Lists travel as "$@".
#
# No function returns non-zero for "no answer". They print nothing and succeed,
# because the caller runs under `set -e`, and `x="$(f ...)"` on a failing
# function would abort it -- tests/deploy/test_backup_pull.py runs every one of
# them under `set -Eeuo pipefail` to hold that line.

# shellcheck shell=bash

# Is this a local copy's filename?
#
#   is_copy_name <name>      # exit 0 if it is, 1 if it is not
#
# `darts-<YYYYMMDDTHHMMSSZ>.db`, the Pi's own backup naming. The one predicate
# here that *does* signal through its status, because it is only ever used as an
# `if` condition. Anything else in the directory -- the hidden temporary a pull
# is written to, a file you dropped there by hand -- is invisible to retention
# and to "newest", so it can never be deleted or mistaken for a backup.
is_copy_name() {
  case "$1" in
    darts-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.db)
      return 0
      ;;
  esac
  return 1
}

# Local copies, newest first.
#
#   copies_newest_first [name...]
#
# Filters out anything that is not a copy's name, then orders by the stamp in
# the name. Never by mtime: copying or restoring a file rewrites mtime, and the
# point of a backup is that it gets copied around (docs/durability.md makes the
# same choice for the Pi's backups). The stamp is fixed-width, so a lexical sort
# is a chronological one; LC_ALL=C so the locale cannot reorder it.
copies_newest_first() {
  local name
  for name in "$@"; do
    if is_copy_name "$name"; then
      printf '%s\n' "$name"
    fi
  done | LC_ALL=C sort -r
}

# The newest local copy, or nothing when there are none.
#
#   newest_copy [name...]
newest_copy() {
  copies_newest_first "$@" | head -n 1
}

# Which copies should retention delete?
#
#   copies_to_prune <keep> [name...]
#
# Everything after the newest <keep>, oldest last. <keep> below 1 is treated as
# 1: the newest copy is never deleted, whatever it is set to -- the same promise
# the Pi's own retention makes, and for the same reason. A mistyped setting
# should cost you history, never your only good copy.
copies_to_prune() {
  local keep="$1"
  shift
  case "$keep" in
    '' | *[!0-9]*) keep=1 ;;
  esac
  keep=$((10#$keep))
  if [ "$keep" -lt 1 ]; then
    keep=1
  fi
  copies_newest_first "$@" | tail -n "+$((keep + 1))"
}

# The stamp inside a copy's name.
#
#   stamp_of <name>          # darts-20260930T001410Z.db -> 20260930T001410Z
stamp_of() {
  local name="${1##*/}"
  name="${name#darts-}"
  printf '%s\n' "${name%.db}"
}

# Seconds since the Unix epoch for a `YYYYMMDDTHHMMSSZ` stamp.
#
#   stamp_to_epoch <stamp>
#
# Arithmetic rather than `date`, for the reasons at the top of this file. This is
# Howard Hinnant's days_from_civil: shift the year to start in March so the leap
# day falls at the end, count whole 400-year eras, then days within the era.
# Every component goes through 10# because "08" and "09" are invalid octal to
# bash. Prints nothing for a malformed stamp.
stamp_to_epoch() {
  local stamp="$1" y m d hh mm ss era yoe doy doe days
  case "$stamp" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *) return 0 ;;
  esac
  y=$((10#${stamp:0:4}))
  m=$((10#${stamp:4:2}))
  d=$((10#${stamp:6:2}))
  hh=$((10#${stamp:9:2}))
  mm=$((10#${stamp:11:2}))
  ss=$((10#${stamp:13:2}))
  if [ "$m" -le 2 ]; then
    y=$((y - 1))
  fi
  era=$((y / 400))
  yoe=$((y - era * 400))
  doy=$(((153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1))
  doe=$((yoe * 365 + yoe / 4 - yoe / 100 + doy))
  days=$((era * 146097 + doe - 719468))
  printf '%s\n' "$((days * 86400 + hh * 3600 + mm * 60 + ss))"
}

# A duration, the way a person would say it.
#
#   format_age <seconds>     # 273600 -> 3d 4h
#
# Two units at most: the warning is read at a glance, and "3d 4h 12m 9s"
# is false precision for "it has been a few days".
format_age() {
  local s="$1" d h m
  if [ "$s" -lt 0 ]; then
    s=0
  fi
  d=$((s / 86400))
  h=$((s % 86400 / 3600))
  m=$((s % 3600 / 60))
  if [ "$d" -gt 0 ]; then
    printf '%sd %sh\n' "$d" "$h"
  elif [ "$h" -gt 0 ]; then
    printf '%sh %sm\n' "$h" "$m"
  else
    printf '%sm\n' "$m"
  fi
}

# The staleness warning, if one is due.
#
#   staleness_warning <now_epoch> <threshold_hours> [name...]
#
# Prints one line when the newest copy is older than the threshold, or when
# there is no copy at all -- having no backup is the stalest possible state, and
# on a first run against a switched-off Pi it is exactly what you need to hear.
# Prints nothing when the newest copy is fresh. Strictly older than: a copy
# exactly <threshold> old is not yet stale.
staleness_warning() {
  local now="$1" hours="$2" newest epoch age
  shift 2
  newest="$(newest_copy "$@")"
  if [ -z "$newest" ]; then
    printf 'there is no local copy yet -- nothing has ever been pulled\n'
    return 0
  fi
  epoch="$(stamp_to_epoch "$(stamp_of "$newest")")"
  age=$((now - epoch))
  if [ "$age" -gt $((hours * 3600)) ]; then
    printf 'the newest local copy is %s old (%s), past the %sh threshold\n' \
      "$(format_age "$age")" "$newest" "$hours"
  fi
}

# What does a curl failure mean?
#
#   classify_curl_failure <curl_exit> <time_connect>
#
# Prints `unreachable` or `failed`. <time_connect> is curl's
# `%{time_connect}`, which stays 0.000000 unless a TCP connection was made.
#
# `unreachable` is the Pi being switched off, which is the normal state of a
# dartboard most of the day and gets one informational line and exit 0. It
# covers the ways that looks from the Mac, all measured there: a darts.local
# nobody answers for (exit 6 -- or exit 28, because macOS's mDNS resolver ran
# into the 5-second connect timeout rather than failing fast), and a connection
# that cannot be made (exit 7, which is both "refused" and "no route to host":
# a Pi whose address is still cached but which is powered down).
#
# Exit 7 also covers a Pi that is up with the app down. That is reported the
# same way on purpose. curl's own message goes into the one line, and the
# staleness warning says so if it has gone on too long; telling the two apart
# reliably would mean a second probe, for a case where nothing new is being
# written anyway.
#
# A timeout *after* a connection was made is not "switched off": that is an app
# that accepted the request and then hung, or a transfer that broke part-way,
# and it is a failure. Measured: a listener that never answers gives exit 28
# with 0 bytes received -- so bytes cannot be the test, the connection has to
# be. Everything else is a failure too: an HTTP error (22, with --fail), a
# connection reset (56), a write error (23).
classify_curl_failure() {
  local code="$1" connect="${2:-0}"
  case "$code" in
    6 | 7)
      printf 'unreachable\n'
      ;;
    28)
      # Zero in any spelling -- "0", "0.000000" -- means no connection.
      case "$connect" in
        '' | *[!0.]*) printf 'failed\n' ;;
        *) printf 'unreachable\n' ;;
      esac
      ;;
    *)
      printf 'failed\n'
      ;;
  esac
}
