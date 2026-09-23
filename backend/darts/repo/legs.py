"""Legs of a match.

A leg is created with its starting team and nothing else; `winner_team_id` and
`completed_at` stay NULL until the leg is played out, which belongs to #15's
play path. `create_match` uses `create_leg` for leg 0 and is the only caller so
far -- legs 1 and up are opened when the previous one finishes, and that is
also #15's.

`starting_team_id` is a real team id, not a team index. The schema's composite
foreign key `(starting_team_id, match_id) -> teams(id, match_id)` then refuses
a leg that starts with a team from a different match, which an index could not
express.
"""

import sqlite3
from dataclasses import dataclass

from darts.repo.errors import NotFoundError
from darts.repo.rowids import new_id

_SELECT = (
    "SELECT id, match_id, leg_index, starting_team_id, winner_team_id, started_at, completed_at "
    "FROM legs"
)


@dataclass(frozen=True, slots=True)
class Leg:
    """One row of `legs`. A leg in progress has no winner and no completion."""

    id: int
    match_id: int
    leg_index: int
    starting_team_id: int
    winner_team_id: int | None
    started_at: str
    completed_at: str | None


def _row_to_leg(row: sqlite3.Row) -> Leg:
    return Leg(
        id=row["id"],
        match_id=row["match_id"],
        leg_index=row["leg_index"],
        starting_team_id=row["starting_team_id"],
        winner_team_id=row["winner_team_id"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def create_leg(
    conn: sqlite3.Connection, *, match_id: int, leg_index: int, starting_team_id: int
) -> int:
    """Open a leg and return its assigned id."""
    cursor = conn.execute(
        "INSERT INTO legs(match_id, leg_index, starting_team_id) VALUES (?, ?, ?)",
        (match_id, leg_index, starting_team_id),
    )
    return new_id(cursor)


def get_leg(conn: sqlite3.Connection, leg_id: int) -> Leg:
    """One leg by id. Raises `NotFoundError` if absent."""
    row = conn.execute(f"{_SELECT} WHERE id = ?", (leg_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"no leg with id {leg_id}")
    return _row_to_leg(row)


def legs_for_match(conn: sqlite3.Connection, match_id: int) -> list[Leg]:
    """Every leg of a match in playing order."""
    rows = conn.execute(f"{_SELECT} WHERE match_id = ? ORDER BY leg_index", (match_id,))
    return [_row_to_leg(row) for row in rows]
