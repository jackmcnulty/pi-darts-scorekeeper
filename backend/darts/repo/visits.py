"""Visits: the container a dart is thrown into.

A visit is opened by its first dart and grows as the next two land, so unlike
`darts` these rows really are updated. `score_before` is fixed when the visit
opens; `score_after`, `is_bust` and `is_complete` are whatever the engine says
after the most recent dart, which is why `update_visit` takes all three
together -- the schema's `CHECK (is_bust = 0 OR (score_after = score_before AND
is_complete = 1))` is a statement about the three of them at once, and writing
them one at a time would mean passing through states that are not true.

`score_before` and `score_after` mean x01 remaining in an x01 leg and the
throwing team's cricket points in a cricket leg. The column cannot say which,
so the service has to; see `darts.services.play`.

`delete_visit` exists for undo. Deleting a visit cascades to its darts, so it
is only ever called once the visit's last dart has already gone.
"""

import sqlite3
from dataclasses import dataclass

from darts.repo.errors import NotFoundError
from darts.repo.rowids import new_id

_COLUMNS = (
    "leg_id",
    "match_id",
    "team_id",
    "player_id",
    "visit_index",
    "team_visit_index",
    "score_before",
    "score_after",
    "is_bust",
    "is_complete",
)

_SELECT = f"SELECT id, {', '.join(_COLUMNS)} FROM visits"


@dataclass(frozen=True, slots=True)
class NewVisit:
    """A visit about to be opened, before SQLite assigns it an id.

    `visit_index` counts every visit of the leg and `team_visit_index` counts
    only this team's, which is what decides whose turn it is within a doubles
    pair. Both are the caller's to supply: they come out of the replayed leg,
    not out of a row count.
    """

    leg_id: int
    match_id: int
    team_id: int
    player_id: int
    visit_index: int
    team_visit_index: int
    score_before: int
    score_after: int
    is_bust: bool = False
    is_complete: bool = False


@dataclass(frozen=True, slots=True)
class Visit:
    """One row of `visits`, as stored."""

    id: int
    leg_id: int
    match_id: int
    team_id: int
    player_id: int
    visit_index: int
    team_visit_index: int
    score_before: int
    score_after: int
    is_bust: bool
    is_complete: bool


def _row_to_visit(row: sqlite3.Row) -> Visit:
    return Visit(
        id=row["id"],
        leg_id=row["leg_id"],
        match_id=row["match_id"],
        team_id=row["team_id"],
        player_id=row["player_id"],
        visit_index=row["visit_index"],
        team_visit_index=row["team_visit_index"],
        score_before=row["score_before"],
        score_after=row["score_after"],
        is_bust=bool(row["is_bust"]),
        is_complete=bool(row["is_complete"]),
    )


def create_visit(conn: sqlite3.Connection, visit: NewVisit) -> Visit:
    """Open a visit and return it as stored, including its assigned id."""
    placeholders = ", ".join("?" * len(_COLUMNS))
    cursor = conn.execute(
        f"INSERT INTO visits({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        tuple(getattr(visit, column) for column in _COLUMNS),
    )
    return get_visit(conn, new_id(cursor))


def get_visit(conn: sqlite3.Connection, visit_id: int) -> Visit:
    """One visit by id. Raises `NotFoundError` if absent."""
    row = conn.execute(f"{_SELECT} WHERE id = ?", (visit_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"no visit with id {visit_id}")
    return _row_to_visit(row)


def visits_for_leg(conn: sqlite3.Connection, leg_id: int) -> list[Visit]:
    """Every visit of a leg in `visit_index` order, first visit first."""
    rows = conn.execute(f"{_SELECT} WHERE leg_id = ? ORDER BY visit_index", (leg_id,))
    return [_row_to_visit(row) for row in rows]


def update_visit(
    conn: sqlite3.Connection,
    visit_id: int,
    *,
    score_after: int,
    is_bust: bool,
    is_complete: bool,
) -> Visit:
    """Rewrite the three fields a further dart can change. Raises if absent."""
    cursor = conn.execute(
        "UPDATE visits SET score_after = ?, is_bust = ?, is_complete = ? WHERE id = ?",
        (score_after, int(is_bust), int(is_complete), visit_id),
    )
    if cursor.rowcount == 0:
        raise NotFoundError(f"no visit with id {visit_id}")
    return get_visit(conn, visit_id)


def delete_visit(conn: sqlite3.Connection, visit_id: int) -> None:
    """Remove a visit, and with it any darts still in it. Raises if absent."""
    cursor = conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
    if cursor.rowcount == 0:
        raise NotFoundError(f"no visit with id {visit_id}")
