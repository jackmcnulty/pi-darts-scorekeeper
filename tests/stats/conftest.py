"""Databases the statistics tests share.

The seed is small and built often; the bulk database is 50,000 darts and an
ANALYZE, so it is built once for the session. Both are read-only to every test
that takes them, which is what makes sharing them safe.
"""

import sqlite3
from collections.abc import Iterator

import pytest
from bulk import BulkSize
from bulk import build as build_bulk
from seed import build as build_seed


@pytest.fixture(scope="session")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    """#13's deterministic fixture: the dataset the golden values describe."""
    conn = build_seed(tmp_path_factory.mktemp("stats-seed") / "seed.db")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def bulk(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[sqlite3.Connection, BulkSize]]:
    """50,000 synthetic darts, analysed, for the plan and timing criteria."""
    conn, size = build_bulk(tmp_path_factory.mktemp("stats-bulk") / "bulk.db")
    yield conn, size
    conn.close()
