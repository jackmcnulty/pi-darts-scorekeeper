"""#19's statistics queries must go through the views, not the base tables.

`backend/darts/stats/sql/` does not exist yet, so the guard has nothing to scan
and passes vacuously. That is the dangerous kind of test, so the scanner itself
is exercised here against queries written for the purpose: whatever #19 adds, a
bypass will be caught, and if the detection ever stops working these tests fail
today rather than in silence.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATS_SQL = ROOT / "backend/darts/stats/sql"

#: Base tables the statistics layer must not read directly. Views wrapping them
#: (`v_darts`, `v_visits`) do not match: `_` is a word character, so there is no
#: word boundary in the middle of `v_darts`.
_FORBIDDEN = re.compile(
    r'\b(?:FROM|JOIN)\s+"?(?:main\.)?(darts|visits)"?\b',
    re.IGNORECASE,
)
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def bypasses(sql: str) -> list[str]:
    """The base tables `sql` reads directly, in order of appearance.

    Comments are stripped first: #19's queries are expected to *mention* the
    tables they are deliberately not reading, and naming one must not be an
    offence.
    """
    return [match[1].lower() for match in _FORBIDDEN.finditer(_COMMENTS.sub(" ", sql))]


def scan(directory: Path) -> dict[str, list[str]]:
    """Every *.sql file under `directory` that reads a base table, with what it read."""
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): found
        for path in sorted(directory.rglob("*.sql"))
        if (found := bypasses(path.read_text(encoding="utf-8")))
    }


def test_no_view_bypass() -> None:
    """Every packaged statistics query reads a view."""
    assert scan(STATS_SQL) == {}


def test_the_stats_directory_is_scanned_when_it_exists(tmp_path: Path) -> None:
    """The same scan, over a directory that does have files in it.

    This is what stops `test_no_view_bypass` from being vacuous forever: the
    walk, the read and the match are all executed here regardless of whether
    #19 has landed.
    """
    (tmp_path / "nested").mkdir()
    (tmp_path / "good.sql").write_text("SELECT player_id FROM v_darts WHERE counted = 1;")
    (tmp_path / "nested" / "bad.sql").write_text("SELECT * FROM darts JOIN visits USING (id);")
    assert scan(tmp_path) == {"nested/bad.sql": ["darts", "visits"]}
    assert scan(tmp_path / "missing") == {}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM darts;",
        "select count(*) from visits v;",
        'SELECT * FROM "darts";',
        "SELECT * FROM main.darts;",
        "SELECT * FROM v_visits JOIN darts ON darts.id = v_visits.visit_id;",
        "SELECT *\n  FROM\n  darts;",
    ],
)
def test_a_direct_read_of_a_base_table_is_caught(sql: str) -> None:
    assert bypasses(sql) != []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM v_darts;",
        "SELECT * FROM v_visits JOIN v_darts USING (visit_id);",
        "-- derived FROM darts, but read through the view\nSELECT * FROM v_darts;",
        "/* FROM visits */ SELECT * FROM v_visits;",
        "SELECT * FROM cricket_point_events;",
        "SELECT dart_id FROM v_darts WHERE counted = 1;",
    ],
)
def test_a_view_read_is_not_flagged(sql: str) -> None:
    assert bypasses(sql) == []
