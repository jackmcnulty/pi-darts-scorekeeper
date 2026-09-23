"""Players, and the archive flag that retires one without erasing them.

Archiving is a flag and never a delete. `players` is referenced by
`team_members`, and through it by every visit and every dart, so removing a row
would take a player's whole history with it. An archived player disappears from
`list_players` -- which is what the pickers call -- and stays in every match
they ever played.

Names are unique among *active* players, compared with surrounding whitespace
stripped and case folded, so "Ana" and " ana " are the same person. Archiving
releases the name: once Ana has retired, a new Ana may be added. The schema
does not enforce any of this (`display_name` is merely non-blank), because the
rule is a product decision about what the picker should look like rather than
an integrity constraint.

The comparison is done in Python rather than in SQL because SQLite's `lower()`
and `NOCASE` collation fold ASCII only, and would call "JOSE" and "JOSÉ"
different names while calling them the same for an ASCII name. A household's
worth of players is a handful of rows, so the loop costs nothing.
"""

import sqlite3
from dataclasses import dataclass

from darts.repo.errors import DuplicateNameError, NotFoundError
from darts.repo.rowids import new_id

_SELECT = "SELECT id, display_name, is_archived, created_at FROM players"


@dataclass(frozen=True, slots=True)
class Player:
    """One row of `players`. `created_at` is the schema's own UTC timestamp."""

    id: int
    display_name: str
    is_archived: bool
    created_at: str


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(
        id=row["id"],
        display_name=row["display_name"],
        is_archived=bool(row["is_archived"]),
        created_at=row["created_at"],
    )


def _normalise(display_name: str) -> str:
    """The form two names are compared in: trimmed and case folded."""
    return display_name.strip().casefold()


def _clean(display_name: str) -> str:
    """The form a name is stored in, rejecting one that is blank or spaces."""
    cleaned = display_name.strip()
    if not cleaned:
        raise ValueError("display_name cannot be blank")
    return cleaned


def _reject_duplicate(
    conn: sqlite3.Connection, display_name: str, *, exclude_id: int | None = None
) -> None:
    """Raise if an active player other than `exclude_id` already has the name."""
    wanted = _normalise(display_name)
    for row in conn.execute("SELECT id, display_name FROM players WHERE is_archived = 0"):
        if row["id"] != exclude_id and _normalise(row["display_name"]) == wanted:
            raise DuplicateNameError(f"{display_name!r} is already taken by player {row['id']}")


def get_player(conn: sqlite3.Connection, player_id: int) -> Player:
    """Any player by id, archived or not. Raises `NotFoundError` if absent."""
    row = conn.execute(f"{_SELECT} WHERE id = ?", (player_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"no player with id {player_id}")
    return _row_to_player(row)


def list_players(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[Player]:
    """Active players by name, or everybody when `include_archived` is set.

    The default is what a picker wants; the management screen passes the flag.
    Ordering is by name so the list is stable whatever order players were added
    in, and archived players sort among the rest rather than in a block.
    """
    where = "" if include_archived else " WHERE is_archived = 0"
    rows = conn.execute(f"{_SELECT}{where} ORDER BY display_name COLLATE NOCASE, id")
    return [_row_to_player(row) for row in rows]


def create_player(conn: sqlite3.Connection, display_name: str) -> Player:
    """Add an active player. The id is assigned by SQLite."""
    cleaned = _clean(display_name)
    _reject_duplicate(conn, cleaned)
    cursor = conn.execute("INSERT INTO players(display_name) VALUES (?)", (cleaned,))
    return get_player(conn, new_id(cursor))


def update_player(conn: sqlite3.Connection, player_id: int, *, display_name: str) -> Player:
    """Rename a player. The name is the only editable field.

    Renaming to your own name in different case or spacing is allowed, which is
    how a typo gets fixed; the player being renamed is excluded from the
    duplicate check.
    """
    cleaned = _clean(display_name)
    get_player(conn, player_id)
    _reject_duplicate(conn, cleaned, exclude_id=player_id)
    conn.execute("UPDATE players SET display_name = ? WHERE id = ?", (cleaned, player_id))
    return get_player(conn, player_id)


def archive_player(conn: sqlite3.Connection, player_id: int) -> Player:
    """Retire a player from the pickers, keeping every row they appear in.

    Archiving an already archived player is a no-op rather than an error, so a
    repeated tap on the management screen cannot fail.
    """
    get_player(conn, player_id)
    conn.execute("UPDATE players SET is_archived = 1 WHERE id = ?", (player_id,))
    return get_player(conn, player_id)


def unarchive_player(conn: sqlite3.Connection, player_id: int) -> Player:
    """Bring a player back, if their name is still free.

    Archiving releases a name, so somebody else may hold it by now. Restoring
    the player anyway would put two identical entries in the picker, which is
    the thing the uniqueness rule exists to prevent, so this raises instead.
    """
    player = get_player(conn, player_id)
    _reject_duplicate(conn, player.display_name, exclude_id=player_id)
    conn.execute("UPDATE players SET is_archived = 0 WHERE id = ?", (player_id,))
    return get_player(conn, player_id)
