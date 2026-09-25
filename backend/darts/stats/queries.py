"""Loading, binding and running the packaged statistics queries.

The SQL lives in `sql/*.sql`, one file per family, because that is where it can
be read as SQL -- reviewed, pasted into a shell, run under EXPLAIN QUERY PLAN --
rather than as fragments of Python string. A file holds one family and may
define several queries in it, each introduced by a `-- name: <identifier>`
marker. The marker is a comment, so a file stays valid SQL and the view-bypass
scanner (which strips comments before looking for a base table) never sees it.

Every query takes the same six scope parameters, plus `:min_darts` for the
leaderboard, and a parameter left unset means "do not narrow by this" -- which
is what lets one query serve the player, match and leaderboard scopes instead of
three near-identical ones.

**Unset means the predicate is not there, not that it is bound to NULL.** The
obvious spelling, `WHERE (:player_id IS NULL OR d.player_id = :player_id)`, is
a trap: SQLite prepares a statement without knowing what will be bound to it, so
an OR against a parameter can never become an index seek. Measured over 50,000
darts it turns `SEARCH d USING INDEX darts_player_time (player_id=?)` into a
full scan of `visits` -- correct answers, two to three times the work, and a
plan that fails #19's own acceptance criterion.

So the scope is composed instead. A query marks where its scope belongs with a
`-- scope: <alias>` comment and `statement` replaces that line with one
`AND ...` per parameter that is actually set. Only predicates from `_PREDICATES`
are ever emitted and values still travel as bindings, so this is assembly from a
closed set, not string interpolation of user input. The marker is a comment, so
each file stays valid SQL that runs unscoped in a shell, and the view-bypass
scanner -- which strips comments first -- never sees it.

`last_matches`, the one predicate that is not a column comparison
-----------------------------------------------------------------
#27 needs "recent form" beside a lifetime number, and the honest spelling of
recent for darts is a count of matches rather than a date window: `since` cannot
say "my last ten games" without knowing when they were. So `last_matches` is a
predicate like the others, and the three decisions inside it are the reason it is
four lines rather than one.

**It is per player, and therefore a pair set.** `(player_id, match_id) IN ...`,
not `match_id IN ...`. A leaderboard windowed to "the last ten matches" globally
ranks whoever happened to play in them and drops everyone who sat out; windowed
per player it is a form table, where each player is judged on their own last ten
and the comparison is between equal numbers of matches. The flat spelling cannot
express that -- it would credit one player with another's window -- so the row
value is load-bearing rather than decorative.

**It is uncorrelated, which is what makes it affordable.** The obvious per-player
top-N is a subquery correlated on the outer alias, which SQLite then evaluates
once per candidate *dart*. Ranking every player's matches once with
`ROW_NUMBER() OVER (PARTITION BY player_id)` and letting the outer query probe
the result instead plans as `LIST SUBQUERY` / `REUSE LIST SUBQUERY` -- built one
time and reused. Measured over 50,000 darts: 0.10-0.50 ms for a windowed player
card and 3.6 ms for a windowed leaderboard, against 24 ms for the lifetime
leaderboard it is faster than, because the window cuts the dart set before any
averaging happens. `tests/stats/test_query_plans.py` asserts the plan.

**The window is drawn from the same scope as the query, minus itself.** That is
what `{scope}` in the predicate is for: the inner match list gets every other
bound predicate, aliased to its own table. So `?game_type=cricket&last_matches=10`
is the last ten *cricket* matches, not the cricket subset of the last ten
matches overall -- which would report ten as the window and then average two
matches' darts. Excluding only `last_matches` keeps that rule one sentence long
and makes the recursion impossible.

It draws from `v_match_players`, so a match counts as played by everyone who was
in it, including a 2v2 partner who never threw. Such a match contributes no
darts and still consumes a slot, which is the truthful reading: it is a match you
played. Reading a view rather than a base table also keeps the promise
`tests/db/test_view_bypass.py` enforces over `sql/` -- that scanner reads files
and cannot see a predicate that lives in Python, so `test_query_loader.py`
asserts it here instead.
"""

import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

SQL_DIR: Final[Path] = Path(__file__).with_name("sql")

#: `-- name: x01_totals` on a line of its own. Anything before the first marker
#: in a file is prose about the family; anything between two markers is prose
#: about the one that follows.
_NAME = re.compile(r"^[ \t]*--[ \t]*name:[ \t]*([a-z][a-z0-9_]*)[ \t]*$", re.MULTILINE)

#: `  -- scope: d` on a line of its own, naming the alias the predicates hang
#: off. The leading whitespace is captured so the expansion keeps the file's
#: indentation and the assembled statement stays readable in an error message.
_SCOPE = re.compile(r"^([ \t]*)--[ \t]*scope:[ \t]*([a-z_][a-z0-9_]*)[ \t]*$", re.MULTILINE)

#: The alias the window's own match list is read under. Fixed rather than
#: derived, because the window is one piece of SQL written once here; it only has
#: to avoid colliding with the aliases the query files use for the outer scope.
_WINDOW_ALIAS: Final = "w"

#: Each player's own last `:last_matches` matches, as (player_id, match_id)
#: pairs. `{scope}` is filled with every *other* bound predicate under
#: `_WINDOW_ALIAS`, so the window is drawn from the same scope as the query that
#: probes it. See the module docstring for why it is a pair set and why the rank
#: is computed once rather than correlated.
_LAST_MATCHES: Final = (
    "({alias}.player_id, {alias}.match_id) IN ("
    "SELECT ranked.player_id, ranked.match_id FROM ("
    "SELECT {w}.player_id AS player_id, {w}.match_id AS match_id, "
    "ROW_NUMBER() OVER (PARTITION BY {w}.player_id "
    "ORDER BY {w}.match_created_at DESC, {w}.match_id DESC) AS rn "
    "FROM v_match_players {w} WHERE TRUE{scope}"
    ") ranked WHERE ranked.rn <= :last_matches)"
)

#: The only predicates the scope marker will ever emit. Every view the stats
#: layer reads carries all five scope columns, so one table serves all of them.
_PREDICATES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "game_type": "{alias}.game_type = :game_type",
        "variant": "{alias}.variant = :variant",
        # Match `created_at`, so a match is never split across the boundary.
        "since": "{alias}.match_created_at >= :since",
        "match_id": "{alias}.match_id = :match_id",
        "player_id": "{alias}.player_id = :player_id",
        "last_matches": _LAST_MATCHES,
    }
)

#: What the window's inner match list is *not* narrowed by. Only itself: a window
#: inside the window would recurse, and every other filter belongs there so that
#: "my last ten cricket matches" means what it says.
_WINDOW_EXCLUDES: Final = frozenset({"last_matches"})


class QueryError(LookupError):
    """A query file is malformed, or a query was asked for by a name nobody defines."""


def _statements(text: str) -> Iterator[tuple[str, str]]:
    """Every (name, SQL) pair in one file, in the order they are written.

    A statement runs from its marker to the last semicolon before the next
    marker, so the prose introducing the *next* query is not swept into the
    previous one's text -- Python's sqlite3 accepts exactly one statement and
    would not thank us for the rest of the file.
    """
    markers = list(_NAME.finditer(text))
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[marker.end() : end]
        semicolon = body.rfind(";")
        if semicolon < 0:
            raise QueryError(f"query {marker[1]!r} has no terminating semicolon")
        yield marker[1], body[: semicolon + 1].strip()


def load(directory: Path = SQL_DIR) -> dict[str, str]:
    """Every packaged query, keyed by name, read once at import."""
    queries: dict[str, str] = {}
    for path in sorted(directory.glob("*.sql")):
        for name, body in _statements(path.read_text(encoding="utf-8")):
            if name in queries:
                raise QueryError(f"duplicate query name {name!r} in {path.name}")
            queries[name] = body
    if not queries:
        raise QueryError(f"no queries found under {directory}")
    return queries


#: Read-only so that a caller cannot edit the loaded SQL of a running process.
QUERIES: Final[Mapping[str, str]] = MappingProxyType(load())


def sql(name: str) -> str:
    """The text of one query, by name."""
    try:
        return QUERIES[name]
    except KeyError:
        raise QueryError(f"no statistics query named {name!r}") from None


def stamp(moment: datetime) -> str:
    """A datetime in the exact text format the schema stores timestamps in.

    Every stored timestamp is UTC in `%Y-%m-%dT%H:%M:%S.sssZ`, a format whose
    lexicographic order is its chronological order, so `>=` on the text is a
    correct comparison and an index on it is usable. A naive datetime is taken
    as UTC, which is the only clock this box has.
    """
    utc = moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


@dataclass(frozen=True, slots=True)
class StatsFilter:
    """What the five query parameters narrow a statistics request to.

    `since` is compared against the *match's* `created_at`, so a match is never
    split across the boundary: asking for stats since Monday gives whole matches
    that started on or after Monday, not the tail of Sunday night's game.

    `last_matches` is the other way of saying recent, and the two are
    independent: `since` is a date and this is a count, so they compose into "my
    last ten matches, of those played since Monday" without either overriding the
    other. It counts *each player's own* matches, so it means the same thing on a
    one-player report and on a leaderboard.
    """

    game_type: str | None = None
    variant: str | None = None
    since: str | None = None
    match_id: int | None = None
    last_matches: int | None = None

    def narrowed_to(self, match_id: int) -> "StatsFilter":
        """The same filter, scoped to one match."""
        return replace(self, match_id=match_id)

    def params(self, *, player_id: int | None = None, min_darts: int = 0) -> dict[str, Any]:
        """The full binding every packaged query is prepared to accept."""
        return {
            "game_type": self.game_type,
            "variant": self.variant,
            "since": self.since,
            "match_id": self.match_id,
            "player_id": player_id,
            "last_matches": self.last_matches,
            "min_darts": min_darts,
        }


def statement(name: str, params: Mapping[str, Any]) -> str:
    """One query's SQL for one binding, with its scope markers expanded.

    A parameter absent from `params`, or present and None, contributes no
    predicate at all -- that is the whole point, and why the text depends on
    the binding rather than only on the name.
    """
    bound = [key for key in _PREDICATES if params.get(key) is not None]
    # The window's own scope, on one line because it is spliced into a
    # single-line subquery. Every bound predicate but the window itself.
    window = "".join(
        f" AND {_PREDICATES[key].format(alias=_WINDOW_ALIAS)}"
        for key in bound
        if key not in _WINDOW_EXCLUDES
    )

    def expand(marker: re.Match[str]) -> str:
        indent, alias = marker[1], marker[2]
        return "\n".join(
            f"{indent}AND {_PREDICATES[key].format(alias=alias, w=_WINDOW_ALIAS, scope=window)}"
            for key in bound
        )

    return _SCOPE.sub(expand, sql(name))


def run(conn: sqlite3.Connection, name: str, params: Mapping[str, Any]) -> Sequence[sqlite3.Row]:
    """Run one packaged query and return every row.

    Rows come back as `sqlite3.Row`, which every connection this project opens
    is configured for; callers read columns by the name the SQL gave them.
    """
    return conn.execute(statement(name, params), params).fetchall()


def plan(conn: sqlite3.Connection, name: str, params: Mapping[str, Any]) -> list[str]:
    """The EXPLAIN QUERY PLAN detail lines for one packaged query.

    Exposed here rather than written out in the tests because the plan is part
    of what this layer promises: #19 requires the hot queries to seek an index
    and never full-scan `darts`, and a promise nothing can read is not one. It
    takes the same binding as `run` because, with the scope composed rather than
    bound, the binding is what decides the plan.
    """
    rows = conn.execute("EXPLAIN QUERY PLAN " + statement(name, params), params).fetchall()
    return [str(row[-1]) for row in rows]
