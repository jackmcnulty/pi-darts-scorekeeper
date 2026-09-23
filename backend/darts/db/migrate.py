"""Forward-only, checksummed migrations with one atomic transaction per file."""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from darts.db.connection import transaction

MIGRATIONS = Path(__file__).with_name("migrations")
_NAME = re.compile(r"([0-9]{4})_[a-z0-9_]+\.sql\Z")
_LEADING = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.DOTALL)
_ADDITIVE = re.compile(
    r"\A(?:CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s|"
    r'ALTER\s+TABLE\s+(?:[a-zA-Z_]\w*|"(?:[^"]|"")+")\s+ADD\s)',
    re.IGNORECASE,
)


class MigrationError(ValueError):
    """The on-disk migration history and database cannot safely be reconciled."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sha256: str
    sql: str


def discover(directory: Path = MIGRATIONS) -> tuple[Migration, ...]:
    """Read exact file bytes once, so hashes and executed SQL cannot diverge."""
    found = []
    for path in sorted(directory.glob("*.sql")):
        match = _NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {path.name}")
        raw = path.read_bytes()
        found.append(
            Migration(
                int(match[1]), path.name, hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")
            )
        )
    if not found or [m.version for m in found] != list(range(1, len(found) + 1)):
        raise MigrationError("migrations must be a contiguous sequence starting at 0001")
    return tuple(found)


def _statements(sql: str) -> tuple[str, ...]:
    """Split using SQLite's completeness check, including quoted semicolons.

    Do not use executescript: Python 3.11 commits a pending transaction before
    that call. Only additive DDL is permitted; the runner owns transactions and
    metadata. Plain or double-quoted table names are supported for ADD COLUMN.
    """
    statements = []
    pending = ""
    for char in sql:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            statement = _LEADING.sub("", pending)
            if not _ADDITIVE.match(statement):
                raise MigrationError("migrations allow only CREATE TABLE/INDEX or ALTER TABLE ADD")
            statements.append(statement)
            pending = ""
    if _LEADING.sub("", pending):
        raise MigrationError("migration has an unterminated SQL statement")
    if not statements:
        raise MigrationError("migration contains no additive statements")
    return tuple(statements)


def _verify_applied(conn: sqlite3.Connection, migrations: tuple[Migration, ...]) -> int:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    exists = conn.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    rows = (
        conn.execute(
            "SELECT version, name, sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
        if exists
        else []
    )
    if version != len(rows) or len(rows) > len(migrations):
        raise MigrationError("user_version or migration history does not match available files")
    for row, migration in zip(rows, migrations, strict=False):
        if tuple(row) != (migration.version, migration.name, migration.sha256):
            raise MigrationError(f"applied migration changed: {migration.name}")
    return version


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS) -> tuple[int, ...]:
    """Apply missing files and return their versions; already-current returns ().

    The writer lock is acquired before checking history, allowing concurrent
    startups to serialize safely. A failed file rolls back its DDL, ledger row,
    and user_version together; earlier successful files remain committed.
    """
    migrations = discover(directory)
    applied = []
    for migration in migrations:
        with transaction(conn):
            current = _verify_applied(conn, migrations)
            if migration.version <= current:
                continue
            for statement in _statements(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, sha256) VALUES (?, ?, ?)",
                (migration.version, migration.name, migration.sha256),
            )
            # Version is parsed from a four-digit filename, never caller SQL.
            conn.execute(f"PRAGMA user_version = {migration.version}")
        applied.append(migration.version)
    return tuple(applied)
