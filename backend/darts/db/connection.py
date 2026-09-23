"""File-backed SQLite connections and explicit transaction boundaries."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def connect(path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a connection with the durability settings required for every writer.

    Autocommit is enabled; use transaction() for a logical write operation.
    In-memory databases cannot use WAL and are intentionally rejected.
    """
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=check_same_thread)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if mode != "wal":
            raise ValueError("a file-backed database supporting WAL is required")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA foreign_keys = ON")
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def connection(path: str | Path, *, check_same_thread: bool = True) -> Iterator[sqlite3.Connection]:
    """Close the connection on exit, including on failure."""
    conn = connect(path, check_same_thread=check_same_thread)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(
    conn: sqlite3.Connection, *, immediate: bool = True
) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception (including failed commit).

    Reject nesting so a helper cannot accidentally commit its caller's work.
    Use immediate=False for an explicit deferred/read transaction.
    """
    if conn.in_transaction:
        raise ValueError("nested transactions are not supported")
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
