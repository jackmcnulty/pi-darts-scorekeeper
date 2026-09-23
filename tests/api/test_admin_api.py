"""The two maintenance endpoints: publish a snapshot, rebuild the caches.

Both are unauthenticated, which is a decision rather than an omission -- one
household, one LAN, and `POST /api/matches` is just as open. Both are
synchronous and both are idempotent, and the idempotence is asserted rather than
assumed, because a button that is unsafe to press twice is a button nobody
presses once.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from apifixtures import make_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from seed import build

from darts.config import Settings
from darts.db.durability import SIDECARS, read_only_uri
from darts.repo.legstate import leg_state_for


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    resolved = make_settings(tmp_path)
    build(resolved.db_path).close()
    return resolved


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as started:
        yield started


# --- POST /api/admin/snapshot ----------------------------------------------


def test_taking_a_snapshot_publishes_the_pair_and_returns_its_manifest(
    client: TestClient, settings: Settings
) -> None:
    response = client.post("/api/admin/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] == "darts-latest.db"
    assert body["path"] == str(settings.snapshot_dir / "darts-latest.db")
    assert body["manifest_path"] == str(settings.snapshot_dir / "snapshot.json")
    assert body["schema_version"] == 2
    assert body["size_bytes"] > 0
    assert body["created_at"].endswith("Z")
    assert body["row_counts"]["darts"] > 0

    assert sorted(path.name for path in settings.snapshot_dir.iterdir()) == [
        "darts-latest.db",
        "snapshot.json",
    ]


def test_the_response_is_the_manifest_that_was_written(
    client: TestClient, settings: Settings
) -> None:
    """A client that called this and one that read the file see one answer."""
    body = client.post("/api/admin/snapshot").json()
    written = json.loads((settings.snapshot_dir / "snapshot.json").read_text(encoding="utf-8"))

    assert written["snapshot"] == body["snapshot"]
    assert written["created_at"] == body["created_at"]
    assert written["schema_version"] == body["schema_version"]
    assert written["size_bytes"] == body["size_bytes"]
    assert written["row_counts"] == body["row_counts"]


def test_the_published_snapshot_is_sound_and_self_contained(
    client: TestClient, settings: Settings
) -> None:
    client.post("/api/admin/snapshot")
    published = settings.snapshot_dir / "darts-latest.db"

    for suffix in SIDECARS:
        assert not published.with_name(published.name + suffix).exists()
    with closing(sqlite3.connect(read_only_uri(published), uri=True)) as conn:
        assert [row[0] for row in conn.execute("PRAGMA integrity_check")] == ["ok"]
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 2
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal"


def test_snapshotting_twice_is_safe_and_replaces_rather_than_accumulates(
    client: TestClient, settings: Settings
) -> None:
    first = client.post("/api/admin/snapshot").json()
    second = client.post("/api/admin/snapshot").json()

    assert second["row_counts"] == first["row_counts"]
    assert len(list(settings.snapshot_dir.iterdir())) == 2


def test_a_snapshot_reflects_play_recorded_since_the_last_one(
    client: TestClient, settings: Settings
) -> None:
    """Not a cached answer: each press copies the database as it is now."""
    before = client.post("/api/admin/snapshot").json()["row_counts"]["players"]

    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.execute("INSERT INTO players(display_name) VALUES ('Gus')")
        conn.commit()

    assert client.post("/api/admin/snapshot").json()["row_counts"]["players"] == before + 1


# --- POST /api/admin/rebuild-caches ----------------------------------------


def test_rebuilding_a_correct_database_changes_nothing(client: TestClient) -> None:
    """The seed's caches already agree with its darts, so this is a pure no-op."""
    response = client.post("/api/admin/rebuild-caches")

    assert response.status_code == 200
    body = response.json()
    assert body["legs_visited"] == 8
    assert body["legs_changed"] == 0
    assert body["rows_before"] == body["rows_after"] > 0


def test_it_visits_every_leg_not_only_the_unfinished_ones(
    client: TestClient, settings: Settings
) -> None:
    """Six of the seed's eight legs are finished and correctly hold no cache rows."""
    with closing(sqlite3.connect(settings.db_path)) as conn:
        total = conn.execute("SELECT count(*) FROM legs").fetchone()[0]
        unfinished = conn.execute(
            "SELECT count(*) FROM legs WHERE winner_team_id IS NULL"
        ).fetchone()[0]
    assert unfinished < total

    assert client.post("/api/admin/rebuild-caches").json()["legs_visited"] == total


def test_rebuilding_restores_a_cache_that_was_wiped(client: TestClient, settings: Settings) -> None:
    """Not vacuously a no-op: it does write, when there is something to write."""
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        leg_id = conn.execute(
            "SELECT id FROM legs WHERE winner_team_id IS NULL ORDER BY id"
        ).fetchone()["id"]
        expected = leg_state_for(conn, leg_id)
        conn.execute("DELETE FROM leg_team_state WHERE leg_id = ?", (leg_id,))
        conn.commit()
        assert leg_state_for(conn, leg_id) == []

    body = client.post("/api/admin/rebuild-caches").json()
    assert body["legs_changed"] == 1
    assert body["rows_after"] > body["rows_before"]

    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        assert leg_state_for(conn, leg_id) == expected


def test_rebuilding_removes_a_cache_a_finished_leg_should_not_have(
    client: TestClient, settings: Settings
) -> None:
    """The drift a sweep limited to unfinished legs could never repair."""
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        finished = conn.execute(
            "SELECT id, match_id FROM legs WHERE winner_team_id IS NOT NULL ORDER BY id"
        ).fetchone()
        conn.execute(
            "INSERT INTO leg_team_state(leg_id, team_id, match_id, remaining, is_open, "
            "darts_thrown, points) VALUES (?, (SELECT id FROM teams WHERE match_id = ? "
            "LIMIT 1), ?, 7, 1, 3, 0)",
            (finished["id"], finished["match_id"], finished["match_id"]),
        )
        conn.commit()
        assert leg_state_for(conn, finished["id"]) != []

    body = client.post("/api/admin/rebuild-caches").json()
    assert body["legs_changed"] == 1
    assert body["rows_after"] < body["rows_before"]

    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        assert leg_state_for(conn, finished["id"]) == []


def test_rebuilding_twice_reports_the_same_thing(client: TestClient) -> None:
    assert client.post("/api/admin/rebuild-caches").json() == (
        client.post("/api/admin/rebuild-caches").json()
    )


def test_a_rebuild_does_not_disturb_the_recorded_history(
    client: TestClient, settings: Settings
) -> None:
    """The caches are disposable; the darts are not, and must be untouched."""
    with closing(sqlite3.connect(settings.db_path)) as conn:
        before = conn.execute("SELECT count(*), sum(segment * multiplier) FROM darts").fetchone()

    client.post("/api/admin/rebuild-caches")

    with closing(sqlite3.connect(settings.db_path)) as conn:
        assert conn.execute("SELECT count(*), sum(segment * multiplier) FROM darts").fetchone() == (
            before
        )


# --- the trust model --------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/admin/snapshot", "/api/admin/rebuild-caches"])
def test_the_admin_endpoints_need_no_credentials(client: TestClient, path: str) -> None:
    """Recorded as a decision: LAN-only, one household, same as every other route."""
    assert client.post(path).status_code == 200


def test_a_get_performs_no_action(client: TestClient, settings: Settings) -> None:
    """These are actions, and only POST performs one.

    The status is 404 rather than 405 because the single-page-app mount at `/`
    is declared last and fully matches any path a router did not claim, so it
    answers before Starlette can report a method mismatch. What matters is that
    nothing happened, which is what this asserts.
    """
    assert client.get("/api/admin/snapshot").status_code == 404
    assert not settings.snapshot_dir.exists()

    assert client.get("/api/admin/rebuild-caches").status_code == 404
