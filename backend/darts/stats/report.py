"""Turning query rows into the reports the API serves.

The rule for a metric with nothing behind it: **a count is 0, an average is
None.** A player who has thrown no darts has thrown zero darts -- that is a
fact, and 0 states it. Their 3-dart average is not 0; it does not exist, and
saying 0 would put them at the bottom of a ranking they are not in. Every query
returns no row at all for such a player rather than a row of zeroes, so the
empty case is built here, in one place, instead of being coaxed out of SQL.

Nothing in here opens a transaction. `darts.services.stats` owns that, the way
#17 and #18 established.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from darts.engine.cricket import TARGETS
from darts.engine.throws import Throw
from darts.stats.queries import StatsFilter, run


@dataclass(frozen=True, slots=True)
class Bands:
    """Scoring bands, counted over visits and cumulative: a 180 is also a 140+."""

    one_eighties: int = 0
    one_forty_plus: int = 0
    hundred_plus: int = 0
    sixty_plus: int = 0


@dataclass(frozen=True, slots=True)
class X01Stats:
    """Scoring in x01, always over x01 darts only.

    A lifetime 3-dart average taken across x01 and cricket darts would be a
    number about nothing, so these are computed from x01 darts whether or not
    the caller asked for `game_type=x01`.
    """

    darts_thrown: int = 0
    visits: int = 0
    points_scored: int = 0
    three_dart_average: float | None = None
    first_nine_average: float | None = None
    first_nine_darts: int = 0
    highest_visit: int | None = None
    average_visit: float | None = None
    bands: Bands = field(default_factory=Bands)
    checkout_attempts: int = 0
    checkouts_hit: int = 0
    checkout_percentage: float | None = None
    best_checkout: int | None = None


@dataclass(frozen=True, slots=True)
class TargetStats:
    """One cricket number, and how often this player's darts land on it."""

    target: int
    hits: int = 0
    marks: int = 0
    hit_rate: float | None = None


@dataclass(frozen=True, slots=True)
class CricketStats:
    """Cricket, always over cricket darts only."""

    darts_thrown: int = 0
    marks: int = 0
    darts_on_target: int = 0
    wasted_darts: int = 0
    marks_per_round: float | None = None
    targets: tuple[TargetStats, ...] = ()


@dataclass(frozen=True, slots=True)
class Segment:
    """One board segment and multiplier, and how often it was hit.

    `label` is the same short form `DartResponse` carries -- "T20", "BULL",
    "MISS" -- and comes from `engine.throws.Throw`, the one place in the codebase
    that knows the inner bull from a double. #27 draws a segment-frequency visual
    and would otherwise have to name these itself, in a second language, from
    `segment` and `multiplier`; `services.play` and #20's `darts.csv` both take
    the name from `Throw` rather than spelling it again, and so does this.
    """

    segment: int
    multiplier: int
    darts: int

    @property
    def label(self) -> str:
        return Throw(self.segment, self.multiplier).label


@dataclass(frozen=True, slots=True)
class PlayerStats:
    """Everything one player's darts say, within one filter."""

    player_id: int
    display_name: str
    is_archived: bool
    darts_thrown: int = 0
    legs_played: int = 0
    legs_won: int = 0
    matches_played: int = 0
    matches_won: int = 0
    x01: X01Stats = field(default_factory=X01Stats)
    cricket: CricketStats = field(default_factory=CricketStats)
    segments: tuple[Segment, ...] = ()


@dataclass(frozen=True, slots=True)
class LegLine:
    """One player's line in one leg: the per-leg 3-dart average, and MPR."""

    leg_id: int
    leg_index: int
    player_id: int
    darts_thrown: int
    won: bool
    three_dart_average: float | None
    marks_per_round: float | None


@dataclass(frozen=True, slots=True)
class MatchStats:
    """One match: every player's stats within it, and their per-leg lines."""

    match_id: int
    game_type: str
    variant: str | None
    players: tuple[PlayerStats, ...]
    legs: tuple[LegLine, ...]


@dataclass(frozen=True, slots=True)
class LeaderboardRow:
    """One ranked player."""

    player_id: int
    display_name: str
    darts_thrown: int
    three_dart_average: float | None
    highest_visit: int | None
    one_eighties: int
    checkout_attempts: int
    checkouts_hit: int
    checkout_percentage: float | None
    best_checkout: int | None


@dataclass(frozen=True, slots=True)
class Leaderboard:
    """The ranking, and the threshold that decided who is on it."""

    min_darts: int
    rows: tuple[LeaderboardRow, ...]


def _by_player(rows: Sequence[sqlite3.Row]) -> dict[int, sqlite3.Row]:
    return {int(row["player_id"]): row for row in rows}


def _grouped(rows: Sequence[sqlite3.Row]) -> dict[int, list[sqlite3.Row]]:
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(int(row["player_id"]), []).append(row)
    return grouped


def _optional_float(value: Any) -> float | None:
    """A nullable numeric column. SQLite returns NULL for an average over nothing."""
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _x01(totals: sqlite3.Row | None, checkout: sqlite3.Row | None) -> X01Stats:
    """The x01 block, from the two queries that make it up.

    They are separate families with separate query plans but describe the same
    darts, so a player with a row in one and not the other keeps the defaults
    from the missing side rather than losing the side that did answer.
    """
    scoring = (
        X01Stats()
        if totals is None
        else X01Stats(
            darts_thrown=int(totals["darts_thrown"]),
            visits=int(totals["visits"]),
            points_scored=int(totals["points_scored"]),
            three_dart_average=_optional_float(totals["three_dart_average"]),
            first_nine_average=_optional_float(totals["first_nine_average"]),
            first_nine_darts=int(totals["first_nine_darts"] or 0),
            highest_visit=_optional_int(totals["highest_visit"]),
            average_visit=_optional_float(totals["average_visit"]),
            bands=Bands(
                one_eighties=int(totals["one_eighties"]),
                one_forty_plus=int(totals["one_forty_plus"]),
                hundred_plus=int(totals["hundred_plus"]),
                sixty_plus=int(totals["sixty_plus"]),
            ),
        )
    )
    if checkout is None:
        return scoring
    return replace(
        scoring,
        checkout_attempts=int(checkout["checkout_attempts"]),
        checkouts_hit=int(checkout["checkouts_hit"]),
        checkout_percentage=_optional_float(checkout["checkout_percentage"]),
        best_checkout=_optional_int(checkout["best_checkout"]),
    )


def _cricket(totals: sqlite3.Row | None, targets: Sequence[sqlite3.Row]) -> CricketStats:
    """The cricket block, with all seven targets present whether hit or not.

    A target the player never went near is a zero, not a gap: #27 renders these
    as a fixed row of numbers, and a list whose length depends on the data would
    make that the client's problem.
    """
    hit = {int(row["target"]): row for row in targets}
    listed = tuple(
        TargetStats(
            target=target,
            hits=int(hit[target]["hits"]) if target in hit else 0,
            marks=int(hit[target]["marks"]) if target in hit else 0,
            hit_rate=_optional_float(hit[target]["hit_rate"]) if target in hit else None,
        )
        for target in TARGETS
    )
    if totals is None:
        return CricketStats(targets=listed)
    return CricketStats(
        darts_thrown=int(totals["darts_thrown"]),
        marks=int(totals["marks"]),
        darts_on_target=int(totals["darts_on_target"]),
        wasted_darts=int(totals["wasted_darts"]),
        marks_per_round=_optional_float(totals["marks_per_round"]),
        targets=listed,
    )


@dataclass(frozen=True, slots=True)
class _Families:
    """Every per-player query, run once for one binding and keyed by player.

    One binding, one pass: the player and match endpoints both want several
    families about the same scope, and running them together keeps the whole
    report to one set of index seeks instead of one per player.
    """

    thrown: Mapping[int, sqlite3.Row]
    x01_totals: Mapping[int, sqlite3.Row]
    checkouts: Mapping[int, sqlite3.Row]
    cricket_totals: Mapping[int, sqlite3.Row]
    cricket_targets: Mapping[int, list[sqlite3.Row]]
    legs: Mapping[int, sqlite3.Row]
    matches: Mapping[int, sqlite3.Row]
    segments: Mapping[int, list[sqlite3.Row]]

    @classmethod
    def query(cls, conn: sqlite3.Connection, params: Mapping[str, object]) -> "_Families":
        return cls(
            thrown=_by_player(run(conn, "darts_thrown", params)),
            x01_totals=_by_player(run(conn, "x01_totals", params)),
            checkouts=_by_player(run(conn, "checkout_totals", params)),
            cricket_totals=_by_player(run(conn, "cricket_totals", params)),
            cricket_targets=_grouped(run(conn, "cricket_targets", params)),
            legs=_by_player(run(conn, "leg_results", params)),
            matches=_by_player(run(conn, "match_results", params)),
            segments=_grouped(run(conn, "segment_frequency", params)),
        )


def _player(
    families: _Families, player_id: int, display_name: str, is_archived: bool
) -> PlayerStats:
    """Assemble one player from whichever queries had a row for them."""
    thrown = families.thrown.get(player_id)
    leg_row, match_row = families.legs.get(player_id), families.matches.get(player_id)
    return PlayerStats(
        player_id=player_id,
        display_name=display_name,
        is_archived=is_archived,
        darts_thrown=int(thrown["darts_thrown"]) if thrown is not None else 0,
        legs_played=int(leg_row["legs_played"]) if leg_row is not None else 0,
        legs_won=int(leg_row["legs_won"]) if leg_row is not None else 0,
        matches_played=int(match_row["matches_played"]) if match_row is not None else 0,
        matches_won=int(match_row["matches_won"]) if match_row is not None else 0,
        x01=_x01(families.x01_totals.get(player_id), families.checkouts.get(player_id)),
        cricket=_cricket(
            families.cricket_totals.get(player_id), families.cricket_targets.get(player_id, [])
        ),
        segments=tuple(
            Segment(int(row["segment"]), int(row["multiplier"]), int(row["darts"]))
            for row in families.segments.get(player_id, [])
        ),
    )


def player_stats(
    conn: sqlite3.Connection,
    player_id: int,
    display_name: str,
    is_archived: bool,
    stats_filter: StatsFilter,
) -> PlayerStats:
    """One player's statistics. Identity is passed in, having been looked up."""
    params = stats_filter.params(player_id=player_id)
    return _player(_Families.query(conn, params), player_id, display_name, is_archived)


def match_stats(
    conn: sqlite3.Connection,
    match_id: int,
    game_type: str,
    variant: str | None,
    stats_filter: StatsFilter,
) -> MatchStats:
    """One match: per-player statistics within it, plus a line per leg.

    The filter is already narrowed to this match by the endpoint, so every query
    here seeks `legs` by match rather than scanning for it.
    """
    params = stats_filter.params()
    families = _Families.query(conn, params)
    return MatchStats(
        match_id=match_id,
        game_type=game_type,
        variant=variant,
        players=tuple(
            _player(
                families,
                int(row["player_id"]),
                str(row["player_name"]),
                bool(row["player_is_archived"]),
            )
            for row in run(conn, "match_roster", params)
        ),
        legs=tuple(
            LegLine(
                leg_id=int(row["leg_id"]),
                leg_index=int(row["leg_index"]),
                player_id=int(row["player_id"]),
                darts_thrown=int(row["darts_thrown"]),
                won=bool(row["won"]),
                three_dart_average=_optional_float(row["three_dart_average"]),
                marks_per_round=_optional_float(row["marks_per_round"]),
            )
            for row in run(conn, "leg_lines", params)
        ),
    )


def leaderboard(conn: sqlite3.Connection, stats_filter: StatsFilter, min_darts: int) -> Leaderboard:
    """The ranking, by x01 3-dart average, over players above the threshold."""
    rows = run(conn, "leaderboard", stats_filter.params(min_darts=min_darts))
    return Leaderboard(
        min_darts=min_darts,
        rows=tuple(
            LeaderboardRow(
                player_id=int(row["player_id"]),
                display_name=str(row["player_name"]),
                darts_thrown=int(row["darts_thrown"]),
                three_dart_average=_optional_float(row["three_dart_average"]),
                highest_visit=_optional_int(row["highest_visit"]),
                one_eighties=int(row["one_eighties"]),
                checkout_attempts=int(row["checkout_attempts"]),
                checkouts_hit=int(row["checkouts_hit"]),
                checkout_percentage=_optional_float(row["checkout_percentage"]),
                best_checkout=_optional_int(row["best_checkout"]),
            )
            for row in rows
        ),
    )
