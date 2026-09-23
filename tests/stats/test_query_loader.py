"""Loading the packaged SQL, and what happens when a file is wrong.

The loader is small but it is the thing standing between a typo in a `.sql`
file and a statistics endpoint that returns the wrong query's answer, so its
refusals are worth testing as carefully as its successes.
"""

import sqlite3
from pathlib import Path

import pytest

from darts.stats.queries import (
    QUERIES,
    SQL_DIR,
    QueryError,
    StatsFilter,
    load,
    plan,
    run,
    sql,
    statement,
)

#: What the endpoints actually ask for. A rename that missed a call site would
#: otherwise only show up when someone opened the page.
REQUIRED = {
    "darts_thrown",
    "segment_frequency",
    "x01_totals",
    "checkout_totals",
    "cricket_totals",
    "cricket_targets",
    "leg_results",
    "match_results",
    "match_roster",
    "leg_lines",
    "leaderboard",
}


def test_every_query_the_layer_uses_is_packaged() -> None:
    assert set(QUERIES) >= REQUIRED


def test_the_families_are_separate_files() -> None:
    """One file per family, as the ticket asks, and every file defines something."""
    files = sorted(path.name for path in SQL_DIR.glob("*.sql"))
    assert files == [
        "checkout.sql",
        "cricket.sql",
        "export.sql",
        "leaderboard.sql",
        "match.sql",
        "results.sql",
        "throws.sql",
        "x01.sql",
    ]
    for path in SQL_DIR.glob("*.sql"):
        assert load(path.parent), path.name


def test_each_query_is_one_statement_ending_in_a_semicolon() -> None:
    for name, text in QUERIES.items():
        assert text.endswith(";"), name
        assert sqlite3.complete_statement(text), name


def test_a_query_runs_unscoped_exactly_as_the_file_is_written(
    seeded: sqlite3.Connection,
) -> None:
    """The scope marker is a comment, so a packaged file is valid SQL as it sits.

    That is the point of using a comment: a query can be pasted into a shell and
    run without the loader, which is what makes reviewing it possible.
    """
    for name in REQUIRED:
        packaged = sql(name)
        assert "-- scope:" in packaged
        assert statement(name, StatsFilter().params()) != packaged
        # The file's own text, unexpanded, still executes.
        seeded.execute(packaged, {"min_darts": 0}).fetchall()


def test_an_unknown_query_name_is_refused() -> None:
    with pytest.raises(QueryError, match="no statistics query named 'nonexistent'"):
        sql("nonexistent")


def test_a_query_with_no_semicolon_is_refused(tmp_path: Path) -> None:
    (tmp_path / "broken.sql").write_text("-- name: dangling\nSELECT 1\n")
    with pytest.raises(QueryError, match="'dangling' has no terminating semicolon"):
        load(tmp_path)


def test_a_duplicate_name_is_refused(tmp_path: Path) -> None:
    """Two files claiming one name would make which query runs depend on sort order."""
    (tmp_path / "a.sql").write_text("-- name: totals\nSELECT 1;\n")
    (tmp_path / "b.sql").write_text("-- name: totals\nSELECT 2;\n")
    with pytest.raises(QueryError, match="duplicate query name 'totals' in b.sql"):
        load(tmp_path)


def test_a_directory_with_no_queries_is_refused(tmp_path: Path) -> None:
    (tmp_path / "prose.sql").write_text("-- just a comment, no name marker\n")
    with pytest.raises(QueryError, match="no queries found"):
        load(tmp_path)


def test_prose_between_queries_belongs_to_neither(tmp_path: Path) -> None:
    """A comment introducing the next query must not be swept into the previous one."""
    (tmp_path / "pair.sql").write_text(
        "-- family prose\n"
        "-- name: first\nSELECT 1 AS one;\n\n"
        "-- Prose about the second one.\n"
        "-- name: second\nSELECT 2 AS two;\n"
    )
    loaded = load(tmp_path)
    assert loaded["first"] == "SELECT 1 AS one;"
    assert loaded["second"] == "SELECT 2 AS two;"


def test_the_loaded_queries_cannot_be_edited_in_place() -> None:
    """A running process must not be able to rewrite its own SQL."""
    with pytest.raises(TypeError):
        QUERIES["x01_totals"] = "SELECT 1;"  # type: ignore[index]


def test_a_scope_marker_keeps_the_indentation_it_replaced() -> None:
    """Only cosmetic, but an assembled statement is read by humans when it fails."""
    text = statement("x01_totals", StatsFilter(game_type="x01").params(player_id=1))
    assert "\n      AND d.game_type = :game_type" in text
    assert "\n      AND d.player_id = :player_id" in text


def test_plan_and_run_agree_on_which_statement_they_are_talking_about(
    seeded: sqlite3.Connection,
) -> None:
    """The binding decides the text, so a plan for a different binding is a different plan."""
    scoped = StatsFilter().params(player_id=1)
    unscoped = StatsFilter().params()
    assert plan(seeded, "darts_thrown", scoped) != plan(seeded, "darts_thrown", unscoped)
    assert len(run(seeded, "darts_thrown", scoped)) == 1
    assert len(run(seeded, "darts_thrown", unscoped)) > 1


def test_an_unused_binding_is_harmless(seeded: sqlite3.Connection) -> None:
    """`params` hands every query the whole set; most of them mention only some."""
    rows = run(seeded, "darts_thrown", StatsFilter().params(player_id=1, min_darts=5000))
    assert len(rows) == 1
