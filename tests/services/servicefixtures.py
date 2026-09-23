"""Shared values and helpers for the play-service tests.

A plain module rather than conftest.py, following `tests/repo/repofixtures.py`:
conftest.py holds only the fixtures, and these are ordinary functions that read
better imported by name.

`snapshot` is the workhorse. It reduces a leg to every value the service
*derives* -- visit scores and flags, per-dart verdicts, cricket effects, point
events -- with database ids replaced by positional indices, so two legs played
in two different matches can be compared directly.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from darts.db.connection import transaction
from darts.engine.replay import LegConfig, LegState, Rules
from darts.engine.replay import replay as pure_replay
from darts.engine.throws import Throw
from darts.engine.types import Team as EngineTeam
from darts.repo.config import GameConfig
from darts.repo.darts import cricket_effects_for_leg, darts_for_leg, point_awards_for_leg
from darts.repo.matches import CreatedMatch, TeamSpec, create_match
from darts.repo.visits import visits_for_leg
from darts.services import play
from darts.services.state import GameState

#: 301 straight in, double out. Nine darts is a leg, which keeps scripts short.
X01_301 = GameConfig(
    game_type="x01", best_of=3, start_score=301, in_rule="straight", out_rule="double"
)
X01_501 = GameConfig(
    game_type="x01", best_of=3, start_score=501, in_rule="straight", out_rule="double"
)
X01_701 = GameConfig(
    game_type="x01", best_of=3, start_score=701, in_rule="straight", out_rule="double"
)

#: Nine, fifteen and twenty-one dart legs: 180, 180, then a finish.
SCRIPT_301 = ["T20"] * 6 + ["T20", "T19", "D2"]
SCRIPT_501 = ["T20"] * 12 + ["T20", "T19", "D12"]
SCRIPT_701 = ["T20"] * 18 + ["T20", "T17", "BULL"]

#: 121 left, sixty scored, then a dart that leaves 1 and voids the whole visit.
SCRIPT_BUST = ["T20"] * 6 + ["T20", "T20"]


def cricket(variant: str, best_of: int = 3) -> GameConfig:
    return GameConfig(game_type="cricket", best_of=best_of, variant=variant)


def make_match(
    conn: sqlite3.Connection, config: GameConfig, members: Sequence[Sequence[int]]
) -> CreatedMatch:
    """A match with one team per entry in `members`, in throwing order."""
    with transaction(conn):
        return create_match(conn, config, tuple(TeamSpec(tuple(m)) for m in members))


def throw_labels(
    conn: sqlite3.Connection, leg_id: int, labels: Sequence[str], *, prefix: str = "k"
) -> GameState:
    """Throw every label into one leg, returning the state after the last."""
    state = play.state(conn, leg_id)
    for index, label in enumerate(labels):
        state = play.throw(
            conn,
            leg_id=leg_id,
            dart=Throw.parse(label),
            client_dart_id=f"{prefix}-{leg_id}-{index}",
        )
    return state


def throw_into_match(
    conn: sqlite3.Connection, leg_id: int, labels: Sequence[str], *, prefix: str = "m"
) -> GameState:
    """Throw every label into whichever leg is currently open.

    Feeding a whole match through the service the way a client would: when a
    leg finishes, the next dart goes into the leg the service opened behind it.
    """
    state = play.state(conn, leg_id)
    for index, label in enumerate(labels):
        assert state.active_leg_id is not None
        state = play.throw(
            conn,
            leg_id=state.active_leg_id,
            dart=Throw.parse(label),
            client_dart_id=f"{prefix}-{index}",
        )
    return state


def pure_leg(
    rules: Rules, starting: int, members: Sequence[Sequence[int]], labels: Sequence[str]
) -> LegState:
    """The same leg through the pure engine, with nothing persistent involved."""
    teams = tuple(EngineTeam(tuple(str(p) for p in team)) for team in members)
    return pure_replay(
        LegConfig(rules, starting), teams, tuple(Throw.parse(label) for label in labels)
    )


def x01_positions(state: GameState) -> list[tuple[int | None, bool | None, int]]:
    """Each team's x01 position in the current leg, in team order."""
    return [(t.remaining, t.is_open, t.darts_thrown) for t in state.current_leg.teams]


def engine_positions(leg: LegState) -> list[tuple[int | None, bool | None, int]]:
    """The same three numbers off a pure `LegState`."""
    return [(t.remaining, t.is_open, t.darts) for t in leg.teams]  # type: ignore[union-attr]


@dataclass(frozen=True)
class Snapshot:
    """Everything a leg derives, with ids reduced to positions."""

    visits: list[tuple[int, int, int, int, int, bool, bool]]
    darts: list[tuple[int, int, int, int, int, bool, bool, bool]]
    effects: list[tuple[int, int | None, int, int, bool]]
    events: list[tuple[int, int, int]]


def snapshot(conn: sqlite3.Connection, leg_id: int, team_ids: Sequence[int]) -> Snapshot:
    """Reduce a leg to its derived values, free of database ids.

    Team ids become indices into `team_ids` and dart ids become `seq_in_leg`,
    so the same leg played into two different matches snapshots identically.
    """
    darts = darts_for_leg(conn, leg_id)
    seq_of = {dart.id: dart.seq_in_leg for dart in darts}
    effects = cricket_effects_for_leg(conn, leg_id)
    return Snapshot(
        visits=[
            (
                v.visit_index,
                team_ids.index(v.team_id),
                v.team_visit_index,
                v.score_before,
                v.score_after,
                v.is_bust,
                v.is_complete,
            )
            for v in visits_for_leg(conn, leg_id)
        ],
        darts=[
            (
                d.seq_in_leg,
                d.dart_index,
                team_ids.index(d.team_id),
                d.segment,
                d.multiplier,
                d.counted,
                d.caused_bust,
                d.was_checkout_attempt,
            )
            for d in darts
        ],
        effects=sorted(
            (seq_of[e.dart_id], e.target, e.counted_marks, e.surplus_marks, e.wasted)
            for e in effects.values()
        ),
        events=sorted(
            (seq_of[a.dart_id], team_ids.index(a.recipient_team_id), a.points)
            for a in point_awards_for_leg(conn, leg_id)
        ),
    )
