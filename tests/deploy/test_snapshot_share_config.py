"""The committed share configuration says what #30 promises, and agrees with the app.

These files are read by Samba, systemd and Avahi on a host CI never touches, so
nothing else in the suite would notice if one of them drifted. The assertions
are the ones where drift is dangerous or silent: a share that became writable, a
path that pointed at the live database, a veto that stopped matching the app's
temporaries, or a unit that posts to a route or port the app no longer serves.

What they cannot establish is that Samba behaves as configured. That was checked
against a real smbd 4.17 (Bookworm's) during #30 and is recorded in
docs/deploy.md; it is re-checked on the Pi by the manual checklist there.
"""

import configparser
import re
from pathlib import Path

import pytest

from darts.api.admin import router as admin_router
from darts.db import artifact
from darts.services import snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"


def _ini(path: Path) -> configparser.ConfigParser:
    """smb.conf and systemd units are both ini-shaped, with two quirks.

    `=` is the only delimiter, because Samba keys contain colons
    (`fruit:metadata`). And option names keep their case, because systemd's are
    case-sensitive.
    """
    parser = configparser.ConfigParser(delimiters=("=",), interpolation=None, strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


@pytest.fixture(scope="module")
def smb() -> configparser.ConfigParser:
    return _ini(DEPLOY / "smb-darts.conf")


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"yes", "true", "1"}


# --- smb-darts.conf ---------------------------------------------------------


def test_it_defines_one_share_and_nothing_else(smb: configparser.ConfigParser) -> None:
    """No [homes], no [printers], no [print$] -- Debian's stock file has all three."""
    assert smb.sections() == ["global", "darts"]


def test_the_share_is_read_only_and_guest_only(smb: configparser.ConfigParser) -> None:
    share = smb["darts"]
    assert _truthy(share["read only"])
    assert _truthy(share["guest ok"])
    assert _truthy(share["guest only"]), "credentials must not map to a user who can write"
    for grants_writes in ("writable", "writeable", "write ok", "write list", "force user"):
        assert grants_writes not in share, grants_writes


def test_the_share_is_the_snapshot_directory_and_never_the_database(
    smb: configparser.ConfigParser,
) -> None:
    """/srv/darts-share is DARTS_SNAPSHOT_DIR in the env file bootstrap installs."""
    env = (DEPLOY / "darts.env.example").read_text(encoding="utf-8")
    assert "DARTS_SNAPSHOT_DIR=/srv/darts-share" in env.splitlines()
    assert smb["darts"]["path"] == "/srv/darts-share"
    assert "/var/lib/darts" not in smb["darts"]["path"]


def _compose_lines() -> list[str]:
    """compose.yaml's entries, comments stripped. Text, not YAML: PyYAML is not a
    dependency of this project, and these two assertions do not need a parser."""
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    return [line.split(" #")[0].strip() for line in text.splitlines()]


def test_the_share_directory_is_mounted_into_the_container() -> None:
    """Otherwise the app publishes into the container's own filesystem."""
    assert "- /srv/darts-share:/srv/darts-share" in _compose_lines()


def test_the_veto_hides_both_kinds_of_temporary_the_app_writes(
    smb: configparser.ConfigParser,
) -> None:
    """A half-written database or manifest must never be visible on the share.

    The prefixes are read from the code that writes them, so renaming one
    without updating the veto fails here rather than on somebody's Mac.
    """
    veto = smb["darts"]["veto files"]
    patterns = [p for p in veto.split("/") if p]
    temporaries = [
        snapshot._TEMP_PREFIX + "abc.db",
        ".darts-manifest-abc.json",  # darts.db.artifact.write_json
    ]
    for name in temporaries:
        assert any(re.fullmatch(p.replace(".", r"\.").replace("*", ".*"), name) for p in patterns)
    for published in (snapshot.SNAPSHOT_NAME, snapshot.MANIFEST_NAME):
        assert not any(
            re.fullmatch(p.replace(".", r"\.").replace("*", ".*"), published) for p in patterns
        )
    assert ".darts-manifest-" in Path(artifact.__file__).read_text(encoding="utf-8")


def test_finder_gets_the_apple_extensions(smb: configparser.ConfigParser) -> None:
    assert smb["global"]["vfs objects"].split() == ["fruit", "streams_xattr"]


def test_guests_are_mapped_and_netbios_is_off(smb: configparser.ConfigParser) -> None:
    glob = smb["global"]
    assert glob["map to guest"].lower() == "bad user"
    assert glob["guest account"] == "nobody"
    assert _truthy(glob["disable netbios"])
    assert glob["load printers"] == "no"


# --- darts-snapshot.service / .timer ----------------------------------------


def test_the_unit_posts_to_the_route_the_app_serves() -> None:
    """The URL in the unit is checked against the app's own routes, not a string."""
    unit = _ini(DEPLOY / "darts-snapshot.service")
    exec_start = unit["Service"]["ExecStart"].replace("\\\n", " ")
    [url] = re.findall(r"http://\S+", exec_start)
    assert "--request POST" in exec_start
    assert "--fail" in exec_start, "without --fail a 500 would look like success"

    match = re.fullmatch(r"http://127\.0\.0\.1:(\d+)(/\S+)", url)
    assert match, url
    port, path = match.groups()

    posts = {
        route.path  # type: ignore[attr-defined]
        for route in admin_router.routes
        if "POST" in getattr(route, "methods", set())
    }
    assert path in posts, f"{path} is not a POST route in darts.api.admin: {sorted(posts)}"
    assert f'- "{port}:8000"' in _compose_lines()


def test_the_unit_never_touches_the_database_itself() -> None:
    """The WAL-sidecar trap: nothing outside the container may open darts.db."""
    text = (DEPLOY / "darts-snapshot.service").read_text(encoding="utf-8")
    service = _ini(DEPLOY / "darts-snapshot.service")["Service"]
    assert "ExecStart" in service
    assert "/var/lib/darts" not in service["ExecStart"]
    assert "darts.db" not in service["ExecStart"]
    assert "sqlite3" not in service["ExecStart"]
    assert _truthy(service["DynamicUser"]), "runs as nobody in particular, not root"
    assert "User" not in service
    assert "ExecStart=/usr/bin/curl" in text


def test_the_timer_fires_every_five_minutes_on_a_monotonic_clock() -> None:
    timer = _ini(DEPLOY / "darts-snapshot.timer")
    assert timer["Timer"]["OnUnitActiveSec"] == "5min"
    assert "OnCalendar" not in timer["Timer"], "the Pi has no battery-backed clock"
    assert timer["Timer"]["AccuracySec"] == "1s", "the default lets each firing drift a minute"
    assert timer["Timer"]["Unit"] == "darts-snapshot.service"
    assert timer["Install"]["WantedBy"] == "timers.target"


# --- avahi-darts.service ----------------------------------------------------


def test_avahi_advertises_smb_on_the_port_samba_listens_on(
    smb: configparser.ConfigParser,
) -> None:
    text = (DEPLOY / "avahi-darts.service").read_text(encoding="utf-8")
    found = re.search(r"<type>_smb\._tcp</type>\s*<port>(\d+)</port>", text)
    assert found
    assert found.group(1) == smb["global"]["smb ports"]
