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
import socket
import subprocess
from pathlib import Path

import pytest

#: tests/deploy/test_bootstrap_dry_run.py -> ../..
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap-pi.sh"
EXAMPLE_ENV = REPO_ROOT / "deploy" / "darts.env.example"
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.yaml"
SMB_CONF = REPO_ROOT / "deploy" / "smb-darts.conf"
AVAHI_SERVICE = REPO_ROOT / "deploy" / "avahi-darts.service"

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

    def run(*args: str, **overrides: str) -> DryRun:
        env = {
            **os.environ,
            "BOOTSTRAP_STATE_DIR": str(tmp_path / "var/lib/darts"),
            "BOOTSTRAP_SHARE_DIR": str(tmp_path / "srv/darts-share"),
            "BOOTSTRAP_ETC_DIR": str(tmp_path / "etc/darts"),
            "BOOTSTRAP_SAMBA_DIR": str(tmp_path / "etc/samba"),
            "BOOTSTRAP_AVAHI_SERVICES_DIR": str(tmp_path / "etc/avahi/services"),
            "BOOTSTRAP_SYSTEMD_DIR": str(tmp_path / "etc/systemd/system"),
            **overrides,
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


# --- the snapshot share (#30) -----------------------------------------------


def test_dry_run_plans_samba_and_avahi(dry_run) -> None:
    """Planned, or already satisfied: same host-dependence as Docker above."""
    out = dry_run("--dry-run").result.stdout
    for package in ("samba", "avahi-daemon"):
        assert f"{package} is installed" in out or ("apt-get install" in out and package in out), (
            f"{package} is neither installed nor planned"
        )


def test_dry_run_installs_the_committed_smb_conf_whole_and_checks_it(dry_run) -> None:
    """The whole file, not an include: Bookworm's Samba has no smb.conf.d.

    And `testparm` over the installed copy, because a broken smb.conf does not
    stop smbd -- it just serves no share, which looks like the network.
    """
    run = dry_run("--dry-run")
    target = str(run.root / "etc/samba/smb.conf")
    assert run.planned("install", "0644", str(SMB_CONF), target)
    assert run.planned("check_samba_config", target)
    # restart, not reload: a reload does not rebind sockets, so the stock
    # config's port 139 survived a reload on the stand-in.
    assert run.planned("systemctl restart smbd")


def test_debians_smb_conf_is_kept_once_before_the_first_replacement(dry_run, tmp_path) -> None:
    samba = tmp_path / "etc/samba"
    samba.mkdir(parents=True)
    (samba / "smb.conf").write_text("[homes]\n")

    run = dry_run("--dry-run")
    assert run.planned("cp", str(samba / "smb.conf"), str(samba / "smb.conf.debian-orig"))


def test_the_kept_original_is_never_overwritten(dry_run, tmp_path) -> None:
    """By the second run smb.conf is ours; copying it over the original loses Debian's."""
    samba = tmp_path / "etc/samba"
    samba.mkdir(parents=True)
    (samba / "smb.conf").write_text("ours\n")
    (samba / "smb.conf.debian-orig").write_text("debian\n")

    run = dry_run("--dry-run")
    assert not run.planned("cp", "smb.conf.debian-orig")
    assert run.planned("skip", "smb.conf.debian-orig")
    assert run.planned("install", str(SMB_CONF), str(samba / "smb.conf")), "still replaced"


def test_dry_run_advertises_the_share_over_avahi(dry_run) -> None:
    run = dry_run("--dry-run")
    target = str(run.root / "etc/avahi/services/darts.service")
    assert run.planned("install", str(AVAHI_SERVICE), target)
    out = run.result.stdout
    assert "systemctl enable avahi-daemon" in out or "avahi-daemon is enabled" in out


def test_dry_run_installs_and_enables_the_snapshot_timer(dry_run) -> None:
    run = dry_run("--dry-run")
    for unit in ("darts-snapshot.service", "darts-snapshot.timer"):
        target = str(run.root / "etc/systemd/system" / unit)
        assert run.planned("install", str(REPO_ROOT / "deploy" / unit), target), unit
    assert run.planned("systemctl daemon-reload")
    out = run.result.stdout
    assert "systemctl enable darts-snapshot.timer" in out or "timer is enabled" in out
    assert run.planned("systemctl restart darts-snapshot.timer")


def test_the_timer_is_installed_after_its_units_are_reloaded(dry_run) -> None:
    """Enabling a unit systemd has not re-read enables the old one, or nothing."""
    plan = dry_run("--dry-run").plan
    reload = next(i for i, line in enumerate(plan) if "daemon-reload" in line)
    installs = [i for i, line in enumerate(plan) if "install" in line and "darts-snapshot." in line]
    starts = [i for i, line in enumerate(plan) if "restart darts-snapshot.timer" in line]
    assert max(installs) < reload < min(starts)


def test_a_wrong_hostname_is_warned_about_not_changed(dry_run) -> None:
    run = dry_run("--dry-run", BOOTSTRAP_HOSTNAME="definitely-not-this-host")
    assert "not 'definitely-not-this-host'" in run.result.stderr
    # Matched on the command, not anywhere in the line: tmp_path contains this
    # test's name, which contains "hostname".
    commands = [line.split()[1] for line in run.plan if line.startswith("plan: ")]
    assert not {"hostname", "hostnamectl", "raspi-config"} & set(commands)


def test_the_right_hostname_is_not_warned_about(dry_run) -> None:
    current = socket.gethostname().split(".")[0]
    run = dry_run("--dry-run", BOOTSTRAP_HOSTNAME=current)
    assert "hostname is" not in run.result.stderr
    assert run.planned("skip", f"hostname is {current}")


def test_nothing_is_ever_shared_out_of_the_state_directory(dry_run) -> None:
    """The live database must never be on the share; see deploy/smb-darts.conf."""
    run = dry_run("--dry-run")
    samba_steps = [line for line in run.plan if "samba" in line or "smb" in line]
    assert samba_steps
    assert not any(str(run.root / "var/lib/darts") in line for line in samba_steps)


# --- HTTPS (#71) --------------------------------------------------------------

CADDYFILE = REPO_ROOT / "deploy" / "Caddyfile"


@pytest.fixture
def tls_source(tmp_path: Path) -> Path:
    """What `scripts/make-cert.sh` leaves on the Mac, as copied to the Pi.

    Placeholders: bootstrap checks the files are there and leaves reading them to
    `caddy validate`, which a dry run never calls. Outside the redirected root so
    the creates-nothing assertion is not tripped by the fixture itself.
    """
    source = tmp_path.parent / f"{tmp_path.name}-tls"
    source.mkdir()
    (source / "darts-leaf.crt").write_text("certificate\n")
    (source / "darts-leaf.key").write_text("key\n")
    return source


@pytest.fixture
def https_run(dry_run, tmp_path: Path):
    def run(*args: str, **overrides: str) -> DryRun:
        return dry_run(*args, BOOTSTRAP_CADDY_DIR=str(tmp_path / "etc/caddy"), **overrides)

    return run


def test_dry_run_with_a_certificate_creates_nothing(https_run, tls_source: Path) -> None:
    run = https_run("--dry-run", "--tls-from", str(tls_source))
    assert run.result.returncode == 0, run.result.stderr
    assert list(run.root.rglob("*")) == []


def test_caddy_comes_from_debians_archive(https_run, tls_source: Path) -> None:
    """Planned or already there, like Samba. Not a container: deploy/Caddyfile says why."""
    out = https_run("--dry-run", "--tls-from", str(tls_source)).result.stdout
    assert "caddy is installed" in out or ("apt-get install" in out and "caddy" in out)


def test_the_certificate_is_installed_with_a_key_only_caddy_can_read(
    https_run, tls_source: Path
) -> None:
    run = https_run("--dry-run", "--tls-from", str(tls_source))
    tls = run.root / "etc/darts/tls"
    assert run.planned("install -d -m 0750 -o root -g caddy", str(tls))
    assert run.planned("-m 0644", str(tls_source / "darts-leaf.crt"), str(tls / "cert.pem"))
    assert run.planned(
        "-m 0640 -o root -g caddy", str(tls_source / "darts-leaf.key"), str(tls / "key.pem")
    )


def test_the_installed_paths_are_the_ones_the_caddyfile_serves() -> None:
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    assert "tls /etc/darts/tls/cert.pem /etc/darts/tls/key.pem" in caddyfile


def test_without_tls_from_an_installed_certificate_is_kept(https_run, tmp_path: Path) -> None:
    """The operator's file, like darts.env. Only --tls-from replaces it (a renewal)."""
    tls = tmp_path / "etc/darts/tls"
    tls.mkdir(parents=True)
    (tls / "cert.pem").write_text("installed\n")
    (tls / "key.pem").write_text("installed\n")

    run = https_run("--dry-run")
    assert run.result.returncode == 0, run.result.stderr
    assert run.planned("skip", "certificate in", str(tls))
    assert not any("cert.pem" in line for line in run.plan if line.startswith("plan: "))
    assert "no HTTPS certificate" not in run.result.stderr


def test_a_plan_without_any_certificate_warns(https_run) -> None:
    """A real run refuses before changing anything; a plan says what is missing."""
    run = https_run("--dry-run")
    assert run.result.returncode == 0
    assert "no HTTPS certificate" in run.result.stderr
    assert "make-cert.sh" in run.result.stderr


def test_tls_from_a_directory_without_the_files_is_refused(https_run, tmp_path: Path) -> None:
    empty = tmp_path.parent / f"{tmp_path.name}-empty"
    empty.mkdir()
    run = https_run("--dry-run", "--tls-from", str(empty))
    assert run.result.returncode != 0
    assert "no darts-leaf.crt" in run.result.stderr
    assert run.plan == [], "refused before planning anything"


def test_tls_from_needs_a_directory(https_run) -> None:
    run = https_run("--dry-run", "--tls-from")
    assert run.result.returncode != 0
    assert "--tls-from needs a directory" in run.result.stderr


def test_the_caddyfile_is_validated_before_caddy_restarts(https_run, tls_source: Path) -> None:
    """A broken Caddyfile or certificate fails bootstrap, not the next request."""
    run = https_run("--dry-run", "--tls-from", str(tls_source))
    target = str(run.root / "etc/caddy/Caddyfile")
    plan = run.plan
    install = next(i for i, line in enumerate(plan) if "install" in line and target in line)
    check = next(i for i, line in enumerate(plan) if "check_caddy_config" in line)
    cert = next(i for i, line in enumerate(plan) if "cert.pem" in line)
    restart = next(i for i, line in enumerate(plan) if "systemctl restart caddy" in line)
    assert str(CADDYFILE) in plan[install]
    assert cert < check, "validating loads the certificate, so it must be in place first"
    assert install < check < restart
    out = run.result.stdout
    assert "systemctl enable caddy" in out or "caddy is enabled" in out


def test_the_url_it_prints_is_https_by_name(https_run, tls_source: Path) -> None:
    """The certificate covers darts.local and no address, so an IP URL would be a lie."""
    err = https_run("--dry-run", "--tls-from", str(tls_source)).result.stderr
    assert "https://darts.local/" in err
    assert "http://" not in err


def test_help_documents_tls_from(dry_run) -> None:
    assert "--tls-from" in dry_run("--help").result.stdout
