"""The replaceable query surface, rebuilt wholesale from views.sql.

Views hold no data, so they are dropped and recreated instead of migrated:
adding or changing one is an edit to views.sql and nothing else. `user_version`
never moves and no ledger row is written.

This is deliberately *not* routed through `darts.db.migrate`. That runner
rejects `CREATE VIEW` by design, and it records a checksum of every file it
applies so an already-applied migration can never change again -- exactly the
opposite of what a view wants. The two allowlists are kept separate rather than
shared for the same reason: widening one must not widen the other.
"""

import re
import sqlite3
from pathlib import Path

from darts.db.connection import transaction

VIEWS = Path(__file__).with_name("views.sql")
_LEADING = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.DOTALL)
_CREATE_VIEW = re.compile(r"\ACREATE\s+VIEW\s", re.IGNORECASE)


class ViewError(ValueError):
    """views.sql contains something other than a well-formed CREATE VIEW."""


def _statements(sql: str) -> tuple[str, ...]:
    """Split on SQLite's own completeness check, as the migration runner does.

    Only CREATE VIEW is permitted. Dropping is the applier's job, not the
    file's, so that a view deleted from views.sql is actually removed rather
    than lingering from a previous install.
    """
    statements = []
    pending = ""
    for char in sql:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            statement = _LEADING.sub("", pending)
            if not _CREATE_VIEW.match(statement):
                raise ViewError("views.sql allows only CREATE VIEW statements")
            statements.append(statement)
            pending = ""
    if _LEADING.sub("", pending):
        raise ViewError("views.sql has an unterminated SQL statement")
    if not statements:
        raise ViewError("views.sql defines no views")
    return tuple(statements)


def view_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Every view currently installed, in name order."""
    return tuple(
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'view' ORDER BY name")
    )


def install_views(conn: sqlite3.Connection, path: Path = VIEWS) -> tuple[str, ...]:
    """Drop every installed view and recreate the file's, atomically.

    Run this after `migrate`: a view is only as good as the tables under it.
    Running it twice leaves exactly one copy of each view, because the drop is
    unconditional rather than a CREATE IF NOT EXISTS that would silently keep a
    stale definition.
    """
    statements = _statements(path.read_text(encoding="utf-8"))
    with transaction(conn):
        for name in view_names(conn):
            conn.execute(f'DROP VIEW "{name}"')
        for statement in statements:
            conn.execute(statement)
    return view_names(conn)
