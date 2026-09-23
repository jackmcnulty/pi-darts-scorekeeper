"""Real schema rows shared by the durability tests and the SIGKILL child.

A plain module rather than conftest.py: the subprocess in test_durability.py
imports it directly, and it must not depend on pytest being in the picture.
"""

import sqlite3
from pathlib import Path

from darts.db.migrate import migrate

MATCH = 1
LEG = 100
TEAMS = (10, 20)
PLAYERS = {10: 1, 20: 2}


def scaffold(conn: sqlite3.Connection) -> None:
    """Migrate, then add one x01 match, two solo teams and one leg."""
    migrate(conn)
    conn.execute(
        """INSERT INTO matches(id, config_json, game_type, start_score, in_rule, out_rule, best_of)
           VALUES (?, '{}', 'x01', 501, 'straight', 'double', 3)""",
        (MATCH,),
    )
    for team, player in PLAYERS.items():
        conn.execute("INSERT INTO players(id, display_name) VALUES (?, ?)", (player, f"P{player}"))
        conn.execute(
            "INSERT INTO teams(id, match_id, team_index, is_solo) VALUES (?, ?, ?, 1)",
            (team, MATCH, TEAMS.index(team)),
        )
        conn.execute("INSERT INTO team_members VALUES (?, ?, 0)", (team, player))
    conn.execute(
        "INSERT INTO legs(id, match_id, leg_index, starting_team_id) VALUES (?, ?, 0, ?)",
        (LEG, MATCH, TEAMS[0]),
    )


def add_visit(conn: sqlite3.Connection, index: int) -> None:
    """A visit and its three darts, satisfying the schema's composite keys.

    Darts are inserted after their visit so a torn snapshot would show a dart
    whose visit is missing, which foreign_key_check reports.
    """
    team = TEAMS[index % 2]
    player = PLAYERS[team]
    visit = 1000 + index
    conn.execute(
        """INSERT INTO visits(id, leg_id, match_id, team_id, player_id, visit_index,
                              team_visit_index, score_before, score_after, is_complete)
           VALUES (?, ?, ?, ?, ?, ?, ?, 501, 441, 1)""",
        (visit, LEG, MATCH, team, player, index, index // 2),
    )
    for dart in range(3):
        conn.execute(
            """INSERT INTO darts(visit_id, leg_id, team_id, player_id, seq_in_leg, dart_index,
                                 segment, multiplier, counted, client_dart_id)
               VALUES (?, ?, ?, ?, ?, ?, 20, 1, 1, ?)""",
            (visit, LEG, team, player, index * 3 + dart, dart, f"dart-{index}-{dart}"),
        )


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def dump(path: Path) -> dict[str, object]:
    """Every row of every table, plus the schema version.

    #13 views, #14 repositories and #19 stats queries do not exist yet, so a
    restore is verified against the source tables statistics are derived from
    rather than against a statistics API.
    """
    with sqlite3.connect(path) as conn:
        snapshot: dict[str, object] = {
            "schema_version": conn.execute("PRAGMA user_version").fetchone()[0]
        }
        for name in table_names(conn):
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
            snapshot[name] = sorted(rows)
    conn.close()
    return snapshot
