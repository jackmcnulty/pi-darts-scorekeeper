"""Reading back the id SQLite just assigned.

Every `id INTEGER PRIMARY KEY` in the schema is a rowid alias, so `lastrowid`
holds the generated id after an INSERT. It is typed `int | None` because it is
also meaningful after statements that assign nothing, and mypy is right to
insist we say what we expect. Repositories always insert exactly one row, so
`None` there would mean SQLite did something we have no story for.
"""

import sqlite3


def new_id(cursor: sqlite3.Cursor) -> int:
    """The id assigned by the INSERT `cursor` just executed."""
    if cursor.lastrowid is None:  # pragma: no cover - unreachable after an INSERT
        raise RuntimeError("expected an assigned rowid after INSERT")
    return cursor.lastrowid
