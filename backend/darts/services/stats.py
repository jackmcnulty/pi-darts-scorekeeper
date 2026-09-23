"""Statistics reads, in a transaction each, as #17 and #18 established.

Nothing here writes, but a report is several queries and they must agree with
each other: a player's darts thrown and their 3-dart average come from separate
statements, and a dart recorded between the two would make the report describe a
board that never existed. So each entry point takes one deferred read
transaction and every query in the report runs inside it, the way
`setup.list_matches` does for its page and its count.

Identity is resolved through the repositories rather than the statistics
queries. A player who does not exist is a `NotFoundError` and a 404; a player
who exists and has never thrown is a well-formed report full of zeroes and
nulls. Those are different answers, and a query that returns no rows cannot
tell them apart.
"""

import sqlite3

from darts.db.connection import transaction
from darts.repo import matches, players
from darts.stats import report
from darts.stats.queries import StatsFilter


def player_stats(
    conn: sqlite3.Connection, player_id: int, stats_filter: StatsFilter
) -> report.PlayerStats:
    """One player's statistics. Raises `NotFoundError` if there is no such player."""
    with transaction(conn, immediate=False):
        player = players.get_player(conn, player_id)
        return report.player_stats(
            conn, player.id, player.display_name, player.is_archived, stats_filter
        )


def match_stats(
    conn: sqlite3.Connection, match_id: int, stats_filter: StatsFilter
) -> report.MatchStats:
    """One match's statistics. Raises `NotFoundError` if there is no such match."""
    with transaction(conn, immediate=False):
        match = matches.get_match(conn, match_id)
        return report.match_stats(
            conn,
            match.id,
            match.config.game_type,
            match.config.variant,
            stats_filter.narrowed_to(match.id),
        )


def leaderboard(
    conn: sqlite3.Connection, stats_filter: StatsFilter, min_darts: int
) -> report.Leaderboard:
    """The ranking. One query, but in a transaction for the same reason."""
    with transaction(conn, immediate=False):
        return report.leaderboard(conn, stats_filter, min_darts)
