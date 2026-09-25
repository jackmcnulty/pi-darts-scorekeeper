"""Assertions over what `deploy.sh --dry-run` and `rollback.sh --dry-run` plan.

The sibling of tests/deploy/test_bootstrap_dry_run.py, and here for the same
reason: the thing worth testing is the process, and pytest already knows how to
run processes and report on them.

What this module can and cannot reach. A deploy needs a target, and neither CI
nor a developer's Mac has one, so `--dry-run` has to plan a deploy without
touching anything -- no ssh, no docker build, no test run. That makes three
properties testable here, and they are the three that do not need hardware:

*The plan is in the right order.* `stop` before the backup, the backup before
the migration, the migration before the container comes up. Those orderings are
the difference between a backup taken against a quiesced database and one taken
against a live writer, and nothing about a successful deploy would reveal that
they had been shuffled.

*Every flag is documented.* #29 makes `--help` an acceptance criterion. The flag
list is extracted from each script's own argument parser rather than hardcoded
here, so a flag added later without a matching `--help` entry fails this test
instead of quietly shipping undocumented.

*Database work happens inside a container.* #29 requires the Pi to have no
system Python dependency on the app, and the plan is where that is decided: a
`darts-migrate` invoked as a bare command rather than as a container entrypoint
would satisfy every other criterion and still need Python on the host.

The rollback path is asserted as a *plan* only. That it actually fires, and does
so inside 40 seconds, cannot be shown from here -- see docs/deploy.md.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

#: tests/deploy/test_deploy_dry_run.py -> ../..
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
DEPLOY = SCRIPTS / "deploy.sh"
ROLLBACK = SCRIPTS / "rollback.sh"
HEALTHCHECK = SCRIPTS / "healthcheck.sh"

#: Deliberately unresolvable: a dry run must not reach for a target at all.
UNREACHABLE = "nobody@deploy-target.invalid"


class Plan:
    """One invocation: the plan on stdout, the commentary on stderr."""

    def __init__(self, result: subprocess.CompletedProcess[str]) -> None:
        self.result = result

    @property
    def lines(self) -> list[str]:
        return [line for line in self.result.stdout.splitlines() if line.strip()]

    def index(self, *fragments: str) -> int:
        """Position of the first planned action containing all of `fragments`.

        Fragment matching rather than whole-line equality, as in the bootstrap
        dry-run tests: pinning exact command strings would make this a
        change-detector for argument order rather than a test of intent.
        """
        for position, line in enumerate(self.lines):
            if all(fragment in line for fragment in fragments):
                return position
        plan = "\n".join(self.lines)
        raise AssertionError(f"no planned action matching {fragments} in:\n{plan}")

    def planned(self, *fragments: str) -> bool:
        return any(all(f in line for f in fragments) for line in self.lines)


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A throwaway git repository with a clean worktree and one commit.

    deploy.sh inspects `git status` to refuse a dirty deploy, so a test of that
    refusal needs a worktree whose state it controls. Running against this
    checkout instead would make the result depend on whether the developer
    happened to have edits open.
    """
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    # Contents are never read in a dry run; their presence is checked.
    (repo / "deploy" / "Dockerfile").write_text("FROM scratch\n")
    (repo / "deploy" / "compose.yaml").write_text("services: {}\n")

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    for args in (
        ["init", "--quiet"],
        ["add", "-A"],
        ["commit", "--quiet", "-m", "fixture"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, timeout=60)
    return repo


@pytest.fixture
def deploy(fake_repo: Path):
    def run(*args: str) -> Plan:
        result = subprocess.run(
            [str(DEPLOY), "--host", UNREACHABLE, *args],
            capture_output=True,
            text=True,
            env={**os.environ, "DEPLOY_REPO_ROOT": str(fake_repo)},
            timeout=120,
        )
        return Plan(result)

    return run


@pytest.fixture
def plan(deploy):
    """The default dry run: clean worktree, no target, nothing executed."""
    return deploy("--dry-run", "--skip-tests")


# --- The mode does what the mode promises ----------------------------------


def test_dry_run_succeeds_without_a_target(plan: Plan) -> None:
    """The whole reason the mode exists: plan a deploy from a machine that
    cannot reach the Pi, which is every machine until someone is at home."""
    assert plan.result.returncode == 0, plan.result.stderr


def test_dry_run_writes_only_plan_lines_to_stdout(plan: Plan) -> None:
    """stdout is machine-readable; prose belongs on stderr (scripts/lib.sh)."""
    unexpected = [line for line in plan.lines if not line.startswith(("plan: ", "skip: "))]
    assert unexpected == [], f"non-plan output on stdout: {unexpected}"


def test_dry_run_builds_nothing(deploy, fake_repo: Path) -> None:
    """No image, and no stray files in the repository it was pointed at."""
    before = sorted(p.relative_to(fake_repo).as_posix() for p in fake_repo.rglob("*"))
    deploy("--dry-run", "--skip-tests")
    after = sorted(p.relative_to(fake_repo).as_posix() for p in fake_repo.rglob("*"))
    assert before == after


def test_dry_run_is_repeatable(deploy) -> None:
    """A plan that varies run to run cannot be reviewed."""
    assert deploy("--dry-run", "--skip-tests").lines == deploy("--dry-run", "--skip-tests").lines


# --- The plan is in the right order ----------------------------------------


def test_the_image_is_stamped_with_the_commit(plan: Plan) -> None:
    """GIT_SHA is what /api/healthz reports, and therefore what the health poll
    and the rollback selection both key off."""
    assert plan.planned("docker build", "--build-arg", "GIT_SHA=")


def test_the_container_is_stopped_before_the_backup(plan: Plan) -> None:
    """SIGTERM first, so the WAL checkpoint in the lifespan hook has run and the
    backup is taken with no writer attached (docs/durability.md)."""
    assert plan.index("compose", "stop") < plan.index("darts-backup")


def test_the_backup_happens_before_the_migration(plan: Plan) -> None:
    """#29: a backup exists from immediately before every deploy. The migration
    is the only step that changes data, so the backup has to precede it -- it is
    the only way back from a bad migration, which an image rollback cannot fix."""
    assert plan.index("darts-backup") < plan.index("darts-migrate")


def test_the_migration_happens_before_the_new_container_starts(plan: Plan) -> None:
    """The new code must never open a database it has not migrated."""
    assert plan.index("darts-migrate") < plan.index("compose", "up")


def test_the_health_poll_follows_the_restart(plan: Plan) -> None:
    assert plan.index("compose", "up") < plan.index("poll /api/healthz")


def test_pruning_happens_last(plan: Plan) -> None:
    """Deleting images before the new build is known good would remove the
    rollback target while it is still needed."""
    assert plan.index("poll /api/healthz") < plan.index("delete the rest")


# --- No system Python on the target ----------------------------------------


@pytest.mark.parametrize("tool", ["darts-backup", "darts-migrate"])
def test_database_commands_run_inside_a_container(plan: Plan, tool: str) -> None:
    """#29: the Pi has no system Python dependency on the app.

    Every invocation of a darts-* tool must be a container entrypoint. A bare
    `darts-backup` in the plan would mean the Pi needs the application installed
    on the host, and would also open the database as the SSH user rather than as
    uid 1000 -- which leaves WAL sidecars the container cannot write.
    """
    invocations = [line for line in plan.lines if tool in line]
    assert invocations, f"{tool} is never invoked"
    for line in invocations:
        assert "docker run" in line, f"{tool} is not run in a container: {line}"
        assert f"--entrypoint {tool}" in line, f"{tool} is not the entrypoint: {line}"


def test_the_database_is_reached_through_the_bind_mount(plan: Plan) -> None:
    """The container has no database of its own; it gets the host's."""
    assert plan.planned("docker run", "-v /var/lib/darts:/var/lib/darts")


@pytest.mark.parametrize("interpreter", ["python", "python3", "node", "npm", "uv"])
def test_no_plan_step_runs_an_interpreter_on_the_target(plan: Plan, interpreter: str) -> None:
    """The other half of "no Node and no system Python on the Pi".

    Checking that the tools are absent from a host only proves it for that host.
    What can be asserted anywhere is the deploy's own side of the bargain: no
    step of it invokes an interpreter on the target. The only Python that runs
    there is inside the image -- the app, and the HEALTHCHECK's urllib call.

    `uv` is included because preflight does use it, on *this* machine, and the
    distinction between the two sides of the ssh matters.
    """
    remote_steps = [line for line in plan.lines if "on_target" in line or "ssh " in line]
    assert remote_steps, "expected some remote steps in the plan"
    offenders = [line for line in remote_steps if re.search(rf"\b{interpreter}\b", line)]
    assert offenders == [], f"{interpreter} is invoked on the target: {offenders}"


# --- The dirty-worktree criterion ------------------------------------------


def test_a_dirty_worktree_is_refused(deploy, fake_repo: Path) -> None:
    """#29: deploying with a dirty worktree fails unless --allow-dirty.

    The image is stamped with HEAD's sha, so deploying uncommitted work puts a
    build on the Pi claiming to be a commit it is not -- and since rollback
    selection keys off the sha the box reports, two different images answering
    with one sha is precisely the state that makes rollback pick wrong.
    """
    (fake_repo / "dirty.txt").write_text("uncommitted\n")
    result = deploy("--dry-run", "--skip-tests").result
    assert result.returncode != 0
    assert "uncommitted changes" in result.stderr


def test_allow_dirty_overrides_the_refusal(deploy, fake_repo: Path) -> None:
    (fake_repo / "dirty.txt").write_text("uncommitted\n")
    result = deploy("--dry-run", "--skip-tests", "--allow-dirty").result
    assert result.returncode == 0, result.stderr
    assert "dirty" in result.stderr


def test_an_untracked_file_counts_as_dirty(deploy, fake_repo: Path) -> None:
    """`git status --porcelain` reports untracked files, and it should: a new
    module that was never added is exactly the kind of thing whose absence from
    the image is discovered on the Pi."""
    (fake_repo / "deploy" / "extra.yaml").write_text("new\n")
    assert deploy("--dry-run", "--skip-tests").result.returncode != 0


# --- --help documents every flag -------------------------------------------


def flags_of(script: Path) -> set[str]:
    """Every flag the script's argument parser accepts.

    Read out of the `case` arms rather than listed here, so that a flag added
    without a matching --help entry fails the test below instead of shipping
    undocumented.
    """
    arms = re.findall(r"^\s{4}(-[^)]*?)\)$", script.read_text(), re.MULTILINE)
    found = set()
    for arm in arms:
        for token in arm.split("|"):
            token = token.strip()
            if token.startswith("-"):
                found.add(token)
    return found


@pytest.mark.parametrize("script", [DEPLOY, ROLLBACK, HEALTHCHECK], ids=lambda p: p.name)
def test_help_documents_every_flag(script: Path) -> None:
    """#29: `deploy.sh --help` documents every flag."""
    result = subprocess.run([str(script), "--help"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr

    flags = flags_of(script)
    assert flags, f"no flags found in {script.name}; the parser shape must have changed"
    undocumented = sorted(flag for flag in flags if flag not in result.stdout)
    assert undocumented == [], f"{script.name} --help omits {undocumented}"


@pytest.mark.parametrize("script", [DEPLOY, ROLLBACK, HEALTHCHECK], ids=lambda p: p.name)
def test_unknown_argument_is_rejected(script: Path) -> None:
    """A typo'd flag must not be silently ignored by a script that restarts the
    only copy of the application."""
    result = subprocess.run(
        [str(script), "--host", UNREACHABLE, "--destroy-everything"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "unknown argument" in result.stderr


@pytest.mark.parametrize("script", [DEPLOY, ROLLBACK, HEALTHCHECK], ids=lambda p: p.name)
def test_host_is_required(script: Path) -> None:
    """Every one of these acts on a remote machine. None of them may guess."""
    result = subprocess.run([str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "--host is required" in result.stderr


# --- rollback.sh -----------------------------------------------------------


def rollback_plan(*args: str) -> Plan:
    result = subprocess.run(
        [str(ROLLBACK), "--host", UNREACHABLE, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return Plan(result)


def test_rollback_dry_run_plans_a_retag_and_a_restart() -> None:
    """The same two primitives the deploy uses, pointed at a different sha."""
    plan = rollback_plan("--dry-run")
    assert plan.result.returncode == 0, plan.result.stderr
    assert plan.planned("docker tag", "darts:latest")
    assert plan.index("docker tag") < plan.index("compose", "up")


def test_rollback_dry_run_plans_a_health_check() -> None:
    """A rollback that is not verified is a guess."""
    assert rollback_plan("--dry-run").planned("poll /api/healthz")


def test_rollback_touches_no_database_command() -> None:
    """Rolling the image back does not un-migrate a schema, and restoring a
    backup automatically would turn a reversible action into a destructive one.
    """
    plan = rollback_plan("--dry-run")
    for tool in ("darts-backup", "darts-migrate", "darts-restore"):
        assert not plan.planned(tool), f"rollback.sh must not invoke {tool}"


def test_rollback_dry_run_writes_only_plan_lines() -> None:
    plan = rollback_plan("--dry-run")
    unexpected = [line for line in plan.lines if not line.startswith(("plan: ", "skip: "))]
    assert unexpected == []
