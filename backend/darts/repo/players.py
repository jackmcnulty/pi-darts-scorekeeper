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

#22 added the two things a scoreboard needs beyond a name: a colour and a
shorter label. Both arrived in `0003_player_identity.sql` and both are nullable,
because ADD COLUMN cannot invent a per-row value and every player created before
it has neither.
"""

import sqlite3
from collections import Counter
from dataclasses import dataclass

from darts.repo.errors import DuplicateNameError, NotFoundError
from darts.repo.rowids import new_id

#: How many player accents #4's palette holds: `--accent-1` .. `--accent-8` in
#: `frontend/src/styles/tokens.css`. The colours themselves are not stored --
#: an index is -- because they were found by a colour-blindness search that #4
#: forbids hand-editing, and a second copy of them in the database would be free
#: to drift from the one the screens actually paint with.
#: `tests/db/test_accent_palette.py` fails if the two sides stop agreeing.
ACCENT_COUNT = 8
ACCENTS = range(1, ACCENT_COUNT + 1)

#: Long enough for "Bartlet", short enough to sit in a score cell beside a
#: three-digit number. Matched by the CHECK in `0003_player_identity.sql`.
SHORT_NAME_MAX = 8

_SELECT = "SELECT id, display_name, short_name, accent_index, is_archived, created_at FROM players"


class Unset:
    """The type of `UNSET`, so that mypy can narrow an argument away from it."""


#: "The caller did not mention this field."
#:
#: `update_player(..., short_name=None)` clears a short name and
#: `update_player(...)` leaves it alone. Those are different requests, and a
#: single `None` could not tell them apart -- which is what makes `PATCH` on a
#: player genuinely partial rather than a whole-record replace wearing the
#: wrong verb.
UNSET = Unset()


@dataclass(frozen=True, slots=True)
class Player:
    """One row of `players`. `created_at` is the schema's own UTC timestamp."""

    id: int
    display_name: str
    short_name: str | None
    accent_index: int | None
    is_archived: bool
    created_at: str


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(
        id=row["id"],
        display_name=row["display_name"],
        short_name=row["short_name"],
        accent_index=row["accent_index"],
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


def _clean_short_name(short_name: str | None) -> str | None:
    """The form a short name is stored in, or `None` for "there isn't one".

    A blank string clears it rather than failing: the edit sheet sends whatever
    is in the field, and an emptied field plainly means "no short name" -- not
    an error the player has to be told about. Too long *is* an error, because
    silently truncating somebody's name is worse than refusing it.
    """
    if short_name is None:
        return None
    cleaned = short_name.strip()
    if not cleaned:
        return None
    if len(cleaned) > SHORT_NAME_MAX:
        raise ValueError(f"short_name cannot exceed {SHORT_NAME_MAX} characters")
    return cleaned


def _clean_accent(accent_index: int | None) -> int | None:
    """Reject an accent outside the palette before SQLite has to.

    The CHECK in `0003_player_identity.sql` would catch it, but as an
    `IntegrityError` -- which the API reports as a 503, "the database is
    unavailable", when the truth is that the request named a colour that does
    not exist.
    """
    if accent_index is not None and accent_index not in ACCENTS:
        raise ValueError(f"accent_index must be between 1 and {ACCENT_COUNT}")
    return accent_index


def next_accent_index(conn: sqlite3.Connection) -> int:
    """The colour to offer next: the lowest-numbered one the fewest players hold.

    With eight or fewer active players that is always a free colour, which is
    what stops two players sharing one unless somebody deliberately picks a
    taken one. A ninth player has no free colour to be given -- the palette is
    eight colours chosen to stay distinguishable under protanopia, deuteranopia
    and tritanopia, and #4 forbids extending it by hand -- so the rule degrades
    to the least-used rather than refusing to add a real person. The screen is
    what says whose colour it is; this only decides which clash hurts least.

    Archived players are ignored, because they are not on any scoreboard.
    """
    held = Counter(
        row["accent_index"]
        for row in conn.execute(
            "SELECT accent_index FROM players WHERE is_archived = 0 AND accent_index IS NOT NULL"
        )
    )
    return min(ACCENTS, key=lambda index: (held[index], index))


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


def create_player(
    conn: sqlite3.Connection,
    display_name: str,
    *,
    short_name: str | None = None,
    accent_index: int | None = None,
) -> Player:
    """Add an active player. The id is assigned by SQLite.

    An unspecified colour is chosen by `next_accent_index` rather than left
    empty, so that a player added without opening the picker still arrives with
    a colour nobody else is using.
    """
    cleaned = _clean(display_name)
    short = _clean_short_name(short_name)
    _reject_duplicate(conn, cleaned)
    accent = next_accent_index(conn) if accent_index is None else _clean_accent(accent_index)
    cursor = conn.execute(
        "INSERT INTO players(display_name, short_name, accent_index) VALUES (?, ?, ?)",
        (cleaned, short, accent),
    )
    return get_player(conn, new_id(cursor))


def update_player(
    conn: sqlite3.Connection,
    player_id: int,
    *,
    display_name: str,
    short_name: str | None | Unset = UNSET,
    accent_index: int | None | Unset = UNSET,
) -> Player:
    """Edit a player. The name is required; the other two are left alone if absent.

    Renaming to your own name in different case or spacing is allowed, which is
    how a typo gets fixed; the player being renamed is excluded from the
    duplicate check.

    Passing `None` for a short name or an accent clears it. Omitting the
    argument entirely leaves whatever is there -- see `UNSET`.
    """
    cleaned = _clean(display_name)
    assignments = ["display_name = ?"]
    values: list[str | int | None] = [cleaned]
    if not isinstance(short_name, Unset):
        assignments.append("short_name = ?")
        values.append(_clean_short_name(short_name))
    if not isinstance(accent_index, Unset):
        assignments.append("accent_index = ?")
        values.append(_clean_accent(accent_index))
    get_player(conn, player_id)
    _reject_duplicate(conn, cleaned, exclude_id=player_id)
    # Every fragment of this statement is a literal from the list above; only
    # the values are ever parameters.
    conn.execute(f"UPDATE players SET {', '.join(assignments)} WHERE id = ?", (*values, player_id))
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
