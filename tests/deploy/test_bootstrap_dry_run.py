"""Assertions over what `bootstrap-pi.sh --dry-run` plans to do.

This is the only test in the suite whose subject is a shell script, and it is
here rather than in a bash test framework for the reason
`tests/api/test_lifespan.py` runs a real uvicorn: the thing worth testing is
the process, and pytest already knows how to run processes and report on them.

Two different properties are under test and they matter for different reasons.

*The plan is right* -- it creates the directories deploy/compose.yaml binds,
owned by the uid the container runs as. A wrong plan produces a Pi where the
app starts and then cannot write, which surfaces as a degraded health check
rather than as anything resembling a permissions error.

*The plan is only a plan* -- nothing was created. That assertion is not
ceremony. CI runs as an ordinary user who can perfectly well `mkdir -p` the
paths this script names, so a mutation that escaped the `run` helper in
scripts/lib.sh would silently succeed on the runner and only be discovered on
someone's Pi.
"""

import os
import subprocess
from pathlib import Path

import pytest

#: tests/deploy/test_bootstrap_dry_run.py -> ../..
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap-pi.sh"
EXAMPLE_ENV = REPO_ROOT / "deploy" / "darts.env.example"
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.yaml"

#: The uid/gid baked into deploy/Dockerfile, and `pi` on Raspberry Pi OS.
CONTAINER_OWNER = "1000:1000"


class DryRun:
    """One invocation: the plan on stdout, the commentary on stderr."""

    def __init__(self, result: subprocess.CompletedProcess[str], root: Path) -> None:
        self.result = result
        self.root = root

    @property
    def plan(self) -> list[str]:
        return [line for line in self.result.stdout.splitlines() if line.strip()]

    def planned(self, *fragments: str) -> bool:
        """Is there a single planned action containing all of `fragments`?

        Fragment matching rather than whole-line equality: the plan embeds
        absolute temporary paths, and pinning exact command strings would make
        this a change-detector for argument order rather than a test of intent.
        """
        return any(all(f in line for f in fragments) for line in self.plan)


@pytest.fixture
def dry_run(tmp_path: Path):
    """Run the script with every target path redirected into `tmp_path`.

    The BOOTSTRAP_* overrides exist for exactly this. Pointing the script at a
    real /var/lib/darts to test it would be the kind of test that passes once
    and then owns the developer's machine.
    """

    def run(*args: str) -> DryRun:
        env = {
            **os.environ,
            "BOOTSTRAP_STATE_DIR": str(tmp_path / "var/lib/darts"),
            "BOOTSTRAP_SHARE_DIR": str(tmp_path / "srv/darts-share"),
            "BOOTSTRAP_ETC_DIR": str(tmp_path / "etc/darts"),
        }
        result = subprocess.run(
            [str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        return DryRun(result, tmp_path)

    return run


def test_dry_run_succeeds(dry_run) -> None:
    run = dry_run("--dry-run")
    assert run.result.returncode == 0, run.result.stderr


def test_dry_run_creates_nothing(dry_run) -> None:
    """The whole point of the mode, and the reason `run` is a chokepoint."""
    run = dry_run("--dry-run")
    strays = sorted(p.relative_to(run.root).as_posix() for p in run.root.rglob("*"))
    assert strays == [], f"--dry-run touched the filesystem: {strays}"


def test_dry_run_needs_no_privileges(dry_run) -> None:
    """Root is required for a real run and must not be required for a plan.

    An operator inspecting what this will do to their Pi should not have to
    hand it root to find out.
    """
    run = dry_run("--dry-run")
    assert run.result.returncode == 0
    assert "must run as root" not in run.result.stderr


def test_dry_run_plans_the_bind_mount_directories(dry_run) -> None:
    """The two mounts in deploy/compose.yaml, plus the backup directory.

    backups/ is created explicitly because nothing else will: the server
    creates the *database's* parent on boot, but `darts-backup` has no
    equivalent fallback.
    """
    run = dry_run("--dry-run")
    for directory in ("var/lib/darts", "var/lib/darts/backups", "srv/darts-share"):
        target = str(run.root / directory)
        assert run.planned("mkdir", target), f"no mkdir planned for {directory}"
        assert run.planned("chown", CONTAINER_OWNER, target), f"no chown planned for {directory}"


def test_dry_run_installs_the_env_file_from_the_committed_example(dry_run) -> None:
    run = dry_run("--dry-run")
    assert run.planned("cp", str(EXAMPLE_ENV), str(run.root / "etc/darts/darts.env"))


def test_dry_run_installs_the_compose_file(dry_run) -> None:
    """The file `deploy.sh` drives the container with.

    It lives on the host, installed here, so that a deploy needs no source
    checkout on the Pi and no write access to /etc -- which is what keeps a
    deploy from ever needing sudo, and therefore from leaving WAL sidecars the
    container cannot write.
    """
    run = dry_run("--dry-run")
    assert run.planned("cp", str(COMPOSE_FILE), str(run.root / "etc/darts/compose.yaml"))


def test_the_compose_file_is_replaced_on_every_run(dry_run, tmp_path: Path) -> None:
    """Unlike `darts.env`, and the difference is the point.

    `darts.env` is the operator's file, so a re-run must not clobber their
    edits. `compose.yaml` is a repository artefact and the only correct copy is
    the current one -- so re-running bootstrap is how a changed compose.yaml
    reaches an already-bootstrapped Pi. Same reasoning as the `chown`s.
    """
    existing = tmp_path / "etc/darts"
    existing.mkdir(parents=True)
    (existing / "compose.yaml").write_text("stale\n")
    (existing / "darts.env").write_text("edited by hand\n")

    run = dry_run("--dry-run")
    assert run.planned("cp", str(COMPOSE_FILE), str(run.root / "etc/darts/compose.yaml"))
    assert run.planned("skip", "darts.env"), "the env file must still be left alone"


def test_dry_run_plans_docker_engine_and_the_compose_plugin(dry_run) -> None:
    """Compose v2 is a plugin, and `stop_grace_period` is why it must be there.

    Asserted as "planned or already satisfied" because this is the one step
    whose plan depends on the host: a developer machine and a CI runner both
    already have Docker, and a fresh Bookworm image does not.
    """
    run = dry_run("--dry-run")
    combined = run.result.stdout
    assert "docker-compose-plugin" in combined or "Compose plugin are installed" in combined


def test_dry_run_plans_docker_to_start_at_boot(dry_run) -> None:
    """`restart: unless-stopped` only survives a power cut if docker.service does.

    This is the load-bearing half of "a full power cycle brings the app back";
    without it there is no systemd unit anywhere in the deployment to start
    anything.
    """
    out = dry_run("--dry-run").result.stdout
    assert "systemctl enable docker" in out or "is enabled at boot" in out


def test_dry_run_writes_only_plan_lines_to_stdout(dry_run) -> None:
    """stdout is a machine-readable channel; prose belongs on stderr.

    This is what lets `bootstrap-pi.sh --dry-run >plan.txt` be diffed between
    revisions of the script without filtering.
    """
    run = dry_run("--dry-run")
    assert run.plan, "expected a plan on stdout"
    unexpected = [line for line in run.plan if not line.startswith(("plan: ", "skip: "))]
    assert unexpected == [], f"non-plan output on stdout: {unexpected}"


def test_dry_run_is_repeatable(dry_run) -> None:
    """Same host, same plan. A plan that varies run to run cannot be reviewed."""
    assert dry_run("--dry-run").plan == dry_run("--dry-run").plan


def test_help_is_not_an_error(dry_run) -> None:
    run = dry_run("--help")
    assert run.result.returncode == 0
    assert "--dry-run" in run.result.stdout


def test_unknown_argument_is_rejected(dry_run) -> None:
    """A typo'd flag must not be silently ignored by a script that runs as root."""
    run = dry_run("--destroy-everything")
    assert run.result.returncode != 0
    assert "unknown argument" in run.result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="running as root, which is the supported case")
def test_a_real_run_refuses_without_root(dry_run) -> None:
    """Refuse up front rather than failing at the first write.

    Bailing out halfway would leave a host that is neither bootstrapped nor
    clean, which is the state the idempotence requirement exists to avoid.
    """
    run = dry_run()
    assert run.result.returncode != 0
    assert "must run as root" in run.result.stderr
    assert list(run.root.rglob("*")) == []
