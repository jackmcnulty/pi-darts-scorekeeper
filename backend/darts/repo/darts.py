"""The dart ledger: append one, and read a leg's back in throwing order.

`darts` is the append-only record every other number in the app is derived
from. A row here is never updated -- an undo removes darts, it does not
rewrite them -- so this module writes and reads and offers nothing else.

`seq_in_leg` is the leg's own throw counter and `UNIQUE (leg_id, seq_in_leg)`
makes it a total order, which is what `darts_for_leg` sorts on. Ordering by
`thrown_at` would not do: the schema stores it to millisecond precision and two
darts of the same visit can share a timestamp.

A dart belongs to a visit, and opening a visit is part of the play path, so
`visit_id` is supplied by the caller. Creating visits, and writing the cricket
effect and point-event rows that accompany a cricket dart, land with #15.
"""

import sqlite3
from dataclasses import dataclass

from darts.repo.errors import NotFoundError
from darts.repo.rowids import new_id

_COLUMNS = (
    "visit_id",
    "leg_id",
    "team_id",
    "player_id",
    "seq_in_leg",
    "dart_index",
    "segment",
    "multiplier",
    "counted",
    "caused_bust",
    "was_checkout_attempt",
    "client_dart_id",
)

_SELECT = f"SELECT id, {', '.join(_COLUMNS)}, thrown_at FROM darts"


@dataclass(frozen=True, slots=True)
class NewDart:
    """A dart about to be recorded, before SQLite assigns it an id.

    `counted` means the score was applied; it is false for a dart thrown before
    an in-rule is satisfied and for every dart of a voided visit.
    `client_dart_id` is the caller's idempotency key and is unique database-wide
    -- #15 is what makes use of that, but the column is filled from the start.
    """

    visit_id: int
    leg_id: int
    team_id: int
    player_id: int
    seq_in_leg: int
    dart_index: int
    segment: int
    multiplier: int
    counted: bool
    client_dart_id: str
    caused_bust: bool = False
    was_checkout_attempt: bool = False


@dataclass(frozen=True, slots=True)
class Dart:
    """One row of `darts`, as stored."""

    id: int
    visit_id: int
    leg_id: int
    team_id: int
    player_id: int
    seq_in_leg: int
    dart_index: int
    segment: int
    multiplier: int
    counted: bool
    caused_bust: bool
    was_checkout_attempt: bool
    client_dart_id: str
    thrown_at: str

    @property
    def score(self) -> int:
        """The raw board value. What it was *worth* depends on `counted`."""
        return self.segment * self.multiplier


def _row_to_dart(row: sqlite3.Row) -> Dart:
    return Dart(
        id=row["id"],
        visit_id=row["visit_id"],
        leg_id=row["leg_id"],
        team_id=row["team_id"],
        player_id=row["player_id"],
        seq_in_leg=row["seq_in_leg"],
        dart_index=row["dart_index"],
        segment=row["segment"],
        multiplier=row["multiplier"],
        counted=bool(row["counted"]),
        caused_bust=bool(row["caused_bust"]),
        was_checkout_attempt=bool(row["was_checkout_attempt"]),
        client_dart_id=row["client_dart_id"],
        thrown_at=row["thrown_at"],
    )


def append_dart(conn: sqlite3.Connection, dart: NewDart) -> Dart:
    """Record a dart and return it as stored, including its assigned id."""
    placeholders = ", ".join("?" * len(_COLUMNS))
    cursor = conn.execute(
        f"INSERT INTO darts({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        tuple(getattr(dart, column) for column in _COLUMNS),
    )
    return get_dart(conn, new_id(cursor))


def get_dart(conn: sqlite3.Connection, dart_id: int) -> Dart:
    """One dart by id. Raises `NotFoundError` if absent."""
    row = conn.execute(f"{_SELECT} WHERE id = ?", (dart_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"no dart with id {dart_id}")
    return _row_to_dart(row)


def darts_for_leg(conn: sqlite3.Connection, leg_id: int) -> list[Dart]:
    """Every dart of a leg in `seq_in_leg` order, first throw first."""
    rows = conn.execute(f"{_SELECT} WHERE leg_id = ? ORDER BY seq_in_leg", (leg_id,))
    return [_row_to_dart(row) for row in rows]
