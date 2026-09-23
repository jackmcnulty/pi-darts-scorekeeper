"""Creating a match, and reading one back in full.

`create_match` is the reason this layer exists. A match is not one row: it is a
row in `matches`, one per team in `teams`, one per player in `team_members` and
leg 0 in `legs`, and a database holding three of those four is not a match
anybody can play. So they are written together or not at all.

The transaction is the caller's -- see the package docstring for why nothing
here opens one -- but this is the one function that refuses to run outside one,
because atomicity is the promise it makes and it cannot keep that promise
alone.

    with transaction(conn):
        created = create_match(conn, config, teams)

Everything that can be rejected is rejected before the first INSERT: the
configuration by `GameConfig`, the team shapes here, and the starting team by
`starting_team`, which is called before any row is written precisely so that a
`fixed_team` outside the team range fails the same way an odd `best_of` does.

`is_solo` is derived from the member count rather than supplied. The schema
only checks it is 0 or 1, so nothing there stops a two-person team claiming to
be solo; deriving it is what makes that unrepresentable.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from darts.engine.rotation import starting_team
from darts.repo.config import GameConfig
from darts.repo.errors import InvalidMatchError, NotFoundError
from darts.repo.legs import create_leg
from darts.repo.players import get_player
from darts.repo.rowids import new_id


@dataclass(frozen=True, slots=True)
class TeamSpec:
    """One team as the caller describes it: its players, in throwing order.

    A solo player is a team of one, per #11's model decision. `name` is the
    optional label a doubles pair gets ("Reds"); solo teams normally have none
    and show the player's name instead.
    """

    player_ids: tuple[int, ...]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedMatch:
    """The ids SQLite assigned, in the order the teams were given."""

    match_id: int
    team_ids: tuple[int, ...]
    leg_id: int


@dataclass(frozen=True, slots=True)
class TeamMember:
    """A player's place in a team, with their name as it stands now.

    `is_archived` travels with the row so historical match detail can show that
    a player has since been retired without a second lookup.
    """

    player_id: int
    member_index: int
    display_name: str
    is_archived: bool


@dataclass(frozen=True, slots=True)
class Team:
    """One team of a match, with its members in throwing order."""

    id: int
    team_index: int
    name: str | None
    is_solo: bool
    members: tuple[TeamMember, ...]


@dataclass(frozen=True, slots=True)
class Match:
    """A match and its teams.

    `config` is parsed from `config_json`, which `create_match` wrote from the
    same validated object as the promoted columns. The promoted columns exist
    for SQL to filter and index on; Python reads the config.
    """

    id: int
    config: GameConfig
    created_at: str
    completed_at: str | None
    winner_team_id: int | None
    teams: tuple[Team, ...]


_TEAM_QUERY = """
    SELECT t.id, t.team_index, t.name, t.is_solo,
           tm.player_id, tm.member_index, p.display_name, p.is_archived
    FROM teams t
    JOIN team_members tm ON tm.team_id = t.id
    JOIN players p ON p.id = tm.player_id
    WHERE t.match_id = ?
    ORDER BY t.team_index, tm.member_index
"""


def _validate_teams(conn: sqlite3.Connection, teams: Sequence[TeamSpec]) -> None:
    """Every way a set of teams can fail to describe a playable match.

    Archived players are rejected: archiving removes somebody from the pickers,
    so picking them is a stale client rather than a choice to honour. Their
    existing matches are untouched.
    """
    if not teams:
        raise InvalidMatchError("a match needs at least one team")
    seen: dict[int, int] = {}
    for index, team in enumerate(teams):
        if not team.player_ids:
            raise InvalidMatchError(f"team {index} has no players")
        for player_id in team.player_ids:
            if player_id in seen:
                raise InvalidMatchError(
                    f"player {player_id} is on team {seen[player_id]} and team {index}"
                )
            seen[player_id] = index
    for player_id in seen:
        # Raises NotFoundError for an id that is not a player at all.
        if get_player(conn, player_id).is_archived:
            raise InvalidMatchError(f"player {player_id} is archived and cannot join a match")


def create_match(
    conn: sqlite3.Connection, config: GameConfig, teams: Sequence[TeamSpec]
) -> CreatedMatch:
    """Write a match, its teams, its members and leg 0 in the caller's transaction.

    Raises `InvalidMatchError` if the teams are unplayable, `NotFoundError` if a
    player id does not exist, and `ValueError` if `fixed_team` falls outside the
    team range -- all of them before any row is written.
    """
    if not conn.in_transaction:
        raise InvalidMatchError("create_match must run inside a transaction")
    _validate_teams(conn, teams)
    starting_index = starting_team(len(teams), 0, config.start_rule, fixed_team=config.fixed_team)

    columns = config.columns
    cursor = conn.execute(
        "INSERT INTO matches(config_json, game_type, variant, start_score, in_rule, out_rule, "
        "best_of) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            config.to_json(),
            columns["game_type"],
            columns["variant"],
            columns["start_score"],
            columns["in_rule"],
            columns["out_rule"],
            columns["best_of"],
        ),
    )
    match_id = new_id(cursor)

    team_ids: list[int] = []
    for team_index, team in enumerate(teams):
        cursor = conn.execute(
            "INSERT INTO teams(match_id, team_index, name, is_solo) VALUES (?, ?, ?, ?)",
            (match_id, team_index, team.name, int(len(team.player_ids) == 1)),
        )
        team_id = new_id(cursor)
        team_ids.append(team_id)
        for member_index, player_id in enumerate(team.player_ids):
            conn.execute(
                "INSERT INTO team_members(team_id, player_id, member_index) VALUES (?, ?, ?)",
                (team_id, player_id, member_index),
            )

    leg_id = create_leg(
        conn,
        match_id=match_id,
        leg_index=0,
        starting_team_id=team_ids[starting_index],
    )
    return CreatedMatch(match_id=match_id, team_ids=tuple(team_ids), leg_id=leg_id)


def _teams_of(conn: sqlite3.Connection, match_id: int) -> tuple[Team, ...]:
    """The match's teams with their members, both in index order."""
    grouped: dict[int, tuple[sqlite3.Row, list[TeamMember]]] = {}
    for row in conn.execute(_TEAM_QUERY, (match_id,)):
        _, members = grouped.setdefault(row["id"], (row, []))
        members.append(
            TeamMember(
                player_id=row["player_id"],
                member_index=row["member_index"],
                display_name=row["display_name"],
                is_archived=bool(row["is_archived"]),
            )
        )
    return tuple(
        Team(
            id=row["id"],
            team_index=row["team_index"],
            name=row["name"],
            is_solo=bool(row["is_solo"]),
            members=tuple(members),
        )
        for row, members in grouped.values()
    )


def get_match(conn: sqlite3.Connection, match_id: int) -> Match:
    """A match with its teams and members. Raises `NotFoundError` if absent.

    This is the historical detail read: it names archived players exactly as it
    names active ones, and flags them so the caller can say so.
    """
    row = conn.execute(
        "SELECT id, config_json, created_at, completed_at, winner_team_id "
        "FROM matches WHERE id = ?",
        (match_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no match with id {match_id}")
    return Match(
        id=row["id"],
        config=GameConfig.from_json(row["config_json"]),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        winner_team_id=row["winner_team_id"],
        teams=_teams_of(conn, match_id),
    )
