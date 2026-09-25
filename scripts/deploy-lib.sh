# The two decisions deploy.sh makes that are worth testing on their own.
#
# Sourced, never executed -- like scripts/lib.sh, and for the same reason: the
# `set` options belong to the entry-point script.
#
# Everything in here is a *pure function*. No ssh, no docker, no filesystem:
# tags go in as arguments, tags come out on stdout. That is deliberate and it is
# the whole point of the file existing. Rollback selection and tag pruning are
# the only logic in #29 that can be wrong in a way no amount of watching a
# deploy succeed would reveal -- pruning is wrong when it deletes the image you
# are about to need, and rollback selection is wrong when the box is already
# broken and nobody is watching. Both are exercised by
# tests/deploy/test_deploy_selection.py with synthetic tag lists, including the
# cases a real Pi would take months to produce.
#
# Neither function returns non-zero for "no answer". They print nothing and
# succeed, because the caller runs under `set -e` and
# `tag="$(select_rollback_tag ...)"` on a failing function would abort the
# script -- at the exact moment the script's remaining job is to recover.

# shellcheck shell=bash

#: The image label deploy.sh stamps at build time and orders by. A label rather
#: than a file on the target: it lives in the image config, so it survives
#: `docker save` / `docker load` and describes the artefact rather than the host
#: it happens to be sitting on. A deploy log on the Pi would be one more piece
#: of state that has to stay correct for a rollback to work.
#:
# Read by deploy.sh (which sets it) and deploy-remote.sh (which reads it back),
# neither of which shellcheck can see from here.
# shellcheck disable=SC2034
BUILT_AT_LABEL="org.darts.built-at"

# Which image tag should a failed deploy of $new_sha return to?
#
#   select_rollback_tag <new_sha> <running_sha> [available_sha...]
#
# `available_sha` is the sha tags present on the target, newest first.
# `running_sha` is what /api/healthz reported *before* the deploy touched
# anything, or empty if nothing was answering.
#
# Prints the chosen sha, or nothing at all when there is no candidate -- a
# first-ever deploy has nothing to go back to, and saying so plainly is better
# than inventing a target.
select_rollback_tag() {
  local new_sha="$1" running_sha="$2" tag
  shift 2

  # The running sha first, when its image is still on the box. This is ground
  # truth from the application itself rather than an inference from Docker's
  # metadata: it is the thing that was demonstrably serving traffic a moment
  # ago, which is a stronger claim than "the most recently created image".
  if [ -n "$running_sha" ] && [ "$running_sha" != "$new_sha" ]; then
    for tag in "$@"; do
      if [ "$tag" = "$running_sha" ]; then
        printf '%s\n' "$running_sha"
        return 0
      fi
    done
  fi

  # Otherwise the newest image that is not the one being deployed. Reached when
  # the box was down before the deploy (nothing reported a sha), or when the
  # running sha's tag has been pruned out from under it.
  for tag in "$@"; do
    if [ -n "$tag" ] && [ "$tag" != "$new_sha" ]; then
      printf '%s\n' "$tag"
      return 0
    fi
  done
}

# Put image tags in build order, newest first.
#
#   order_tags_by_stamp "<tag> <id>..." "<id> <stamp>..."
#
# Both arguments are multi-line strings straight out of docker on the target:
# `docker image ls --no-trunc --format '{{.Tag}} {{.ID}}'` and
# `docker image inspect --format '{{.Id}} {{...built-at label...}}'`.
#
# This is here, rather than inline in the two scripts that need it, because the
# obvious ways to do it are both wrong and both fail quietly.
#
# Sorting by `docker image ls` order does not work: it sorts by the image's
# Created timestamp, and BuildKit copies that from the cached parent rather than
# setting it when the image is assembled. Measured on Engine 29.5.2, seven
# distinct images built from four commits over twenty minutes all reported
# Created as 2026-09-25T12:34:10.61878831-04:00. The sort is then arbitrary
# while looking chronological.
#
# Pairing the labels to the tags by position does not work either, because
# `docker image inspect` does not emit one line per argument in argument order.
# Given an image whose config has no `Labels` key, the template errors to stderr
# and emits nothing for it; the lines that do come back are in an order of their
# own. Measured: seven images in, three lines out, the third belonging to the
# fourth argument. So the join is on the image id, explicitly.
#
# A tag whose stamp is missing or non-numeric sorts last, which is correct: it
# was built before the label existed, so it is older than anything carrying one.
order_tags_by_stamp() {
  local pairs="$1" stamps="$2"
  local tag="" id="" stamp="" combined=""

  while IFS=' ' read -r tag id; do
    [ -n "$tag" ] || continue
    case "$tag" in
      latest | '<none>') continue ;;
    esac
    stamp="$(printf '%s\n' "$stamps" | awk -v want="$id" '$1 == want { print $2; exit }')"
    case "$stamp" in
      '' | *[!0-9]*) stamp=0 ;;
    esac
    combined="${combined}${stamp} ${tag}"$'\n'
  done <<<"$pairs"

  [ -n "$combined" ] || return 0
  printf '%s' "$combined" | sort -k1,1rn | awk '{ print $2 }'
}

# Which image tags should be deleted to get down to $keep?
#
#   tags_to_prune <keep> <protected> [available_sha...]
#
# `protected` is a space-separated list that must survive regardless of age --
# in practice the sha just deployed and the rollback target. `available_sha` is
# newest first. Prints the tags to delete, one per line.
#
# Protected tags are reserved *first* and the remaining slots filled newest
# first, rather than keeping the newest $keep and then rescuing anything
# protected. The difference only shows when a protected tag is older than the
# newest $keep -- and there the second approach would leave $keep+1 tags on the
# box, quietly breaking the "never exceed 5" criterion in the one situation
# that matters, a rollback to something that has been sitting there a while.
tags_to_prune() {
  local keep="$1" protected="$2" tag candidate
  shift 2

  # The kept set is a newline-delimited string rather than an array on purpose.
  # Arrays here would need the `${a[@]+"${a[@]}"}` dance to stay safe under
  # `set -u` when empty, and this file has to work under whichever bash the
  # operator's Mac happens to ship -- which is still 3.2 by default.
  local kept="" kept_count=0

  # Reserved slots. Only tags that actually exist on the box count against the
  # budget; a protected sha that was never loaded is not occupying anything.
  local -a wanted=()
  read -ra wanted <<<"$protected"
  for candidate in ${wanted[@]+"${wanted[@]}"}; do
    for tag in "$@"; do
      if [ "$tag" = "$candidate" ] && ! _tag_in_set "$tag" "$kept"; then
        kept="${kept}${tag}"$'\n'
        kept_count=$((kept_count + 1))
      fi
    done
  done

  # Then newest first until the budget is spent.
  for tag in "$@"; do
    [ "$kept_count" -lt "$keep" ] || break
    if ! _tag_in_set "$tag" "$kept"; then
      kept="${kept}${tag}"$'\n'
      kept_count=$((kept_count + 1))
    fi
  done

  for tag in "$@"; do
    if ! _tag_in_set "$tag" "$kept"; then
      printf '%s\n' "$tag"
    fi
  done
}

# Exact-line membership. A substring test would be wrong the moment one sha is a
# prefix of another, which `git rev-parse --short` makes perfectly possible.
_tag_in_set() {
  local needle="$1" haystack="$2" line
  while IFS= read -r line; do
    [ "$line" = "$needle" ] && return 0
  done <<<"$haystack"
  return 1
}
