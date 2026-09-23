"""A large synthetic database, for the query-plan and timing criteria of #19.

This is not `seed.py` and does not try to be. `seed.py` drives the real engine
so that every flag is whatever the rules produced, which is what makes it worth
computing golden values against -- and which is also why it is small. What the
performance criteria need is the opposite: fifty thousand darts spread over
enough players, matches and legs that a full scan is visibly different from an
index seek, and nothing else.

So the rows here are written directly and the scoring is arithmetic rather than
played. Every CHECK in 0001 is still satisfied -- a synthetic row that the
schema would reject would make the timings meaningless -- but no claim is made
that these legs could have happened. Nothing asserts a value against this data;
the assertions are about query plans and wall-clock.

A plain module rather than conftest.py, following `seed.py` and
`tests/db/dbfixtures.py`: it is imported directly from several test
directories and must not need pytest.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from darts.db.connection import connect
from darts.db.migrate import migrate
from darts.db.views import install_views

#: Enough players that a per-player seek is a small slice of the table, and an
#: odd number so players meet in changing pairs rather than fixed rivalries.
PLAYERS: tuple[str, ...] = tuple(f"Bulk {index}" for index in range(1, 12))

#: The same fixed epoch idea as seed.py: no wall clock, so the data is the same
#: on every machine and `since` filters have something deterministic to cut.
EPOCH = datetime(2026, 2, 1, tzinfo=UTC)

#: Visit totals cycled through in order, chosen to populate every band and to
#: leave a bust (0) and some small visits in the mix.
_VISIT_SCORES: tuple[int, ...] = (180, 140, 100, 85, 60, 45, 26, 0, 140, 100, 60, 41)

#: (segment, multiplier) triples that sum to the visit totals above, so the
#: darts and the visit agree and segment frequency has a real distribution.
_DART_SHAPES: dict[int, tuple[tuple[int, int], ...]] = {
    180: ((20, 3), (20, 3), (20, 3)),
    140: ((20, 3), (20, 3), (20, 1)),
    100: ((20, 3), (20, 1), (20, 1)),
    85: ((19, 3), (20, 1), (8, 1)),
    60: ((20, 1), (20, 1), (20, 1)),
    45: ((15, 3), (0, 0), (0, 0)),
    41: ((9, 1), (16, 2), (0, 0)),
    26: ((20, 1), (3, 1), (3, 1)),
    0: ((0, 0), (0, 0), (0, 0)),
}


@dataclass(frozen=True, slots=True)
class BulkSize:
    """How much was written, so a test can assert it got what it asked for."""

    matches: int
    legs: int
    visits: int
    darts: int


def _stamp(seconds: int) -> str:
    return (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _rows(target_darts: int) -> tuple[list[tuple[object, ...]], ...]:
    """Every row of the synthetic dataset, built in memory before a single insert.

    One pass that appends to six lists, so the ids are obviously consistent
    across the tables and the whole thing goes in through executemany.
    """
    matches: list[tuple[object, ...]] = []
    teams: list[tuple[object, ...]] = []
    members: list[tuple[object, ...]] = []
    legs: list[tuple[object, ...]] = []
    visits: list[tuple[object, ...]] = []
    darts: list[tuple[object, ...]] = []
    effects: list[tuple[object, ...]] = []

    match_id = 0
    while len(darts) < target_darts:
        match_id += 1
        # Cricket every fifth match, so the cricket family has real work to do
        # and the x01 queries have something they must exclude.
        cricket = match_id % 5 == 0
        left = PLAYERS[match_id % len(PLAYERS)]
        right = PLAYERS[(match_id * 3 + 1) % len(PLAYERS)]
        if left == right:
            right = PLAYERS[(match_id * 3 + 2) % len(PLAYERS)]
        created = _stamp(match_id * 600)

        matches.append(
            (
                match_id,
                '{"game_type": "cricket", "best_of": 3, "variant": "standard"}'
                if cricket
                else '{"game_type": "x01", "best_of": 3, "start_score": 501}',
                "cricket" if cricket else "x01",
                "standard" if cricket else None,
                None if cricket else 501,
                None if cricket else "straight",
                None if cricket else "double",
                3,
                created,
            )
        )
        for team_index, name in enumerate((left, right)):
            team_id = match_id * 10 + team_index
            teams.append((team_id, match_id, team_index, None, 1))
            members.append((team_id, PLAYERS.index(name) + 1, 0))

        for leg_index in range(2):
            leg_id = match_id * 100 + leg_index
            legs.append((leg_id, match_id, leg_index, match_id * 10, _stamp(match_id * 600 + 60)))
            remaining = [501, 501]
            for visit_index in range(14):
                team_index = (leg_index + visit_index) % 2
                team_id = match_id * 10 + team_index
                player_id = PLAYERS.index(left if team_index == 0 else right) + 1
                visit_id = leg_id * 100 + visit_index
                scored = _VISIT_SCORES[(visit_index + match_id) % len(_VISIT_SCORES)]
                before = remaining[team_index]
                if cricket:
                    # Cricket's score columns hold the thrower's points, which
                    # only ever go up.
                    after = before + scored
                else:
                    # Never push a remaining below zero: the CHECK forbids it,
                    # and a synthetic leg that "wins" without a real checkout
                    # would only confuse the checkout queries.
                    scored = min(scored, max(before - 2, 0))
                    after = before - scored
                remaining[team_index] = after
                visits.append(
                    (
                        visit_id,
                        leg_id,
                        match_id,
                        team_id,
                        player_id,
                        visit_index,
                        visit_index // 2,
                        before,
                        after,
                        0,
                        1,
                    )
                )
                shape = _DART_SHAPES.get(scored, ((0, 0), (0, 0), (0, 0)))
                for dart_index, (segment, multiplier) in enumerate(shape):
                    seq = visit_index * 3 + dart_index
                    dart_id = visit_id * 10 + dart_index
                    darts.append(
                        (
                            dart_id,
                            visit_id,
                            leg_id,
                            team_id,
                            player_id,
                            seq,
                            dart_index,
                            segment,
                            multiplier,
                            1,
                            0,
                            # A dart thrown at a two-figure remaining is an
                            # attempt often enough to give the checkout query
                            # a populated denominator.
                            1 if 2 <= after <= 40 and dart_index == 2 else 0,
                            f"bulk:m{match_id}:l{leg_index}:d{seq}",
                            _stamp(match_id * 600 + 60 + seq),
                        )
                    )
                    if cricket:
                        effects.append(_effect(dart_id, segment, multiplier))
    return matches, teams, members, legs, visits, darts, effects


def _effect(dart_id: int, segment: int, multiplier: int) -> tuple[object, ...]:
    """The cricket_dart_effects row a synthetic cricket dart implies.

    Marks are counted, never surplus, so every CHECK on the table holds without
    a second rule: the bull caps at two marks and an untargeted dart records a
    NULL target with nothing on it.
    """
    if segment in (15, 16, 17, 18, 19, 20):
        return (dart_id, segment, multiplier, 0, 0)
    if segment == 25:
        return (dart_id, 25, min(multiplier, 2), 0, 0)
    return (dart_id, None, 0, 0, 0)


def seed(conn: sqlite3.Connection, target_darts: int = 50_000) -> BulkSize:
    """Write at least `target_darts` darts into an already-migrated database."""
    matches, teams, members, legs, visits, darts, effects = _rows(target_darts)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            "INSERT INTO players(id, display_name, created_at) VALUES (?, ?, ?)",
            [(index, name, _stamp(index)) for index, name in enumerate(PLAYERS, start=1)],
        )
        conn.executemany(
            """INSERT INTO matches(id, config_json, game_type, variant, start_score,
                                   in_rule, out_rule, best_of, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            matches,
        )
        conn.executemany("INSERT INTO teams VALUES (?, ?, ?, ?, ?)", teams)
        conn.executemany("INSERT INTO team_members VALUES (?, ?, ?)", members)
        conn.executemany(
            """INSERT INTO legs(id, match_id, leg_index, starting_team_id, started_at)
               VALUES (?, ?, ?, ?, ?)""",
            legs,
        )
        conn.executemany("INSERT INTO visits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", visits)
        conn.executemany(
            """INSERT INTO darts(id, visit_id, leg_id, team_id, player_id, seq_in_leg,
                                 dart_index, segment, multiplier, counted, caused_bust,
                                 was_checkout_attempt, client_dart_id, thrown_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            darts,
        )
        conn.executemany("INSERT INTO cricket_dart_effects VALUES (?, ?, ?, ?, ?)", effects)
        # Award every leg to the team that got lowest, and every match to the
        # team that took both legs, so the win and checkout queries have
        # non-empty numerators to find.
        conn.execute(
            """UPDATE legs SET winner_team_id = match_id * 10, completed_at = started_at
               WHERE leg_index = 0"""
        )
        conn.execute(
            """UPDATE matches SET winner_team_id = id * 10, completed_at = created_at
               WHERE id % 3 = 0"""
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return BulkSize(len(matches), len(legs), len(visits), len(darts))


def build(path: Path, target_darts: int = 50_000) -> tuple[sqlite3.Connection, BulkSize]:
    """Create, migrate, install views into and bulk-fill a database at `path`."""
    conn = connect(path)
    try:
        migrate(conn)
        install_views(conn)
        size = seed(conn, target_darts)
        # The planner has real statistics on a real Pi, where this database has
        # been written to over months. Without ANALYZE the plans measured here
        # would be the ones SQLite guesses for an unknown table, which is not
        # the situation #19's criterion is about.
        conn.execute("ANALYZE")
    except BaseException:
        conn.close()
        raise
    return conn, size
