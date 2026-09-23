"""The checked-in golden file, and the derivation it came from.

`expected.py` computes #19's expected values from the seeded rows in plain
Python, and `expected_stats.json` is that computation written down. The file is
what the golden tests read, so it has to stay in step with the derivation --
otherwise editing one of them by hand quietly moves the goalposts.
"""

import ast
import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from expected import GOLDEN, TARGETS, derive, dump, load
from seed import PLAYERS, build

from darts.engine.cricket import TARGETS as ENGINE_TARGETS


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    conn = build(tmp_path_factory.mktemp("golden") / "seed.db")
    yield conn
    conn.close()


def test_the_checked_in_file_is_what_the_derivation_produces(
    seeded: sqlite3.Connection,
) -> None:
    """Regenerate with `python tests/fixtures/expected.py` when the seed changes."""
    assert GOLDEN.read_text(encoding="utf-8") == dump(derive(seeded))


def test_the_derivation_is_stable(seeded: sqlite3.Connection) -> None:
    """No clock, no randomness and no scan order in it, same as the seed itself."""
    assert derive(seeded) == derive(seeded)


def test_the_target_order_matches_the_engines(seeded: sqlite3.Connection) -> None:
    """Written out in expected.py on purpose, so a reorder fails instead of drifting."""
    assert TARGETS == ENGINE_TARGETS


def test_the_golden_values_describe_the_seed_that_is_checked_in(
    seeded: sqlite3.Connection,
) -> None:
    """A guard against the file outliving the fixture it describes."""
    golden: dict[str, Any] = load()
    assert set(golden) == {str(index) for index in range(1, len(PLAYERS) + 1)}
    for index, name in enumerate(PLAYERS, start=1):
        assert golden[str(index)]["display_name"] == name

    total = seeded.execute("SELECT count(*) FROM darts").fetchone()[0]
    assert sum(player["darts_thrown"] for player in golden.values()) == total


def test_the_derivation_does_not_use_the_code_it_checks() -> None:
    """The whole point of the file: it must not be the implementation's own output.

    A golden file produced by running the queries would assert that the
    implementation equals itself, and would pass however wrong both were. The
    check is over the parsed module rather than its text, so the prose above
    explaining what it must not import does not count as importing it.
    """
    tree = ast.parse((GOLDEN.parent / "expected.py").read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(module.startswith("darts") for module in imported), imported

    # And no aggregation in the SQL it does run: the counting is Python's.
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Constant) and isinstance(statement.value, str):
            query = statement.value.upper()
            if "SELECT" in query:
                assert "GROUP BY" not in query, statement.value
                assert "V_DARTS" not in query and "V_VISITS" not in query, statement.value
