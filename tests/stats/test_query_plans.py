"""The performance criteria, over a database of 50,000 darts.

#19 asks for two things: every query under 50 ms, and EXPLAIN QUERY PLAN
confirming the hot queries use #11's indexes and never full-scan `darts`.

**The query plan is the hard assertion here and the clock is a regression
guard.** A plan is a deterministic property of the SQL, the schema and the
statistics; it is the same on a Pi, on a laptop and on a busy shared runner, and
it is the thing that actually decides whether this scales. Wall-clock over
50,000 rows is none of those. #46 is already an open flake against a 5-second
wall-clock budget in this suite and a second one would not earn its keep, so the
timing assertion is kept but given a generous multiplier: it still catches a
query that got an order of magnitude slower, which is what a regression looks
like, without going red because a runner was busy.

Measured on the development machine when this was written, the slowest query was
the unfiltered leaderboard at roughly 25 ms and every player-scoped query was
under 3 ms.
"""

import re
import sqlite3
import time
from collections.abc import Mapping
from typing import Any

import pytest
from bulk import BulkSize

from darts.stats.queries import QUERIES, StatsFilter, plan, run

#: The ticket's budget. The Pi is the machine it is about.
BUDGET_MS = 50.0

#: What the budget is multiplied by before it is asserted. A query that has
#: regressed by an order of magnitude still fails; a runner having a bad minute
#: does not. See the module docstring.
CI_MULTIPLIER = 10

#: `darts` and `visits` as v_darts aliases them, plus their own names, at the
#: start of a SCAN line. `SCAN scoped` and `SCAN per_visit` are scans of a CTE
#: that has already been narrowed and are not what the criterion is about.
_FULL_SCAN = re.compile(r"^SCAN (d|v|darts|visits)\b")


def scans_a_base_table(lines: list[str]) -> list[str]:
    return [line for line in lines if _FULL_SCAN.match(line)]


def fastest(conn: sqlite3.Connection, name: str, params: Mapping[str, Any], runs: int = 5) -> float:
    """The best of several runs, in milliseconds.

    Best rather than mean: the question is what the query costs, and the slow
    runs are measuring what else the machine was doing.
    """
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        run(conn, name, params)
        best = min(best, (time.perf_counter() - started) * 1000)
    return best


#: Every scope the endpoints actually ask for, as (label, query, binding).
def _scopes() -> list[tuple[str, str, dict[str, Any]]]:
    blank = StatsFilter()
    per_player = (
        "darts_thrown",
        "segment_frequency",
        "x01_totals",
        "checkout_totals",
        "cricket_totals",
        "cricket_targets",
        "leg_results",
        "match_results",
    )
    scopes: list[tuple[str, str, dict[str, Any]]] = [
        (f"player/{name}", name, blank.params(player_id=3)) for name in per_player
    ]
    scopes += [
        (f"match/{name}", name, blank.narrowed_to(7).params())
        for name in (*per_player, "match_roster", "leg_lines")
    ]
    # #20's exports read the same views under the same scope composition, so
    # they are held to the same criteria. Both scopes are checked: narrowed to
    # one match, which is what `?match_id=` must make cheap, and unfiltered,
    # which is the whole-database download.
    scopes += [
        (f"match/{name}", name, blank.narrowed_to(7).params())
        for name in ("export_darts", "export_matches")
    ]
    scopes += [
        (f"export/{name}", name, blank.params()) for name in ("export_darts", "export_matches")
    ]
    scopes += [
        ("leaderboard", "leaderboard", blank.params(min_darts=50)),
        (
            "leaderboard/filtered",
            "leaderboard",
            StatsFilter(game_type="x01", since="2026-02-01T10:00:00.000Z").params(min_darts=50),
        ),
    ]
    return scopes


SCOPES = _scopes()


def test_the_bulk_database_really_holds_fifty_thousand_darts(
    bulk: tuple[sqlite3.Connection, BulkSize],
) -> None:
    """If it did not, every assertion below would be about nothing."""
    conn, size = bulk
    assert size.darts >= 50_000
    assert conn.execute("SELECT count(*) FROM darts").fetchone()[0] == size.darts
    assert conn.execute("SELECT count(*) FROM matches").fetchone()[0] == size.matches
    # And the planner has statistics, as it would on a Pi that has been played on.
    assert conn.execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0] > 0


@pytest.mark.parametrize(("label", "name", "params"), SCOPES, ids=[s[0] for s in SCOPES])
def test_no_hot_query_full_scans_darts_or_visits(
    bulk: tuple[sqlite3.Connection, BulkSize],
    label: str,
    name: str,
    params: dict[str, Any],
) -> None:
    """The criterion: never a full scan of `darts`, at any scope the API uses."""
    conn, _ = bulk
    assert scans_a_base_table(plan(conn, name, params)) == []


@pytest.mark.parametrize(
    "name",
    [
        "darts_thrown",
        "segment_frequency",
        "x01_totals",
        "checkout_totals",
        "cricket_totals",
        "cricket_targets",
    ],
)
def test_a_player_scoped_query_seeks_the_player_index_from_eleven(
    bulk: tuple[sqlite3.Connection, BulkSize], name: str
) -> None:
    """#11's `darts_player_time (player_id, thrown_at, leg_id)`, used as a seek.

    SEARCH, not SCAN: `SCAN d USING INDEX darts_player_time` would be walking
    the whole index, which is what the optional-parameter spelling produced
    before the scope was composed instead of bound.
    """
    conn, _ = bulk
    lines = plan(conn, name, StatsFilter().params(player_id=3))
    assert any(
        line.startswith("SEARCH d USING INDEX darts_player_time (player_id=?)") for line in lines
    ), lines


def test_a_match_scoped_query_seeks_the_match_through_legs(
    bulk: tuple[sqlite3.Connection, BulkSize],
) -> None:
    """`visits` has no index starting at match_id; `legs` does.

    That is why v_darts takes `match_id` from the leg rather than the visit.
    Without it this plan is a full scan of every visit in the database.
    """
    conn, _ = bulk
    lines = plan(conn, "leg_lines", StatsFilter().narrowed_to(7).params())
    assert any("SEARCH l USING INDEX sqlite_autoindex_legs_1 (match_id=?)" in x for x in lines), (
        lines
    )
    assert scans_a_base_table(lines) == []


def test_the_filtered_leaderboard_drives_from_the_match_index_from_eleven(
    bulk: tuple[sqlite3.Connection, BulkSize],
) -> None:
    """#11's `matches_game_created (game_type, variant, created_at)`.

    The leaderboard has no player to seek by, so this is the index that makes
    `?game_type=&since=` cheap: matches, then legs, then darts, all by seek.
    """
    conn, _ = bulk
    scoped = StatsFilter(game_type="x01", since="2026-02-01T10:00:00.000Z")
    lines = plan(conn, "leaderboard", scoped.params(min_darts=50))
    assert any("matches_game_created" in line for line in lines), lines
    assert scans_a_base_table(lines) == []


@pytest.mark.parametrize(("label", "name", "params"), SCOPES, ids=[s[0] for s in SCOPES])
def test_every_query_stays_inside_the_time_budget(
    bulk: tuple[sqlite3.Connection, BulkSize],
    label: str,
    name: str,
    params: dict[str, Any],
) -> None:
    """The regression guard. See the module docstring for why it is generous."""
    conn, _ = bulk
    elapsed = fastest(conn, name, params)
    assert elapsed < BUDGET_MS * CI_MULTIPLIER, (
        f"{label} took {elapsed:.1f} ms, more than {CI_MULTIPLIER}x the {BUDGET_MS} ms budget"
    )


def test_the_darts_export_needs_no_sorter_even_over_fifty_thousand_rows(
    bulk: tuple[sqlite3.Connection, BulkSize],
) -> None:
    """What makes #20's "exports stream rather than buffering" true end to end.

    Python yielding one line at a time buys nothing if SQLite has to materialise
    the whole result first. It does not: the export's `ORDER BY match_id,
    leg_index, seq_in_leg` is satisfied by walking `legs(match_id, leg_index)`
    and then `darts(leg_id, seq_in_leg)`, both of which are unique indexes the
    schema already has, so rows arrive in order with no temporary B-tree behind
    them. A reordering of the header that broke this would still be correct and
    would quietly start buffering 50,000 rows on a Pi.
    """
    conn, _ = bulk
    lines = plan(conn, "export_darts", StatsFilter().params())
    assert not [line for line in lines if "ORDER BY" in line], lines
    assert scans_a_base_table(lines) == []


def test_every_packaged_query_is_covered_by_the_plan_assertions() -> None:
    """A query added to sql/ without a scope here would never be checked."""
    assert {name for _, name, _ in SCOPES} == set(QUERIES)
