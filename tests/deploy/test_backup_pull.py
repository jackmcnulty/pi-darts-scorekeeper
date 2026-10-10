"""scripts/backup-pull.sh, its pure decisions and its refusals.

The script runs on the Mac and pulls from a Pi that CI can never reach, so the
suite splits it the way #29 split deploy.sh. The decisions -- which copies
retention deletes, which copy is newest, when a copy is stale, and what a curl
failure means -- are pure functions in scripts/backup-pull-lib.sh, called here
with synthetic inputs, including the ones a real Mac would take months to
produce. Every call is made under `set -Eeuo pipefail`, because the bugs worth
catching in bash 3.2 (which is /bin/bash on macOS, and what these run under on
a Mac) only exist under it: an empty list under `set -u`, or a function that
says "no answer" with a non-zero status and so aborts its caller.

The script itself is then run end to end with no server at all. curl reads
`file://` exactly as it reads HTTP, so BACKUP_PULL_URL can hand it a good
database, a deliberately corrupted one, or an empty body; and a closed port on
127.0.0.1 and a `.invalid` name are what a switched-off Pi looks like. What that
cannot establish is anything about darts.local on the LAN. That was exercised
against a stand-in Linux host and is recorded in docs/dr.md.
"""

import calendar
import contextlib
import http.server
import shlex
import shutil
import socket
import sqlite3
import ssl
import subprocess
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

#: tests/deploy/test_backup_pull.py -> ../..
REPO_ROOT = Path(__file__).resolve().parents[2]
PULL = REPO_ROOT / "scripts" / "backup-pull.sh"
PULL_LIB = REPO_ROOT / "scripts" / "backup-pull-lib.sh"


def test_the_tools_the_script_needs_are_here() -> None:
    """A loud failure rather than a skip: without these, nothing below means anything."""
    assert shutil.which("sqlite3"), "install the sqlite3 command-line tool"
    assert shutil.which("curl"), "install curl"


def call(function: str, *args: str) -> list[str]:
    """Source backup-pull-lib.sh and call one function, returning its stdout lines."""
    quoted = " ".join(shlex.quote(arg) for arg in args)
    script = f"set -Eeuo pipefail\n. {shlex.quote(str(PULL_LIB))}\n{function} {quoted}\n"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"{function} failed: {result.stderr}"
    return [line for line in result.stdout.splitlines() if line.strip()]


def one(function: str, *args: str) -> str:
    """The single line a function printed, or "" when it printed nothing."""
    lines = call(function, *args)
    assert len(lines) <= 1, f"expected at most one line, got {lines}"
    return lines[0] if lines else ""


def name(stamp: str) -> str:
    return f"darts-{stamp}.db"


def stamp_at(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


# --- What counts as a copy -------------------------------------------------


def test_a_copy_is_named_like_the_pis_backups() -> None:
    assert call("copies_newest_first", "darts-20260930T001410Z.db") == ["darts-20260930T001410Z.db"]


@pytest.mark.parametrize(
    "candidate",
    [
        ".pull.Ab12Cd",  # the temporary a download is written to
        ".pull-err.Ab12Cd",
        "darts-latest.db",  # the share's name, if someone copied it in by hand
        "darts-20260930T001410Z.db.json",
        "darts-20260930T001410Z-1.db",
        "darts-2026093T001410Z.db",
        "notes.txt",
    ],
)
def test_anything_else_is_invisible(candidate: str) -> None:
    """Retention cannot delete, and "newest" cannot pick, a file it does not own."""
    assert call("copies_newest_first", candidate) == []
    assert call("copies_to_prune", "0", candidate, name("20260930T001410Z")) == []


def test_newest_is_read_from_the_name() -> None:
    names = [name("20260101T000000Z"), name("20261231T235959Z"), name("20260615T120000Z")]
    assert one("newest_copy", *names) == name("20261231T235959Z")
    assert call("copies_newest_first", *names) == [
        name("20261231T235959Z"),
        name("20260615T120000Z"),
        name("20260101T000000Z"),
    ]


# --- Retention -------------------------------------------------------------


def _copies(count: int) -> list[str]:
    """`count` hourly copies, oldest first -- the order a directory listing is not in."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    return [name(stamp_at(start + timedelta(hours=i))) for i in range(count)]


def test_retention_keeps_the_newest_n() -> None:
    copies = _copies(10)
    doomed = call("copies_to_prune", "3", *copies)
    assert doomed == list(reversed(copies[:7]))
    assert set(copies) - set(doomed) == set(copies[7:])


def test_retention_ignores_argument_order() -> None:
    copies = _copies(6)
    assert set(call("copies_to_prune", "2", *reversed(copies))) == set(copies[:4])


@pytest.mark.parametrize("keep", ["0", "1", "", "-5", "abc"])
def test_the_newest_copy_is_never_deleted(keep: str) -> None:
    copies = _copies(5)
    doomed = call("copies_to_prune", keep, *copies)
    assert copies[-1] not in doomed
    assert doomed == list(reversed(copies[:4]))


def test_keeping_more_than_there_are_deletes_nothing() -> None:
    assert call("copies_to_prune", "30", *_copies(4)) == []


def test_an_empty_directory_is_not_an_error() -> None:
    """An empty list under `set -u` is the bash-3.2 trap the lib avoids arrays for."""
    assert call("copies_to_prune", "3") == []
    assert one("newest_copy") == ""


def test_a_keep_with_a_leading_zero_is_decimal() -> None:
    """ "08" is invalid octal to bash arithmetic."""
    assert len(call("copies_to_prune", "08", *_copies(10))) == 2


# --- Time, without `date` --------------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(2000, 2, 29, 23, 59, 59, tzinfo=UTC),
        datetime(2000, 3, 1, tzinfo=UTC),
        datetime(2024, 2, 29, 8, 9, 8, tzinfo=UTC),  # 08 and 09: not octal
        datetime(2026, 9, 30, 0, 14, 10, tzinfo=UTC),
        datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
        datetime(2100, 3, 1, tzinfo=UTC),  # not a leap year
    ],
)
def test_stamps_convert_to_the_same_epoch_as_python(moment: datetime) -> None:
    expected = calendar.timegm(moment.utctimetuple())
    assert one("stamp_to_epoch", stamp_at(moment)) == str(expected)


@pytest.mark.parametrize("stamp", ["", "latest", "2026-09-30T00:14:10Z", "20260930T001410"])
def test_a_malformed_stamp_converts_to_nothing(stamp: str) -> None:
    assert one("stamp_to_epoch", stamp) == ""


def test_the_stamp_comes_out_of_a_path_or_a_name() -> None:
    assert one("stamp_of", "darts-20260930T001410Z.db") == "20260930T001410Z"
    assert one("stamp_of", "/a b/darts-20260930T001410Z.db") == "20260930T001410Z"


@pytest.mark.parametrize(
    ("seconds", "said"),
    [
        (-5, "0m"),
        (0, "0m"),
        (59, "0m"),
        (61, "1m"),
        (3 * 3600 + 7 * 60, "3h 7m"),
        (273_600, "3d 4h"),
    ],
)
def test_ages_are_said_the_way_a_person_would(seconds: int, said: str) -> None:
    assert one("format_age", str(seconds)) == said


# --- Staleness -------------------------------------------------------------

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def stale(hours: int, *names: str) -> str:
    return one("staleness_warning", str(calendar.timegm(NOW.utctimetuple())), str(hours), *names)


def test_a_fresh_copy_is_not_stale() -> None:
    assert stale(24, name(stamp_at(NOW - timedelta(hours=1)))) == ""


def test_exactly_the_threshold_is_not_yet_stale() -> None:
    assert stale(24, name(stamp_at(NOW - timedelta(hours=24)))) == ""


def test_one_second_past_the_threshold_is_stale() -> None:
    newest = name(stamp_at(NOW - timedelta(hours=24, seconds=1)))
    warning = stale(24, newest)
    assert "1d 0h old" in warning
    assert newest in warning
    assert "24h threshold" in warning


def test_only_the_newest_copy_decides() -> None:
    old = name(stamp_at(NOW - timedelta(days=40)))
    fresh = name(stamp_at(NOW - timedelta(minutes=5)))
    assert stale(24, old, fresh) == ""
    assert "3d 4h old" in stale(24, old, name(stamp_at(NOW - timedelta(days=3, hours=4))))


def test_having_no_copy_at_all_is_stale() -> None:
    assert "no local copy yet" in stale(24)
    assert "no local copy yet" in stale(24, "notes.txt", ".pull.Ab12Cd")


# --- What a curl failure means --------------------------------------------


@pytest.mark.parametrize(
    ("code", "connect", "meaning"),
    [
        ("6", "0.000000", "unreachable"),  # could not resolve
        ("7", "0.000000", "unreachable"),  # refused, or no route to host
        ("28", "0.000000", "unreachable"),  # mDNS gave up inside the connect timeout
        ("28", "", "unreachable"),
        ("28", "0.000456", "failed"),  # connected, then the app hung
        ("22", "0.000456", "failed"),  # HTTP error, via --fail
        ("18", "0.000456", "failed"),  # partial transfer
        ("56", "0.000456", "failed"),  # connection reset mid-body
        ("23", "0.000000", "failed"),  # could not write the temporary
    ],
)
def test_curl_failures_are_classified(code: str, connect: str, meaning: str) -> None:
    assert one("classify_curl_failure", code, connect) == meaning


# --- The script, end to end ------------------------------------------------


def _database(path: Path, *, rows: int = 3000) -> Path:
    """A small database shaped like the real one: STRICT, indexed, versioned."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 3")
    conn.execute(
        "CREATE TABLE darts (id INTEGER PRIMARY KEY, segment INTEGER NOT NULL,"
        " multiplier INTEGER NOT NULL) STRICT"
    )
    conn.execute("CREATE INDEX darts_segment ON darts (segment, multiplier)")
    conn.executemany(
        "INSERT INTO darts (segment, multiplier) VALUES (?, ?)",
        [(i % 21, 1 + i % 3) for i in range(rows)],
    )
    conn.commit()
    conn.close()
    return path


def _corrupt_a_page(source: Path, target: Path) -> Path:
    """Damage the cell area of the last page, leaving the header intact.

    The quiet kind of damage: the file opens, sqlite3 exits 0, and only
    integrity_check's output says anything is wrong.
    """
    data = bytearray(source.read_bytes())
    page_size = int.from_bytes(data[16:18], "big")
    last = len(data) - page_size
    data[last + page_size - 200 : last + page_size - 100] = b"\xff" * 100
    target.write_bytes(bytes(data))
    return target


def _corrupt_the_header(source: Path, target: Path) -> Path:
    """The loud kind: sqlite3 cannot open it at all, before integrity_check runs."""
    data = bytearray(source.read_bytes())
    data[0:16] = b"not a database!!"
    target.write_bytes(bytes(data))
    return target


def _closed_port() -> int:
    """A port nothing is listening on, found by binding it and letting it go."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    # A space in the path, because the real default is ~/Library/Application Support.
    path = tmp_path / "Application Support" / "darts-backups"
    path.mkdir(parents=True)
    return path


def pull(dest: Path, url: str, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(dest.parent),
        "BACKUP_PULL_DIR": str(dest),
        "BACKUP_PULL_URL": url,
        "BACKUP_PULL_CONNECT_TIMEOUT": "5",
        **env,
    }
    return subprocess.run(
        [str(PULL)], capture_output=True, text=True, timeout=120, env=environment, check=False
    )


def contents(dest: Path) -> dict[str, bytes]:
    """Everything in the directory, hidden files included, byte for byte."""
    return {p.name: p.read_bytes() for p in sorted(dest.iterdir())}


def seed_copy(dest: Path, moment: datetime, source: Path) -> Path:
    path = dest / name(stamp_at(moment))
    shutil.copyfile(source, path)
    return path


def test_a_good_download_is_accepted(tmp_path: Path, dest: Path) -> None:
    good = _database(tmp_path / "good.db")
    result = pull(dest, good.as_uri())
    assert result.returncode == 0, result.stderr
    [copy] = dest.iterdir()
    assert copy.name.startswith("darts-") and copy.name.endswith("Z.db")
    assert copy.read_bytes() == good.read_bytes()
    assert "integrity ok" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("damage", "said"),
    [
        (_corrupt_a_page, "integrity_check said: *** in database main ***"),
        (_corrupt_the_header, "file is not a database"),
    ],
)
def test_a_corrupt_download_never_replaces_a_good_copy(
    tmp_path: Path, dest: Path, damage: Callable[[Path, Path], Path], said: str
) -> None:
    good = _database(tmp_path / "good.db")
    bad = damage(good, tmp_path / "bad.db")
    known_good = seed_copy(dest, datetime.now(UTC) - timedelta(hours=1), good)
    before = contents(dest)

    result = pull(dest, bad.as_uri())

    assert result.returncode == 1
    assert said in result.stderr
    assert "untouched" in result.stderr
    assert contents(dest) == before, "the known-good copy changed, or a file was left behind"
    assert known_good.read_bytes() == good.read_bytes()


def test_the_page_corruption_is_the_quiet_kind(tmp_path: Path) -> None:
    """The precondition the test above relies on: this file opens, and only
    integrity_check notices. Without it the "page" case would be the header case
    again."""
    bad = _corrupt_a_page(_database(tmp_path / "good.db"), tmp_path / "bad.db")
    result = subprocess.run(
        ["sqlite3", "-readonly", str(bad), "PRAGMA integrity_check;"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() != "ok"


def test_an_empty_body_is_rejected(tmp_path: Path, dest: Path) -> None:
    """Zero bytes is a valid, empty SQLite database: integrity_check says ok.

    A Pi that loses power mid-stream can end a response with no Content-Length
    cleanly, so this is the truncated download the check must still refuse.
    """
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    result = pull(dest, empty.as_uri())
    assert result.returncode == 1
    assert "user_version is '0'" in result.stderr
    assert contents(dest) == {}


@pytest.mark.parametrize(
    "url",
    [
        f"http://127.0.0.1:{_closed_port()}/api/export/db",
        "http://darts.invalid:8000/api/export/db",
    ],
    ids=["closed-port", "unresolvable"],
)
def test_a_switched_off_pi_is_one_quiet_line(tmp_path: Path, dest: Path, url: str) -> None:
    good = _database(tmp_path / "good.db")
    seed_copy(dest, datetime.now(UTC) - timedelta(hours=1), good)
    before = contents(dest)

    result = pull(dest, url)

    assert result.returncode == 0
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1, lines
    assert lines[0].startswith("info: the Pi is not reachable")
    assert "curl exit" in lines[0]
    assert contents(dest) == before


def test_a_long_silence_adds_one_warning(tmp_path: Path, dest: Path) -> None:
    good = _database(tmp_path / "good.db")
    seed_copy(dest, datetime.now(UTC) - timedelta(days=3, hours=4, minutes=1), good)

    result = pull(dest, f"http://127.0.0.1:{_closed_port()}/", BACKUP_PULL_STALE_HOURS="24")

    assert result.returncode == 0
    info, warning = result.stderr.splitlines()
    assert info.startswith("info: ")
    assert warning.startswith("warning: the newest local copy is 3d 4h old")


def test_a_first_run_against_a_switched_off_pi_says_there_is_no_copy(dest: Path) -> None:
    result = pull(dest, f"http://127.0.0.1:{_closed_port()}/")
    assert result.returncode == 0
    assert result.stderr.splitlines()[-1] == (
        "warning: there is no local copy yet -- nothing has ever been pulled"
    )


def test_a_successful_pull_says_nothing_about_staleness(tmp_path: Path, dest: Path) -> None:
    good = _database(tmp_path / "good.db")
    seed_copy(dest, datetime.now(UTC) - timedelta(days=30), good)
    result = pull(dest, good.as_uri())
    assert result.returncode == 0
    assert "warning" not in result.stderr


def test_retention_runs_after_a_pull(tmp_path: Path, dest: Path) -> None:
    good = _database(tmp_path / "good.db")
    start = datetime.now(UTC) - timedelta(days=10)
    for day in range(5):
        seed_copy(dest, start + timedelta(days=day), good)
    (dest / "notes.txt").write_text("mine")

    result = pull(dest, good.as_uri(), BACKUP_PULL_KEEP="3")

    assert result.returncode == 0, result.stderr
    kept = sorted(p.name for p in dest.glob("darts-*.db"))
    assert len(kept) == 3
    assert kept[0] == name(stamp_at(start + timedelta(days=3)))
    assert kept[-1] > name(stamp_at(start + timedelta(days=4)))  # the new one
    assert (dest / "notes.txt").read_text() == "mine"
    assert result.stderr.count("retention: deleted") == 3


def test_a_rejected_pull_never_runs_retention(tmp_path: Path, dest: Path) -> None:
    good = _database(tmp_path / "good.db")
    for day in range(4):
        seed_copy(dest, datetime.now(UTC) - timedelta(days=10 - day), good)
    before = contents(dest)
    bad = _corrupt_a_page(good, tmp_path / "bad.db")
    result = pull(dest, bad.as_uri(), BACKUP_PULL_KEEP="1")
    assert result.returncode == 1
    assert contents(dest) == before


def test_an_http_error_is_a_failure_not_a_database(dest: Path) -> None:
    """--fail: an error page must never be saved as a copy."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        proc = subprocess.Popen(
            [str(PULL)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": str(dest.parent),
                "BACKUP_PULL_DIR": str(dest),
                "BACKUP_PULL_URL": f"http://127.0.0.1:{port}/api/export/db",
            },
        )
        conn, _ = server.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 5\r\n"
                b"Connection: close\r\n\r\nbusy!"
            )
        _, stderr = proc.communicate(timeout=60)
    assert proc.returncode == 1
    assert "the pull from" in stderr and "failed" in stderr
    assert contents(dest) == {}


@pytest.mark.parametrize(
    ("setting", "value"), [("BACKUP_PULL_KEEP", "three"), ("BACKUP_PULL_STALE_HOURS", "1.5")]
)
def test_a_malformed_setting_is_refused_before_anything_happens(
    tmp_path: Path, dest: Path, setting: str, value: str
) -> None:
    good = _database(tmp_path / "good.db")
    result = pull(dest, good.as_uri(), **{setting: value})
    assert result.returncode == 1
    assert setting in result.stderr
    assert contents(dest) == {}


def test_help_is_on_stdout_and_exits_zero() -> None:
    result = subprocess.run([str(PULL), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "BACKUP_PULL_URL" in result.stdout


# --- over HTTPS, as the Pi serves it since #71 ----------------------------------

MAKE_CERT = REPO_ROOT / "scripts" / "make-cert.sh"


def _issue(directory: Path) -> Path:
    """A root and a leaf from the real script, for `localhost` so curl can reach it."""
    subprocess.run(
        [str(MAKE_CERT)],
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(directory),
             "MAKE_CERT_DIR": str(directory), "MAKE_CERT_NAME": "localhost"},
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    return directory


@contextlib.contextmanager
def _https_server(certs: Path, body: bytes) -> Iterator[str]:
    """Serves `body` at /api/export/db over TLS, the way Caddy fronts the app."""

    class Export(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Export)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certs / "darts-leaf.crt", certs / "darts-leaf.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_address[1]}/api/export/db"
    finally:
        server.shutdown()
        server.server_close()


def test_a_pull_over_https_trusts_the_root_it_is_given(tmp_path: Path, dest: Path) -> None:
    good = _database(tmp_path / "good.db")
    certs = _issue(tmp_path / "certs")
    with _https_server(certs, good.read_bytes()) as url:
        result = pull(dest, url, BACKUP_PULL_CACERT=str(certs / "darts-root.crt"))
    assert result.returncode == 0, result.stderr
    [copy] = dest.iterdir()
    assert copy.read_bytes() == good.read_bytes()


def test_the_root_is_found_where_make_cert_keeps_it(tmp_path: Path, dest: Path) -> None:
    """On the Mac that made the certificate there is nothing to configure."""
    home = tmp_path / "home"
    certs = _issue(home / "Library" / "Application Support" / "darts-tls")
    good = _database(tmp_path / "good.db")
    with _https_server(certs, good.read_bytes()) as url:
        result = pull(dest, url, HOME=str(home))
    assert result.returncode == 0, result.stderr


def test_an_untrusted_certificate_is_a_failure_that_says_so(tmp_path: Path, dest: Path) -> None:
    """Not "probably switched off": the Pi answered, and the answer was not trusted."""
    good = _database(tmp_path / "good.db")
    served = _issue(tmp_path / "served")
    other = _issue(tmp_path / "other")
    with _https_server(served, good.read_bytes()) as url:
        result = pull(dest, url, BACKUP_PULL_CACERT=str(other / "darts-root.crt"))
    assert result.returncode == 1
    assert "curl exit 60" in result.stderr
    assert "certificate is not trusted" in result.stderr
    assert contents(dest) == {}


def test_the_default_url_is_https_by_name() -> None:
    result = subprocess.run([str(PULL), "--help"], capture_output=True, text=True, check=False)
    assert "https://darts.local/api/export/db" in result.stdout
    assert "BACKUP_PULL_CACERT" in result.stdout
