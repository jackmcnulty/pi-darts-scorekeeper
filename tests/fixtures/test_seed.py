"""The seeded fixture database: reproducible, and rich enough for #19."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from seed import MATCHES, PLAYERS, build, digest, dump


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    conn = build(tmp_path_factory.mktemp("seed") / "seed.db")
    yield conn
    conn.close()


def test_seed_is_deterministic(tmp_path: Path) -> None:
    """Two independent builds agree on every seeded row.

    The comparison is a canonical dump, not the database file: `applied_at` and
    SQLite's own page allocation are outside the seed's control, while the rows
    are exactly what a fixture promises. A failure prints the differing dump, so
    the offending column is visible rather than just a changed hash.
    """
    first, second = build(tmp_path / "one.db"), build(tmp_path / "two.db")
    try:
        assert dump(first) == dump(second)
        assert digest(first) == digest(second)
    finally:
        first.close()
        second.close()


def test_the_dump_notices_a_changed_row(seeded: sqlite3.Connection, tmp_path: Path) -> None:
    """The determinism test would be worthless if the dump ignored differences."""
    other = build(tmp_path / "edited.db")
    try:
        assert digest(other) == digest(seeded)
        other.execute("UPDATE players SET display_name = 'Zed' WHERE id = 1")
        assert digest(other) != digest(seeded)
    finally:
        other.close()


def test_every_player_and_match_is_present(seeded: sqlite3.Connection) -> None:
    names = [row[0] for row in seeded.execute("SELECT display_name FROM players ORDER BY id")]
    assert names == list(PLAYERS)
    assert seeded.execute("SELECT count(*) FROM matches").fetchone()[0] == len(MATCHES)


def test_no_seeded_timestamp_comes_from_the_clock(seeded: sqlite3.Connection) -> None:
    """Every defaulted column is supplied explicitly, or determinism is a fiction."""
    stamps = [
        ("players", "created_at"),
        ("matches", "created_at"),
        ("legs", "started_at"),
        ("darts", "thrown_at"),
    ]
    for table, column in stamps:
        outside = seeded.execute(
            f"SELECT count(*) FROM {table} WHERE {column} NOT LIKE '2026-01-0%'"
        ).fetchone()[0]
        assert outside == 0, f"{table}.{column}"


def test_client_dart_ids_are_unique_and_reproducible(seeded: sqlite3.Connection) -> None:
    ids = [row[0] for row in seeded.execute("SELECT client_dart_id FROM darts ORDER BY id")]
    assert len(set(ids)) == len(ids)
    assert all(value.startswith("seed:m") for value in ids)


def test_the_fixture_covers_both_x01_shapes(seeded: sqlite3.Connection) -> None:
    solo = seeded.execute(
        "SELECT count(*) FROM matches m JOIN teams t ON t.match_id = m.id "
        "WHERE m.game_type = 'x01' AND t.is_solo = 1"
    ).fetchone()[0]
    pairs = seeded.execute(
        """SELECT m.id FROM matches m JOIN teams t ON t.match_id = m.id
           JOIN team_members tm ON tm.team_id = t.id
           WHERE m.game_type = 'x01' AND t.is_solo = 0
           GROUP BY t.id HAVING count(*) = 2"""
    ).fetchall()
    assert solo >= 2
    assert len(pairs) == 2, "one match of two two-player teams"


def test_the_fixture_covers_every_cricket_variant(seeded: sqlite3.Connection) -> None:
    variants = [
        row[0]
        for row in seeded.execute(
            "SELECT DISTINCT variant FROM matches WHERE game_type = 'cricket' ORDER BY variant"
        )
    ]
    assert variants == ["cutthroat", "quick", "standard"]
    for variant in variants:
        legs = seeded.execute(
            "SELECT count(*) FROM legs l JOIN matches m ON m.id = l.match_id WHERE m.variant = ?",
            (variant,),
        ).fetchone()[0]
        assert legs >= 1, variant


def test_the_fixture_contains_a_bust_and_a_checkout(seeded: sqlite3.Connection) -> None:
    assert seeded.execute("SELECT count(*) FROM visits WHERE is_bust = 1").fetchone()[0] >= 1
    # A checkout is a completed leg with a winner and a visit that reached zero.
    checkouts = seeded.execute(
        """SELECT count(*) FROM visits v JOIN legs l ON l.id = v.leg_id
           JOIN matches m ON m.id = v.match_id
           WHERE m.game_type = 'x01' AND v.score_after = 0 AND l.winner_team_id = v.team_id"""
    ).fetchone()[0]
    assert checkouts >= 1


def test_cricket_effects_and_point_events_come_from_the_engine(
    seeded: sqlite3.Connection,
) -> None:
    """Every cricket dart has an effect row, and only cricket darts do."""
    unmatched = seeded.execute(
        """SELECT count(*) FROM darts d JOIN visits v ON v.id = d.visit_id
           JOIN matches m ON m.id = v.match_id
           LEFT JOIN cricket_dart_effects e ON e.dart_id = d.id
           WHERE (m.game_type = 'cricket') != (e.dart_id IS NOT NULL)"""
    ).fetchone()[0]
    assert unmatched == 0

    # Cut-throat pays opponents and never the thrower; standard pays the thrower.
    conceded = seeded.execute(
        """SELECT count(*) FROM cricket_point_events e JOIN darts d ON d.id = e.dart_id
           JOIN matches m ON m.id = e.match_id
           WHERE m.variant = 'cutthroat' AND e.recipient_team_id = d.team_id"""
    ).fetchone()[0]
    assert conceded == 0
    assert (
        seeded.execute(
            "SELECT count(*) FROM cricket_point_events e JOIN matches m ON m.id = e.match_id "
            "WHERE m.variant = 'cutthroat'"
        ).fetchone()[0]
        > 0
    )
    # Quick cricket scores for nobody, so it must produce no events at all.
    assert (
        seeded.execute(
            "SELECT count(*) FROM cricket_point_events e JOIN matches m ON m.id = e.match_id "
            "WHERE m.variant = 'quick'"
        ).fetchone()[0]
        == 0
    )
    assert (
        seeded.execute("SELECT count(*) FROM cricket_dart_effects WHERE wasted = 1").fetchone()[0]
        > 0
    )


def test_replay_caches_exist_only_for_unfinished_legs(seeded: sqlite3.Connection) -> None:
    cached = {row[0] for row in seeded.execute("SELECT DISTINCT leg_id FROM leg_team_state")}
    unfinished = {
        row[0] for row in seeded.execute("SELECT id FROM legs WHERE winner_team_id IS NULL")
    }
    assert cached == unfinished
    assert cached, "the fixture must leave at least one leg in progress"
    assert seeded.execute("SELECT count(*) FROM cricket_leg_state").fetchone()[0] > 0, (
        "an unfinished cricket leg must carry its marks"
    )


def test_the_seed_is_visible_through_the_views(seeded: sqlite3.Connection) -> None:
    """build() installs the views, so #19 can query the fixture as it will ship."""
    assert (
        seeded.execute("SELECT count(*) FROM v_darts").fetchone()[0]
        == (seeded.execute("SELECT count(*) FROM darts").fetchone()[0])
    )
    assert (
        seeded.execute("SELECT count(*) FROM v_visits").fetchone()[0]
        == (seeded.execute("SELECT count(*) FROM visits").fetchone()[0])
    )
