# Talking to the deploy target. Sourced by deploy.sh and rollback.sh.
#
# Split from scripts/deploy-lib.sh along a line worth keeping: that file is pure
# functions over tag lists and is unit-tested directly, this one is the only
# place either script reaches the network. Nothing here decides anything.
#
# The caller sets HOST and SSH_CONFIG from its own arguments and then calls
# remote_init, which is what builds the option array. That ordering is forced --
# the options depend on flags that have not been parsed at source time.

# shellcheck shell=bash

remote_init() {
  # BatchMode: a deploy must fail on a missing key rather than block forever on
  # a passphrase prompt at step 6 of 9.
  SSH_OPTS=(-o BatchMode=yes)
  if [ -n "${SSH_CONFIG:-}" ]; then
    SSH_OPTS=(-F "$SSH_CONFIG" -o BatchMode=yes)
  fi
}

# Named wrappers rather than inline ssh, so that `run on_target docker ...`
# produces a --dry-run plan line that reads like the thing it will do.
on_target() {
  # ssh joins its arguments into one string and the remote shell re-parses it.
  # That is relied on deliberately -- several commands here carry pipes and
  # redirections -- and every value interpolated into them is a git sha or a
  # path the calling script produced, not anything that came from the target.
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "$HOST" "$@"
}

copy_to_target() {
  local src="$1" dest="$2"
  scp "${SSH_OPTS[@]}" -- "$src" "${HOST}:${dest}"
}

# A read-only query. Deliberately *not* routed through `run`: queries have to
# actually execute for the calling script to make decisions, and in --dry-run
# they are skipped entirely rather than run against a target that may not
# exist. The placeholders that stand in for their answers are the same trick
# bootstrap-pi.sh uses when `dpkg` is absent.
query_target() {
  on_target "$@" 2>/dev/null
}

# "<tag> <full image id>", one line per darts image on the target.
remote_tag_ids() {
  query_target "docker image ls 'darts' --no-trunc --format '{{.Tag}} {{.ID}}'" || true
}

# "<full image id> <build stamp>", one line per darts image, stamp possibly
# empty. `with index .Config "Labels"` rather than `.Config.Labels` because the
# latter raises a template error -- and emits no line at all -- for an image
# whose config carries no Labels key, which every image built before
# org.darts.built-at existed does. See order_tags_by_stamp.
remote_build_stamps() {
  local refs="" id=""
  while IFS=' ' read -r _ id; do
    [ -n "$id" ] || continue
    refs="${refs} ${id}"
  done <<<"$(remote_tag_ids)"

  [ -n "$refs" ] || return 0

  query_target "docker image inspect --format '{{.Id}} {{with index .Config \"Labels\"}}{{index . \"${BUILT_AT_LABEL}\"}}{{end}}'${refs}" || true
}
