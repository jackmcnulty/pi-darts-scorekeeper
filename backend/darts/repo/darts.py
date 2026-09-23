"""The dart ledger, and the cricket rows that hang off a single dart.

`darts` is the record every other number in the app is derived from. What a
dart *was* -- its segment, its multiplier, the visit it belongs to -- is never
rewritten; an undo removes darts, it does not edit them. `set_counted` is the
one exception and it changes a verdict, not a fact: an x01 bust voids every
dart already thrown in that visit, and those rows were written before the
offending dart existed. `docs/data-model.md` calls for exactly that update.

`seq_in_leg` is the leg's own throw counter and `UNIQUE (leg_id, seq_in_leg)`
makes it a total order, which is what `darts_for_leg` sorts on. Ordering by
`thrown_at` would not do: the schema stores it to millisecond precision and two
darts of the same visit can share a timestamp.

A dart belongs to a visit, and `visit_id` is supplied by the caller; see
`darts.repo.visits`.

`cricket_dart_effects` and `cricket_point_events` live here rather than in a
module of their own because both are keyed on a dart: an effect row is what one
cricket dart did to the board, and a point event is what it paid one team. Both
cascade when their dart is deleted, so neither has a delete of its own -- undo
gets them for free.
"""

import sqlite3
from collections.abc import Sequence
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


def dart_for_client_id(conn: sqlite3.Connection, client_dart_id: str) -> Dart | None:
    """The dart already recorded under this idempotency key, or None.

    `client_dart_id` is unique database-wide, so this is deliberately not scoped
    to a leg: a key that turns up on another leg is a client bug worth
    reporting, and it cannot be reported by a query that never looks.
    """
    row = conn.execute(f"{_SELECT} WHERE client_dart_id = ?", (client_dart_id,)).fetchone()
    return None if row is None else _row_to_dart(row)


def set_counted(conn: sqlite3.Connection, dart_id: int, counted: bool) -> None:
    """Change whether a recorded dart's score applies. Raises if absent.

    Only a bust needs this: the darts that preceded the offending one were
    counted when they were written and are voided by what came after.
    """
    cursor = conn.execute("UPDATE darts SET counted = ? WHERE id = ?", (int(counted), dart_id))
    if cursor.rowcount == 0:
        raise NotFoundError(f"no dart with id {dart_id}")


def delete_dart(conn: sqlite3.Connection, dart_id: int) -> None:
    """Remove a dart and, by cascade, its cricket effect and point events.

    Raises `NotFoundError` if absent. The visit is left behind: nothing in the
    schema removes it, so undoing the only dart of a visit is two steps.
    """
    cursor = conn.execute("DELETE FROM darts WHERE id = ?", (dart_id,))
    if cursor.rowcount == 0:
        raise NotFoundError(f"no dart with id {dart_id}")


@dataclass(frozen=True, slots=True)
class CricketEffect:
    """What one cricket dart did to the board.

    Every cricket dart gets one of these, including a dart that hit no target
    at all -- that is a `target` of None with everything else zero, which the
    schema requires and which is how a missed cricket dart is told apart from a
    dart that was never recorded.
    """

    dart_id: int
    target: int | None
    counted_marks: int
    surplus_marks: int
    wasted: bool


@dataclass(frozen=True, slots=True)
class PointAward:
    """Points one cricket dart gave one team.

    `recipient_team_id` is a real team id, not the engine's opponent index:
    `standard` awards the throwing team, `cutthroat` awards opponents, `quick`
    awards nobody, and the stored row should not need the variant to be read.
    """

    dart_id: int
    leg_id: int
    match_id: int
    recipient_team_id: int
    points: int


def record_cricket_effect(conn: sqlite3.Connection, effect: CricketEffect) -> None:
    """Write the effect row for one cricket dart."""
    conn.execute(
        "INSERT INTO cricket_dart_effects(dart_id, target, counted_marks, surplus_marks, wasted) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            effect.dart_id,
            effect.target,
            effect.counted_marks,
            effect.surplus_marks,
            int(effect.wasted),
        ),
    )


def record_point_awards(conn: sqlite3.Connection, awards: Sequence[PointAward]) -> None:
    """Write every point event one cricket dart produced. An empty sequence writes nothing."""
    conn.executemany(
        "INSERT INTO cricket_point_events(dart_id, leg_id, match_id, recipient_team_id, points) "
        "VALUES (?, ?, ?, ?, ?)",
        [(a.dart_id, a.leg_id, a.match_id, a.recipient_team_id, a.points) for a in awards],
    )


def cricket_effects_for_leg(conn: sqlite3.Connection, leg_id: int) -> dict[int, CricketEffect]:
    """A leg's effect rows keyed by dart id, for replay comparison."""
    rows = conn.execute(
        "SELECT e.dart_id, e.target, e.counted_marks, e.surplus_marks, e.wasted "
        "FROM cricket_dart_effects e JOIN darts d ON d.id = e.dart_id WHERE d.leg_id = ?",
        (leg_id,),
    )
    return {
        row["dart_id"]: CricketEffect(
            dart_id=row["dart_id"],
            target=row["target"],
            counted_marks=row["counted_marks"],
            surplus_marks=row["surplus_marks"],
            wasted=bool(row["wasted"]),
        )
        for row in rows
    }


def point_awards_for_leg(conn: sqlite3.Connection, leg_id: int) -> list[PointAward]:
    """A leg's point events, ordered by the dart that produced them."""
    rows = conn.execute(
        "SELECT e.dart_id, e.leg_id, e.match_id, e.recipient_team_id, e.points "
        "FROM cricket_point_events e JOIN darts d ON d.id = e.dart_id "
        "WHERE e.leg_id = ? ORDER BY d.seq_in_leg, e.recipient_team_id",
        (leg_id,),
    )
    return [
        PointAward(
            dart_id=row["dart_id"],
            leg_id=row["leg_id"],
            match_id=row["match_id"],
            recipient_team_id=row["recipient_team_id"],
            points=row["points"],
        )
        for row in rows
    ]
