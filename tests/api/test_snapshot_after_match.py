"""#30: the dart that wins a match republishes the snapshot.

Three claims, each of which would be easy to get subtly wrong. The snapshot
contains the win -- taken inside the dart's transaction it would not. Only the
match-winning dart triggers one -- not every dart, and not a leg that leaves the
match undecided. And a snapshot that fails cannot fail the dart, which has
already been committed and answered by the time the snapshot runs.

`TestClient` runs background tasks before `post` returns, which is what lets
these tests assert on the published files without waiting. It also means an
exception escaping the task would surface here as an error, so the failure test
proves the exception is swallowed rather than merely unreported.
"""

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playfixtures import CRICKET_CLOSE_OUT, alternating, cricket, new_match, throw_all, throw_match

from darts.config import Settings
from darts.db.durability import read_only_uri
from darts.services import snapshot


def _leg(match: dict[str, object]) -> int:
    return int(match["current_leg_id"])  # type: ignore[call-overload]


def test_winning_the_match_publishes_a_snapshot_containing_the_win(
    client: TestClient, settings: Settings
) -> None:
    match = new_match(client, cricket("standard"))
    body = throw_all(client, _leg(match), alternating(CRICKET_CLOSE_OUT)).json()
    assert body["is_complete"] is True

    published = snapshot.snapshot_path(settings.snapshot_dir)
    with closing(sqlite3.connect(read_only_uri(published), uri=True)) as conn:
        winner, completed_at = conn.execute(
            "SELECT winner_team_id, completed_at FROM matches WHERE id = ?", (match["id"],)
        ).fetchone()
    assert winner == body["winner_team_id"]
    assert completed_at is not None

    manifest = json.loads(snapshot.manifest_path(settings.snapshot_dir).read_text())
    assert manifest["snapshot"] == "darts-latest.db"


def test_darts_that_do_not_win_the_match_publish_nothing(
    client: TestClient, settings: Settings
) -> None:
    """Every dart but the last one, in a best-of-one: nothing is published.

    The directory's absence is the assertion -- `snapshot.create` makes it, so
    if it exists something took a snapshot.
    """
    match = new_match(client, cricket("standard"))
    script = alternating(CRICKET_CLOSE_OUT)
    body = throw_all(client, _leg(match), script[:-1]).json()

    assert body["is_complete"] is False
    assert not settings.snapshot_dir.exists()


def test_winning_a_leg_but_not_the_match_publishes_nothing(
    client: TestClient, settings: Settings
) -> None:
    """A best-of-three after one leg: a leg complete is not a match complete."""
    match = new_match(client, cricket("standard", best_of=3))
    body = throw_match(client, _leg(match), alternating(CRICKET_CLOSE_OUT)).json()

    assert body["current_leg"]["is_complete"] is True
    assert body["is_complete"] is False
    assert not settings.snapshot_dir.exists()


def test_a_failed_snapshot_does_not_fail_the_winning_dart(
    client: TestClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(database: Path, snapshot_dir: Path) -> snapshot.SnapshotResult:
        raise OSError("disk full")

    monkeypatch.setattr(snapshot, "create", broken)
    match = new_match(client, cricket("standard"))

    with caplog.at_level(logging.ERROR, logger="darts.api.play"):
        response = throw_all(client, _leg(match), alternating(CRICKET_CLOSE_OUT))

    assert response.status_code == 200
    assert response.json()["is_complete"] is True
    [failure] = [r for r in caplog.records if r.name == "darts.api.play"]
    assert failure.message == "snapshot after match failed"
    assert failure.exc_info is not None

    # And the win is durable: the match reads back complete.
    state = client.get(f"/api/matches/{match['id']}/state")
    assert state.json()["is_complete"] is True


def test_the_snapshot_is_logged_with_the_match_it_was_for(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    match = new_match(client, cricket("standard"))
    with caplog.at_level(logging.INFO, logger="darts.api.play"):
        throw_all(client, _leg(match), alternating(CRICKET_CLOSE_OUT))

    [record] = [r for r in caplog.records if r.message == "snapshot after match"]
    assert record.match_id == match["id"]  # type: ignore[attr-defined]
    assert record.bytes > 0  # type: ignore[attr-defined]
