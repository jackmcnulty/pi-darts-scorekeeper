"""Getting the data out: two CSV grains and one statistics document.

Everything here reads, and every entry point takes one deferred read
transaction, the way `services.stats` and `services.setup` do. An export is many
rows from several statements and they have to agree with each other; a dart
recorded halfway through would otherwise put a match in `matches.csv` whose
count disagrees with the rows in `darts.csv`.

Streaming, and why the generators own nothing
---------------------------------------------
The two CSV entry points are **generators**. They yield one formatted line at a
time straight off an open cursor, so a fifty-thousand-dart export is never a
fifty-thousand-element list in memory and the first line reaches the client
before the last row has been read.

That has one consequence worth stating: a generator's body does not run until it
is iterated, and `StreamingResponse` iterates it *after* the endpoint has
returned. So the connection cannot come from `deps.get_connection`, whose
`with connection(...)` block has closed by then. `darts.api.export` opens a
connection inside its own generator and closes it when iteration ends, however
it ends. These functions take the connection they are given and never outlive
it.

The published headers
---------------------
`DARTS_HEADER` and `MATCHES_HEADER` are a contract, documented in
`docs/data-model.md`. Column names, order and count are all part of it, because
a spreadsheet that reads column 18 will keep reading column 18. Both are
composed only of integers, text and timestamps -- **no column is a float**, so
neither file can drift between platforms on a repr.

A NULL is written as an empty cell, not as `0`, `null` or `NA`. A cricket match
has no `variant` of NULL and an x01 dart has no `cricket_target`; those are
absences, and an empty cell is what every spreadsheet reads back as one.
Booleans are written as the `0`/`1` the schema stores.
"""

import csv
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from typing import Any, Final

from darts.db.connection import transaction
from darts.engine.throws import Throw
from darts.repo import players as players_repo
from darts.repo.matches import MatchStatus
from darts.stats import report
from darts.stats.queries import StatsFilter, stamp, statement

#: The one column no view can supply. `Throw.label` is the only thing in the
#: codebase that tells the inner bull (`BULL`, segment 25 doubled) from the
#: doubles ring (`D20`), which is what #20's comment requires an exported throw
#: to preserve. `segment`, `multiplier` and `score` carry it independently, so
#: the file states it three ways over.
LABEL: Final = "label"

#: One row per recorded dart. Published; see the module docstring.
DARTS_HEADER: Final[tuple[str, ...]] = (
    "match_id",
    "match_created_at",
    "game_type",
    "variant",
    "leg_id",
    "leg_index",
    "team_id",
    "team_index",
    "team_name",
    "player_id",
    "player_name",
    "visit_id",
    "visit_index",
    "team_visit_index",
    "seq_in_leg",
    "dart_index",
    "dart_id",
    LABEL,
    "segment",
    "multiplier",
    "score",
    "counted",
    "caused_bust",
    "was_checkout_attempt",
    "thrown_at",
    "visit_score_before",
    "visit_score_after",
    "visit_is_bust",
    "cricket_target",
    "cricket_counted_marks",
    "cricket_surplus_marks",
    "cricket_wasted",
)

#: One row per match, with its teams and players flattened into two cells.
MATCHES_HEADER: Final[tuple[str, ...]] = (
    "match_id",
    "status",
    "created_at",
    "completed_at",
    "abandoned_at",
    "game_type",
    "variant",
    "start_score",
    "in_rule",
    "out_rule",
    "best_of",
    "legs_played",
    "legs_completed",
    "darts_thrown",
    "winner_team_id",
    "winner_team_name",
    "teams",
    "players",
)

#: `Ana+Ben vs Cal+Dee`. Two separators rather than one, so a 2v2 reads as two
#: sides. These cells are for a human reading a spreadsheet and are deliberately
#: **not** a parseable encoding: #17 only strips a display name, so a player may
#: legitimately be called `A+B`. `winner_team_id` and `darts.csv`'s `team_id` /
#: `player_id` are the unambiguous form, and `darts.csv` is the lossless grain.
TEAM_SEPARATOR: Final = " vs "
MEMBER_SEPARATOR: Final = "+"

#: RFC 4180's line ending, which is also what `csv` defaults to. Named here
#: because it is part of the published format rather than an accident.
LINE_TERMINATOR: Final = "\r\n"


class _Line:
    """A file-like sink that hands each formatted row straight back.

    `csv.writer.writerow` returns whatever the underlying `write` returned, so
    this turns the writer into a formatter with no buffer behind it -- quoting,
    escaping and the line terminator all still `csv`'s, and nothing accumulates.
    """

    def write(self, text: str) -> str:
        return text


def _formatter() -> Any:
    return csv.writer(_Line(), lineterminator=LINE_TERMINATOR)


def _lines(header: Sequence[str], records: Iterator[Sequence[Any]]) -> Iterator[str]:
    """The header, then one line per record, formatted lazily."""
    writer = _formatter()
    line: str = writer.writerow(header)
    yield line
    for record in records:
        line = writer.writerow(record)
        yield line


def _query(conn: sqlite3.Connection, name: str, stats_filter: StatsFilter) -> sqlite3.Cursor:
    """An open cursor, deliberately not `fetchall()`: the point is to stream."""
    params = stats_filter.params()
    return conn.execute(statement(name, params), params)


def _dart_record(row: sqlite3.Row) -> tuple[Any, ...]:
    """One dart, driven by the published header so the two cannot disagree."""
    label = Throw(int(row["segment"]), int(row["multiplier"])).label
    return tuple(label if name == LABEL else row[name] for name in DARTS_HEADER)


def darts_csv(conn: sqlite3.Connection, stats_filter: StatsFilter) -> Iterator[str]:
    """`darts.csv`: one line per recorded dart, in match, leg and throw order.

    Busted and uncounted darts are here -- they were thrown, and `counted` and
    `caused_bust` say what became of them. Undone darts are not, because #15
    deleted the row.
    """
    with transaction(conn, immediate=False):
        yield from _lines(
            DARTS_HEADER,
            (_dart_record(row) for row in _query(conn, "export_darts", stats_filter)),
        )


def _status(row: sqlite3.Row) -> str:
    """#17's vocabulary, from the same two columns `repo.matches` reads."""
    if row["match_abandoned_at"] is not None:
        return str(MatchStatus.ABANDONED)
    if row["match_completed_at"] is not None:
        return str(MatchStatus.COMPLETE)
    return str(MatchStatus.IN_PROGRESS)


def _team_label(name: Any, members: list[str]) -> str:
    """What to call a team in a cell a human reads.

    A solo team has no `name` -- #17 only names a team when somebody types one --
    so it is called after whoever is in it. That makes `teams` read `Ana vs Ben`
    for a singles match and `Reds vs Blues` for a named one, instead of the
    empty cells a literal `team_name` would give in the commonest case of all.
    """
    return str(name) if name is not None else MEMBER_SEPARATOR.join(members)


def _match_record(rows: list[sqlite3.Row]) -> tuple[Any, ...]:
    """One match, folded from its (team, player) rows.

    The rows arrive ordered by `team_index` then `member_index`, so the two
    joined cells come out in board order rather than in whatever order SQLite
    felt like -- which is why the join happens here and not in `group_concat`,
    whose `ORDER BY` needs SQLite 3.44 and the Pi has 3.40.
    """
    first = rows[0]
    members: dict[int, list[str]] = {}
    names: dict[int, Any] = {}
    for row in rows:
        team_id = int(row["team_id"])
        names.setdefault(team_id, row["team_name"])
        members.setdefault(team_id, []).append(str(row["player_name"]))
    labels = {team: _team_label(names[team], members[team]) for team in names}
    winner_id = first["match_winner_team_id"]
    winner = labels.get(int(winner_id), "") if winner_id is not None else ""
    return (
        first["match_id"],
        _status(first),
        first["match_created_at"],
        first["match_completed_at"],
        first["match_abandoned_at"],
        first["game_type"],
        first["variant"],
        first["start_score"],
        first["in_rule"],
        first["out_rule"],
        first["best_of"],
        first["legs_played"],
        first["legs_completed"],
        first["darts_thrown"],
        first["match_winner_team_id"],
        winner,
        TEAM_SEPARATOR.join(labels[team] for team in labels),
        TEAM_SEPARATOR.join(MEMBER_SEPARATOR.join(members[team]) for team in members),
    )


def matches_csv(conn: sqlite3.Connection, stats_filter: StatsFilter) -> Iterator[str]:
    """`matches.csv`: one line per match, newest id last.

    `groupby` over an ordered cursor keeps this streaming: only one match's
    handful of participation rows is ever held, never the whole result.
    """
    with transaction(conn, immediate=False):
        cursor = _query(conn, "export_matches", stats_filter)
        grouped = (_match_record(list(rows)) for _, rows in groupby(cursor, key=_match_id))
        yield from _lines(MATCHES_HEADER, grouped)


def _match_id(row: sqlite3.Row) -> int:
    return int(row["match_id"])


@dataclass(frozen=True, slots=True)
class StatsDocument:
    """`stats.json`: the whole statistical picture as one self-describing file.

    `generated_at` and `schema_version` are here because a file outlives the
    request that made it: something read off a Samba share months later has to
    be able to say what it is and when it was true.
    """

    generated_at: str
    schema_version: int
    min_darts: int
    leaderboard: report.Leaderboard
    players: tuple[report.PlayerStats, ...]


#: The export's own threshold. `/api/stats/leaderboard` defaults to 50 because a
#: ranking needs one; an export is raw material and withholds nothing, so the
#: file ships every player who has thrown and the spreadsheet can cut it.
DEFAULT_EXPORT_MIN_DARTS: Final = 0


def stats_document(
    conn: sqlite3.Connection,
    stats_filter: StatsFilter,
    min_darts: int = DEFAULT_EXPORT_MIN_DARTS,
    *,
    now: datetime | None = None,
) -> StatsDocument:
    """Every player's report and the leaderboard, read as one consistent whole.

    One transaction covers the ranking and every individual report, so the file
    cannot show a player an average the table beside it disagrees with.
    Archived players are included: they are left off the live leaderboard
    because that is a thing you are currently on, but an export of the history
    that silently dropped them would be wrong about the history.
    """
    with transaction(conn, immediate=False):
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        ranking = report.leaderboard(conn, stats_filter, min_darts)
        reports = tuple(
            report.player_stats(
                conn, player.id, player.display_name, player.is_archived, stats_filter
            )
            for player in players_repo.list_players(conn, include_archived=True)
        )
    return StatsDocument(
        generated_at=stamp(now if now is not None else datetime.now(UTC)),
        schema_version=version,
        min_darts=min_darts,
        leaderboard=ranking,
        players=reports,
    )
