"""The two disposable replay caches, written and cleared as a single unit.

`leg_team_state` and `cricket_leg_state` are not a second source of truth. They
exist so an interrupted leg can be resumed without reading its darts, and every
value in them is recomputed from `darts` by `darts.services.play.rebuild_caches`.
Nothing in the schema references them, so they can be deleted and rebuilt at any
time -- which is exactly what `write_leg_state` does.

The two tables are one cache in two pieces: `cricket_leg_state` is keyed on
`(leg_id, team_id)` into `leg_team_state` and cascades from it, so clearing the
parent clears the marks too and no module outside this one needs to know that.

`marks` is None for an x01 leg and a target -> count mapping for a cricket one,
mirroring `remaining` and `is_open`, which are NULL for cricket. A leg is one
game type throughout, so a caller never mixes the two.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TeamCache:
    """One team's cached position in one leg.

    `darts_thrown` counts every dart the team actually threw, including darts
    voided by a bust and cricket marks that paid nobody. `points` is cricket's
    and stays 0 for x01, which is what the seed fixture stores.
    """

    team_id: int
    remaining: int | None
    is_open: bool | None
    darts_thrown: int
    points: int
    marks: Mapping[int, int] | None = None


def clear_leg_state(conn: sqlite3.Connection, leg_id: int) -> None:
    """Drop a leg's cache rows, marks included by cascade."""
    conn.execute("DELETE FROM leg_team_state WHERE leg_id = ?", (leg_id,))


def write_leg_state(
    conn: sqlite3.Connection, leg_id: int, match_id: int, teams: Sequence[TeamCache]
) -> None:
    """Replace a leg's cache with `teams`.

    A wholesale replace rather than an upsert: the cache is derived, so the
    rows that should not be there are as much a part of the answer as the
    values of the rows that should.
    """
    clear_leg_state(conn, leg_id)
    for team in teams:
        conn.execute(
            "INSERT INTO leg_team_state(leg_id, team_id, match_id, remaining, is_open, "
            "darts_thrown, points) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                leg_id,
                team.team_id,
                match_id,
                team.remaining,
                None if team.is_open is None else int(team.is_open),
                team.darts_thrown,
                team.points,
            ),
        )
        if team.marks is None:
            continue
        conn.executemany(
            "INSERT INTO cricket_leg_state(leg_id, team_id, target, marks) VALUES (?, ?, ?, ?)",
            [(leg_id, team.team_id, target, marks) for target, marks in team.marks.items()],
        )


def leg_state_for(conn: sqlite3.Connection, leg_id: int) -> list[TeamCache]:
    """A leg's cache as stored, in `team_id` order. Empty for an uncached leg."""
    marks: dict[int, dict[int, int]] = {}
    for row in conn.execute(
        "SELECT team_id, target, marks FROM cricket_leg_state WHERE leg_id = ?", (leg_id,)
    ):
        marks.setdefault(row["team_id"], {})[row["target"]] = row["marks"]
    rows = conn.execute(
        "SELECT team_id, remaining, is_open, darts_thrown, points FROM leg_team_state "
        "WHERE leg_id = ? ORDER BY team_id",
        (leg_id,),
    )
    return [
        TeamCache(
            team_id=row["team_id"],
            remaining=row["remaining"],
            is_open=None if row["is_open"] is None else bool(row["is_open"]),
            darts_thrown=row["darts_thrown"],
            points=row["points"],
            marks=marks.get(row["team_id"]),
        )
        for row in rows
    ]
