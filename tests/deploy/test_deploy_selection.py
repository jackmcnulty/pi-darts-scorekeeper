"""Assertions over the two decisions in scripts/deploy-lib.sh.

These are the only parts of #29 that can be wrong in a way that watching a
successful deploy would never reveal, which is why they were pulled out into
pure functions to begin with.

*Rollback selection* runs only when a deploy has already failed. There is no
happy path through it, so the first time it executes for real is the first time
the Pi is broken -- and if it picks the wrong tag then, it turns a failed deploy
into an outage. Every branch of it is exercised here, including "the image that
was serving has been pruned away" and "there is nothing to go back to", neither
of which a hand-run drill produces on demand.

*Tag pruning* deletes things. The failure that matters is not leaving six images
on the card, it is deleting the fifth while it is the one rollback needs, and
that only happens on a specific combination of ages a real Pi would take months
to reach.

The functions are run under `set -Eeuo pipefail`, the options every entry-point
script in this repository sets, because several of the bugs worth catching here
only exist under them: an unset-variable expansion of an empty array, or a
function that signals "no answer" by returning non-zero and so aborts its caller
at the exact moment the caller's remaining job is to recover.
"""

import shlex
import subprocess
from pathlib import Path

import pytest

#: tests/deploy/test_deploy_selection.py -> ../..
REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_LIB = REPO_ROOT / "scripts" / "deploy-lib.sh"


def call(function: str, *args: str) -> list[str]:
    """Source deploy-lib.sh and call one function, returning its stdout lines."""
    quoted = " ".join(shlex.quote(arg) for arg in args)
    script = f"set -Eeuo pipefail\n. {shlex.quote(str(DEPLOY_LIB))}\n{function} {quoted}\n"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"{function} failed: {result.stderr}"
    return [line for line in result.stdout.splitlines() if line.strip()]


def rollback_tag(new_sha: str, running_sha: str, *available: str) -> str:
    """The chosen tag, or "" when the function declines to choose one."""
    lines = call("select_rollback_tag", new_sha, running_sha, *available)
    assert len(lines) <= 1, f"expected at most one tag, got {lines}"
    return lines[0] if lines else ""


def pruned(keep: int, protected: str, *available: str) -> list[str]:
    return call("tags_to_prune", str(keep), protected, *available)


def ordered(pairs: str, stamps: str) -> list[str]:
    return call("order_tags_by_stamp", pairs, stamps)


# --- Build ordering --------------------------------------------------------
#
# Both inputs are real docker output shapes. The cases below are the two ways
# the obvious implementations failed when this ran against a live daemon, plus
# the ties and gaps that follow from them.


def test_tags_come_back_newest_first() -> None:
    pairs = "new sha256:aaa\nold sha256:bbb\nmid sha256:ccc"
    stamps = "sha256:aaa 300\nsha256:bbb 100\nsha256:ccc 200"
    assert ordered(pairs, stamps) == ["new", "mid", "old"]


def test_the_stamp_is_joined_by_id_not_by_position() -> None:
    """The bug that made this a tested function.

    `docker image inspect` does not emit one line per argument in argument
    order. Measured against Engine 29.5.2: seven images in, three lines out,
    and the third line belonged to the fourth argument. Here the stamp lines
    are deliberately in a different order from the tags, and a positional
    implementation would report `first` as the newest.
    """
    pairs = "first sha256:aaa\nsecond sha256:bbb\nthird sha256:ccc"
    stamps = "sha256:ccc 900\nsha256:aaa 100\nsha256:bbb 500"
    assert ordered(pairs, stamps) == ["third", "second", "first"]


def test_an_image_with_no_stamp_sorts_last() -> None:
    """Images built before the label existed. They are older by definition, and
    `docker image inspect` emits no line at all for them."""
    pairs = "labelled sha256:aaa\nlegacy sha256:bbb"
    stamps = "sha256:aaa 100"
    assert ordered(pairs, stamps) == ["labelled", "legacy"]


def test_a_non_numeric_stamp_does_not_break_the_sort() -> None:
    """`<no value>` is what a Go template can yield for a missing key, and it
    must not end up compared numerically against a real timestamp."""
    pairs = "good sha256:aaa\nodd sha256:bbb"
    stamps = "sha256:aaa 100\nsha256:bbb <no value>"
    assert ordered(pairs, stamps) == ["good", "odd"]


def test_latest_is_not_a_separate_image() -> None:
    """`latest` is an alias for whichever sha is live, not a sixth image, so it
    must never occupy a slot in the prune budget or be offered as a rollback."""
    pairs = "latest sha256:aaa\nabc123 sha256:aaa\nold sha256:bbb"
    stamps = "sha256:aaa 200\nsha256:bbb 100"
    assert ordered(pairs, stamps) == ["abc123", "old"]


def test_dangling_images_are_ignored() -> None:
    pairs = "<none> sha256:aaa\nabc123 sha256:bbb"
    stamps = "sha256:aaa 200\nsha256:bbb 100"
    assert ordered(pairs, stamps) == ["abc123"]


def test_no_images_is_not_an_error() -> None:
    """A first-ever deploy. The caller must get an empty list, not a failure."""
    assert ordered("", "") == []


def test_identical_stamps_still_return_every_tag() -> None:
    """Ties are possible and must not drop anything -- which is the state
    `docker image ls` reports for *every* image once the layer cache is warm."""
    pairs = "a sha256:aaa\nb sha256:bbb\nc sha256:ccc"
    stamps = "sha256:aaa 100\nsha256:bbb 100\nsha256:ccc 100"
    assert sorted(ordered(pairs, stamps)) == ["a", "b", "c"]


# --- Rollback selection ----------------------------------------------------


def test_the_running_build_is_the_rollback_target() -> None:
    """The normal case: go back to what was serving a moment ago."""
    assert rollback_tag("new", "old", "new", "old", "older") == "old"


def test_the_running_build_wins_over_a_newer_image() -> None:
    """The point of reading the sha from /api/healthz instead of docker images.

    An image can sit on the box newer than the one actually serving -- a deploy
    that failed and rolled back leaves exactly that. Returning to "the newest
    image" would then return to the build already known to be broken.
    """
    assert rollback_tag("new", "old", "new", "broken", "old") == "old"


def test_the_newest_image_is_used_when_nothing_was_serving() -> None:
    """The box was down before the deploy, so there is no running sha to trust."""
    assert rollback_tag("new", "", "new", "previous", "older") == "previous"


def test_a_pruned_running_image_falls_back_to_the_newest_available() -> None:
    """Retagging `latest` to a sha that is no longer on the box takes the app
    down rather than back, so a running sha without an image is not a target."""
    assert rollback_tag("new", "vanished", "new", "previous") == "previous"


def test_the_new_sha_is_never_the_rollback_target() -> None:
    """Rolling back to the build that just failed is not a rollback."""
    assert rollback_tag("new", "new", "new") == ""


def test_a_same_sha_redeploy_still_finds_the_previous_build() -> None:
    """Deploying the sha that is already running is explicitly allowed (#29's
    idempotence criterion), and it must not lose its way back."""
    assert rollback_tag("same", "same", "same", "previous") == "previous"


def test_a_first_ever_deploy_has_no_rollback_target() -> None:
    """Nothing on the box, nothing serving. Saying so beats inventing a tag."""
    assert rollback_tag("new", "") == ""


def test_declining_to_choose_does_not_abort_the_caller() -> None:
    """`tag="$(select_rollback_tag ...)"` must not kill a script under `set -e`.

    If it did, the failure mode would be a deploy script that dies instead of
    reporting that it cannot roll back -- losing the one piece of information
    the operator needs at that moment.
    """
    script = (
        "set -Eeuo pipefail\n"
        f". {shlex.quote(str(DEPLOY_LIB))}\n"
        'tag="$(select_rollback_tag new "" )"\n'
        'printf "survived:%s\\n" "$tag"\n'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "survived:"


# --- Tag pruning -----------------------------------------------------------


def test_nothing_is_pruned_under_the_budget() -> None:
    assert pruned(5, "a b", "a", "b", "c") == []


def test_nothing_is_pruned_at_exactly_the_budget() -> None:
    assert pruned(5, "a b", "a", "b", "c", "d", "e") == []


def test_the_oldest_images_are_pruned_over_the_budget() -> None:
    """Six images, keep five: the oldest goes."""
    assert pruned(5, "a b", "a", "b", "c", "d", "e", "f") == ["f"]


def test_pruning_leaves_no_more_than_the_budget() -> None:
    """#29: image tags never exceed 5 on the Pi."""
    available = [f"sha{n:02d}" for n in range(20)]
    doomed = pruned(5, "sha00 sha01", *available)
    assert len(available) - len(doomed) == 5


def test_the_rollback_target_survives_however_old_it_is() -> None:
    """The failure this function exists to avoid.

    A protected tag far down the age list is not a hypothetical: it is a box
    that has been deployed to several times since the last build anyone
    considers good. Deleting it removes the only way back.
    """
    available = ["new", "b", "c", "d", "e", "f", "ancient"]
    doomed = pruned(5, "new ancient", *available)
    assert "ancient" not in doomed
    assert "new" not in doomed


def test_protecting_an_old_tag_still_respects_the_budget() -> None:
    """Rescuing a protected tag must not quietly push the total to keep+1.

    Reserved slots are allocated before the newest-first fill, so protecting
    something old costs a younger image its place rather than costing the
    criterion its meaning.
    """
    available = ["new", "b", "c", "d", "e", "f", "ancient"]
    doomed = pruned(5, "new ancient", *available)
    assert len(available) - len(doomed) == 5
    # The newest four survive alongside `ancient`; the fifth-newest is what pays.
    assert doomed == ["e", "f"]


def test_a_protected_tag_that_is_not_on_the_box_costs_nothing() -> None:
    """A first deploy protects a sha that has not been loaded yet. It must not
    reserve a slot that no image occupies."""
    available = ["a", "b", "c", "d", "e", "f"]
    doomed = pruned(5, "not-here a", *available)
    assert len(available) - len(doomed) == 5
    assert doomed == ["f"]


def test_an_empty_tag_list_prunes_nothing() -> None:
    assert pruned(5, "new") == []


def test_a_sha_that_prefixes_another_is_not_confused() -> None:
    """`git rev-parse --short` makes this perfectly possible, and a substring
    membership test would silently protect or delete the wrong image."""
    available = ["abc", "abc123", "b", "c", "d", "e"]
    doomed = pruned(5, "abc", *available)
    assert "abc" not in doomed
    assert doomed == ["e"]


@pytest.mark.parametrize("keep", [1, 2, 3, 5, 10])
def test_the_budget_is_honoured_for_any_keep(keep: int) -> None:
    available = [f"sha{n:02d}" for n in range(12)]
    doomed = pruned(keep, "sha00", *available)
    assert len(available) - len(doomed) == keep
