"""#20's sharpest criterion: a snapshot taken *during active play* is sound.

The shape follows `tests/db/test_backup.py`'s concurrent-writer test, which #12
established for the same question, rather than inventing a second one. The
writer runs on its own thread with its **own connection** -- the one-connection-
per-user rule this repository states everywhere applies to a test as much as to
a request, and sharing one would be testing something the application never
does.

What makes this more than "it did not crash": every visit and its three darts
commit together, so a copy that caught a transaction mid-flight would show a
dart whose visit is missing. `foreign_key_check` reports that, and so does the
3:1 ratio, and both are asserted on every snapshot taken.
"""

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from dbfixtures import add_visit, scaffold

from darts.db.connection import connection, transaction
from darts.db.durability import SIDECARS, integrity_report, read_only_uri, verify_file
from darts.services import snapshot


def _counts(artifact: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(read_only_uri(artifact), uri=True)) as conn:
        visits = int(conn.execute("SELECT count(*) FROM visits").fetchone()[0])
        darts = int(conn.execute("SELECT count(*) FROM darts").fetchone()[0])
    return visits, darts


def test_snapshot_consistency(tmp_path: Path) -> None:
    """Snapshot on this thread while another writes; every copy is a valid image."""
    database = tmp_path / "darts.db"
    snapshots = tmp_path / "snapshots"
    with connection(database) as conn:
        scaffold(conn)

    stop = threading.Event()
    failures: list[BaseException] = []
    #: One entry per committed visit. Appending is what makes the writer's
    #: progress observable, so the test waits for real commits instead of
    #: sleeping and hoping -- which is what made an earlier draft of this flaky.
    committed: list[int] = []

    def writer() -> None:
        try:
            with connection(database) as conn:
                index = 0
                while not stop.is_set():
                    with transaction(conn):
                        add_visit(conn, index)
                    committed.append(index)
                    index += 1
        except BaseException as exc:  # pragma: no cover - reported by the assertion below
            failures.append(exc)

    def wait_for_commits(at_least: int) -> None:
        """Block until the writer has committed `at_least` visits.

        The deadline is a generous regression guard, not the assertion: what
        this establishes is the deterministic property that a snapshot is taken
        only once there is concurrent work to catch mid-flight.
        """
        deadline = time.monotonic() + 30
        while len(committed) < at_least:
            assert not failures, failures
            assert time.monotonic() < deadline, "the writer thread made no progress"
            time.sleep(0.001)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        taken = []
        for round_number in range(5):
            wait_for_commits(round_number + 1)
            before = len(committed)
            result = snapshot.create(database, snapshots)
            # Copy the published file aside immediately: the next iteration
            # replaces it, and each one has to be judged on its own.
            aside = tmp_path / f"taken-{round_number}.db"
            aside.write_bytes(result.path.read_bytes())
            taken.append((aside, result, before))
    finally:
        stop.set()
        thread.join(timeout=30)
    assert not failures
    assert not thread.is_alive()
    assert len(committed) >= 5, "the writer was not running alongside the snapshots"

    for aside, result, committed_before in taken:
        # PRAGMA integrity_check, which is the criterion in #20's own words,
        # plus foreign_key_check: a torn copy shows a dart with no visit.
        assert verify_file(aside) is None

        visits, darts = _counts(aside)
        assert darts == visits * 3, "a copy caught a visit mid-commit"
        # Everything committed before the copy started must be in it, and a
        # point-in-time image cannot contain a visit that was never committed.
        assert visits >= committed_before > 0
        assert visits <= len(committed)

        # And the manifest describes that same file rather than the live one.
        assert result.row_counts["visits"] == visits
        assert result.row_counts["darts"] == darts

    counted = [result.row_counts["visits"] for _, result, _ in taken]
    assert counted == sorted(counted), "later snapshots must see at least as much"

    with connection(database) as conn:
        assert conn.execute("SELECT count(*) FROM visits").fetchone()[0] > 0


def test_a_snapshot_taken_mid_play_is_still_one_self_contained_file(tmp_path: Path) -> None:
    """The WAL collapse has to survive concurrency too, or the copy loses pages.

    A writer holding the database in WAL mode is exactly the situation in which
    a naive copy would leave committed pages in a sidecar the artifact does not
    carry.
    """
    database = tmp_path / "darts.db"
    snapshots = tmp_path / "snapshots"
    with connection(database) as conn:
        scaffold(conn)

    stop = threading.Event()

    def writer() -> None:
        with connection(database) as conn:
            index = 0
            while not stop.is_set():
                with transaction(conn):
                    add_visit(conn, index)
                index += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        result = snapshot.create(database, snapshots)
    finally:
        stop.set()
        thread.join(timeout=30)

    for suffix in SIDECARS:
        assert not result.path.with_name(result.path.name + suffix).exists()
    with closing(sqlite3.connect(read_only_uri(result.path), uri=True)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
        assert integrity_report(conn) is None
        # The live database is still WAL and still being written; the copy is not.
    with connection(database) as live:
        assert live.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_user_version_survives_a_snapshot_taken_mid_play(tmp_path: Path) -> None:
    """#20 names `user_version` explicitly, and a concurrent copy must keep it."""
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        scaffold(conn)
        expected = int(conn.execute("PRAGMA user_version").fetchone()[0])
    assert expected > 0

    stop = threading.Event()

    def writer() -> None:
        with connection(database) as conn:
            index = 0
            while not stop.is_set():
                with transaction(conn):
                    add_visit(conn, index)
                index += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        result = snapshot.create(database, tmp_path / "snapshots")
    finally:
        stop.set()
        thread.join(timeout=30)

    with closing(sqlite3.connect(read_only_uri(result.path), uri=True)) as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == expected
    assert result.schema_version == expected
