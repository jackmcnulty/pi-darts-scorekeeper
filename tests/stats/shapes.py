"""Writing the same darts into different team shapes.

The sharpest criterion in #19 is that a 2v2 match and four solo matches
*containing identical darts* produce identical per-player statistics. Proving
it needs the two arrangements built from one description, so that the darts are
demonstrably the same and only the team structure differs -- which is exactly
what this module is: one visit script, two ways of seating it.

Rows are written directly rather than played through the engine, deliberately.
Driving the engine would make each shape score its own way (a 2v2 team shares a
remaining; four solo players do not), and the darts would stop being identical
-- which is the one thing the test needs them to be.

A plain module rather than conftest.py, following `seed.py` and `bulk.py`.
"""

import sqlite3
from dataclasses import dataclass

from darts.db.connection import connect
from darts.db.migrate import migrate
from darts.db.views import install_views
from darts.engine.throws import Throw

#: Four players' visits, in the order a 2v2 would throw them: the teams
#: alternate, and the members within a team alternate between rounds.
#:
#: Four rounds each, so every player throws twelve darts and their first nine
#: are a strict subset -- without that the first-nine average would equal the
#: 3-dart average and the test would pass whichever darts it counted. The fourth
#: round is deliberately unlike the first three for the same reason.
SCRIPT: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, ("T20", "T20", "T20")),
    (2, ("T19", "T19", "19")),
    (3, ("T20", "T5", "1")),
    (4, ("T18", "18", "MISS")),
    (1, ("20", "20", "5")),
    (2, ("T20", "T20", "T19")),
    (3, ("T17", "17", "D3")),
    (4, ("T20", "20", "1")),
    (1, ("T19", "T19", "D12")),
    (2, ("25", "BULL", "MISS")),
    (3, ("T20", "T20", "5")),
    (4, ("19", "19", "19")),
    (1, ("1", "1", "1")),
    (2, ("T20", "T20", "T20")),
    (3, ("T20", "T20", "T20")),
    (4, ("T20", "T20", "T20")),
)

#: Which of the four players are partners in the 2v2 arrangement.
TEAMS_2V2: tuple[tuple[int, ...], ...] = ((1, 3), (2, 4))

_STAMP = "2026-03-01T12:00:00.000Z"


@dataclass(frozen=True, slots=True)
class _Cursor:
    """Ids derived from the match, so two databases can be compared by player."""

    match_id: int

    def team(self, index: int) -> int:
        return self.match_id * 100 + index

    @property
    def leg(self) -> int:
        return self.match_id * 1000


def _database(path: str) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    install_views(conn)
    for player_id, name in enumerate(("Ana", "Ben", "Cal", "Dee", "Spare"), start=1):
        conn.execute(
            "INSERT INTO players(id, display_name, created_at) VALUES (?, ?, ?)",
            (player_id, name, _STAMP),
        )
    return conn


def _write(
    conn: sqlite3.Connection,
    match_id: int,
    teams: tuple[tuple[int, ...], ...],
    script: tuple[tuple[int, tuple[str, ...]], ...],
) -> None:
    """One leg in which `script` is thrown, seated in `teams`.

    The running score is bookkeeping only. It starts high enough that a 2v2
    team -- which shares one remaining across two players' visits -- still never
    reaches 0, so no leg here is won by a checkout and the checkout metrics stay
    out of a comparison that is about scoring. Nothing asserts on it; the stats
    read `score_after = 0` only to recognise a checkout, and there is none.
    """
    cursor = _Cursor(match_id)
    of_player = {player: index for index, members in enumerate(teams) for player in members}

    conn.execute(
        """INSERT INTO matches(id, config_json, game_type, start_score, in_rule, out_rule,
                               best_of, created_at)
           VALUES (?, '{"game_type": "x01", "best_of": 3, "start_score": 3001}',
                   'x01', 3001, 'straight', 'double', 3, ?)""",
        (match_id, _STAMP),
    )
    for index, members in enumerate(teams):
        conn.execute(
            "INSERT INTO teams(id, match_id, team_index, is_solo) VALUES (?, ?, ?, ?)",
            (cursor.team(index), match_id, index, int(len(members) == 1)),
        )
        for member_index, player_id in enumerate(members):
            conn.execute(
                "INSERT INTO team_members VALUES (?, ?, ?)",
                (cursor.team(index), player_id, member_index),
            )
    conn.execute(
        """INSERT INTO legs(id, match_id, leg_index, starting_team_id, started_at)
           VALUES (?, ?, 0, ?, ?)""",
        (cursor.leg, match_id, cursor.team(0), _STAMP),
    )

    remaining = [3001] * len(teams)
    team_visits = [0] * len(teams)
    seq = 0
    for visit_index, (player_id, labels) in enumerate(script):
        team_index = of_player[player_id]
        throws = tuple(Throw.parse(label) for label in labels)
        scored = sum(throw.segment * throw.multiplier for throw in throws)
        before = remaining[team_index]
        after = before - scored
        remaining[team_index] = after
        visit_id = cursor.leg * 100 + visit_index
        conn.execute(
            """INSERT INTO visits(id, leg_id, match_id, team_id, player_id, visit_index,
                                  team_visit_index, score_before, score_after, is_bust, is_complete)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)""",
            (
                visit_id,
                cursor.leg,
                match_id,
                cursor.team(team_index),
                player_id,
                visit_index,
                team_visits[team_index],
                before,
                after,
            ),
        )
        team_visits[team_index] += 1
        for dart_index, throw in enumerate(throws):
            conn.execute(
                """INSERT INTO darts(id, visit_id, leg_id, team_id, player_id, seq_in_leg,
                                     dart_index, segment, multiplier, counted, caused_bust,
                                     was_checkout_attempt, client_dart_id, thrown_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?)""",
                (
                    visit_id * 10 + dart_index,
                    visit_id,
                    cursor.leg,
                    cursor.team(team_index),
                    player_id,
                    seq,
                    dart_index,
                    throw.segment,
                    throw.multiplier,
                    f"shape:m{match_id}:d{seq}",
                    _STAMP,
                ),
            )
            seq += 1


def _win(conn: sqlite3.Connection, match_id: int, team_index: int) -> None:
    """Award the leg and the match to one team, without a checkout dart.

    Legs and matches won are the metrics the identical-darts criterion cannot
    cover, so the test needs them decided -- and decided without pretending a
    dart finished a leg it did not.
    """
    cursor = _Cursor(match_id)
    conn.execute(
        "UPDATE legs SET winner_team_id = ?, completed_at = ? WHERE id = ?",
        (cursor.team(team_index), _STAMP, cursor.leg),
    )
    conn.execute(
        "UPDATE matches SET winner_team_id = ?, completed_at = ? WHERE id = ?",
        (cursor.team(team_index), _STAMP, match_id),
    )


def two_versus_two(path: str) -> sqlite3.Connection:
    """`SCRIPT` as one 2v2 match: Ana with Cal, Ben with Dee. Ana's team wins."""
    conn = _database(path)
    with conn:
        _write(conn, 1, TEAMS_2V2, SCRIPT)
        _win(conn, 1, 0)
    return conn


def four_solo(path: str) -> sqlite3.Connection:
    """The same darts as four solo matches, one per player.

    Each player throws their own visits, in their own order, against a spare
    opponent who throws nothing -- so the dart rows attributed to each of the
    four are identical to the 2v2's, and nothing else is.
    """
    conn = _database(path)
    with conn:
        for player_id in (1, 2, 3, 4):
            own = tuple(visit for visit in SCRIPT if visit[0] == player_id)
            _write(conn, player_id, ((player_id,), (5,)), own)
            _win(conn, player_id, 0)
    return conn
