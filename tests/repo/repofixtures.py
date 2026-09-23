"""Shared values and helpers for the repository tests.

A plain module rather than conftest.py, following `tests/db/dbfixtures.py`:
these are ordinary functions and constants, and importing them by name is
clearer than reaching for a fixture. conftest.py holds only the fixtures.
"""

import sqlite3

from darts.repo.config import GameConfig
from darts.repo.matches import CreatedMatch
from darts.repo.visits import NewVisit, create_visit

#: A valid, unremarkable x01 configuration, for tests that need any match at all.
X01_501 = GameConfig(
    game_type="x01", best_of=3, start_score=501, in_rule="double", out_rule="double"
)

#: The cricket equivalent.
CRICKET = GameConfig(game_type="cricket", best_of=3, variant="standard")


def x01_with(**changes: object) -> GameConfig:
    """`X01_501` with some fields replaced, re-validated from scratch.

    Deliberately not `model_copy(update=...)`, which skips validation and would
    leave `start_rule` as a plain `str`. `darts.engine.rotation.starting_team`
    compares the rule by *identity* against `StartRule` members, so a bare
    string does not match any branch and quietly falls through to the wrong
    starting team instead of raising.
    """
    return GameConfig(**{**X01_501.model_dump(), **changes})


def open_visit(
    conn: sqlite3.Connection,
    created: CreatedMatch,
    *,
    player_id: int,
    team_index: int = 0,
    visit_index: int = 0,
) -> int:
    """A `visits` row on `created`'s leg 0, returning its id.

    Goes through `repo.visits.create_visit`, which #15 added: opening a visit
    was play-path work when the dart tests were written, so they scaffolded
    their own row in raw SQL. Now there is one way to open a visit and this is
    a thin wrapper over it rather than a second one.

    `team_visit_index` is just `visit_index`, which is not what a real rotation
    would produce but does satisfy `UNIQUE (leg_id, team_id, team_visit_index)`
    for any distinct visits. Nothing here depends on its value.
    """
    return create_visit(
        conn,
        NewVisit(
            leg_id=created.leg_id,
            match_id=created.match_id,
            team_id=created.team_ids[team_index],
            player_id=player_id,
            visit_index=visit_index,
            team_visit_index=visit_index,
            score_before=501,
            score_after=501,
        ),
    ).id


def count(conn: sqlite3.Connection, table: str) -> int:
    """Rows in `table`. Only ever called with a literal table name."""
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts of the four tables `create_match` writes."""
    return {table: count(conn, table) for table in ("matches", "teams", "team_members", "legs")}
