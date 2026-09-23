"""Setup operations own transactions; repositories remain composable row operations."""

import sqlite3
from collections.abc import Sequence

from darts.db.connection import transaction
from darts.repo import matches, players
from darts.repo.config import GameConfig
from darts.services.errors import MatchCompleteError


def create_player(
    conn: sqlite3.Connection,
    display_name: str,
    *,
    short_name: str | None = None,
    accent_index: int | None = None,
) -> players.Player:
    # The colour is chosen inside the transaction, so two phones adding a player
    # at the same moment cannot both be handed the same free accent.
    with transaction(conn):
        return players.create_player(
            conn, display_name, short_name=short_name, accent_index=accent_index
        )


def update_player(
    conn: sqlite3.Connection,
    player_id: int,
    display_name: str,
    *,
    short_name: str | None | players.Unset = players.UNSET,
    accent_index: int | None | players.Unset = players.UNSET,
) -> players.Player:
    with transaction(conn):
        return players.update_player(
            conn,
            player_id,
            display_name=display_name,
            short_name=short_name,
            accent_index=accent_index,
        )


def archive_player(conn: sqlite3.Connection, player_id: int) -> players.Player:
    with transaction(conn):
        return players.archive_player(conn, player_id)


def create_match(
    conn: sqlite3.Connection, config: GameConfig, teams: Sequence[matches.TeamSpec]
) -> matches.Match:
    with transaction(conn):
        created = matches.create_match(conn, config, teams)
        return matches.get_match(conn, created.match_id)


def abandon_match(conn: sqlite3.Connection, match_id: int) -> matches.Match:
    with transaction(conn):
        match = matches.get_match(conn, match_id)
        if match.completed_at is not None:
            raise MatchCompleteError(f"match {match_id} is already complete")
        matches.abandon_match(conn, match_id)
        return matches.get_match(conn, match_id)


def list_matches(
    conn: sqlite3.Connection, *, status: matches.MatchStatus | None, limit: int, offset: int
) -> tuple[list[matches.Match], int]:
    with transaction(conn, immediate=False):
        return matches.list_matches(conn, status=status, limit=limit, offset=offset)
