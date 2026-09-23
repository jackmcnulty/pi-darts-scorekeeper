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


def set_leg_winner(conn: sqlite3.Connection, leg_id: int, winner_team_id: int | None) -> None:
    """Record or withdraw a leg's winner, moving `completed_at` with it.

    The two fields say the same thing twice, so they are written together and
    never apart: a winner sets the completion time, and `None` -- which is what
    undoing a winning dart needs -- clears both. The timestamp comes from
    SQLite so that it is formatted exactly as the column's own default is.

    Raises `NotFoundError` if the leg does not exist.
    """
    cursor = conn.execute(
        "UPDATE legs SET winner_team_id = ?, completed_at = CASE WHEN ? IS NULL THEN NULL "
        "ELSE strftime('%Y-%m-%dT%H:%M:%fZ', 'now') END WHERE id = ?",
        (winner_team_id, winner_team_id, leg_id),
    )
    if cursor.rowcount == 0:
        raise NotFoundError(f"no leg with id {leg_id}")


def delete_leg(conn: sqlite3.Connection, leg_id: int) -> None:
    """Remove a leg, and by cascade everything recorded in it.

    Undo is the only caller: completing a leg opens the next one, so undoing
    the dart that completed it has to close that one again. Raises
    `NotFoundError` if absent.
    """
    cursor = conn.execute("DELETE FROM legs WHERE id = ?", (leg_id,))
    if cursor.rowcount == 0:
        raise NotFoundError(f"no leg with id {leg_id}")
