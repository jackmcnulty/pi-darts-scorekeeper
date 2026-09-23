"""Turning stored rows into engine types, and engine answers back into rows.

Everything here is derivation: it reads, it replays, and it says what a row
*should* contain. Nothing here writes. `play` uses it to decide what to write
and `verify` uses it to decide whether what is written is still right, which is
what keeps the two from disagreeing about the same question.

A leg is replayed against its own `legs.starting_team_id` rather than against a
starter re-derived from the match's start rule. The column is written when the
leg is opened and is the only record of who actually threw first; see the
`play` module docstring for why that is the authority.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from darts.engine import cricket, x01
from darts.engine.checkout import was_checkout_attempt
from darts.engine.replay import LegConfig, LegState, Rules, VisitOutcome
from darts.engine.replay import replay as replay_leg
from darts.engine.throws import Throw
from darts.engine.types import Team as EngineTeam
from darts.repo.config import GameConfig, GameType
from darts.repo.darts import Dart, darts_for_leg
from darts.repo.legs import Leg, get_leg
from darts.repo.legstate import TeamCache
from darts.repo.matches import Match, get_match


@dataclass(frozen=True, slots=True)
class Board:
    """One leg's surroundings, loaded once per call.

    `teams` is the engine's view -- members as player-id strings, since the
    engine treats them as opaque identifiers -- positionally aligned to
    `match.teams`, so an engine team index and a `match.teams` index are the
    same number and `team_ids` turns either into a database id.
    """

    match: Match
    leg: Leg
    rules: Rules
    teams: tuple[EngineTeam, ...]
    config: LegConfig
    team_ids: tuple[int, ...]

    @property
    def is_x01(self) -> bool:
        return isinstance(self.rules, x01.X01Config)


@dataclass(frozen=True, slots=True)
class VisitVerdict:
    """The `visits` columns a further dart can change, read off the replay."""

    score_before: int
    score_after: int
    is_bust: bool
    is_complete: bool


@dataclass(frozen=True, slots=True)
class DartFlags:
    """The `darts` columns that are a judgement rather than an observation."""

    counted: bool
    caused_bust: bool
    was_checkout_attempt: bool


def rules_for(config: GameConfig) -> Rules:
    """The engine's rules object for a stored configuration.

    `GameConfig` has already refused any combination the game type does not
    allow, so the fields this reads cannot be None; the asserts are for mypy,
    which does not know the validator ran.
    """
    if config.game_type is GameType.X01:
        assert config.start_score is not None
        assert config.in_rule is not None and config.out_rule is not None
        return x01.X01Config(config.start_score, config.in_rule, config.out_rule)
    assert config.variant is not None
    return cricket.CricketConfig(config.variant)


def load(conn: sqlite3.Connection, leg_id: int) -> Board:
    """Everything a leg needs, in one read. Raises `NotFoundError` if absent."""
    leg = get_leg(conn, leg_id)
    match = get_match(conn, leg.match_id)
    team_ids = tuple(team.id for team in match.teams)
    rules = rules_for(match.config)
    return Board(
        match=match,
        leg=leg,
        rules=rules,
        teams=tuple(
            EngineTeam(tuple(str(member.player_id) for member in team.members))
            for team in match.teams
        ),
        config=LegConfig(rules, team_ids.index(leg.starting_team_id)),
        team_ids=team_ids,
    )


def throws_of(darts: Sequence[Dart]) -> tuple[Throw, ...]:
    """The board positions of some stored darts, in the order given."""
    return tuple(Throw(dart.segment, dart.multiplier) for dart in darts)


def replay(board: Board, throws: tuple[Throw, ...]) -> LegState:
    """The leg these throws make, against this leg's own starting team."""
    return replay_leg(board.config, board.teams, throws)


def replay_stored(conn: sqlite3.Connection, board: Board) -> LegState:
    """The leg as its recorded darts make it."""
    return replay(board, throws_of(darts_for_leg(conn, board.leg.id)))


def visit_verdict(outcome: VisitOutcome) -> VisitVerdict:
    """What the visit row should say now.

    `score_before` and `score_after` are x01 remaining in an x01 leg and the
    throwing team's points in a cricket one; the column means whichever the leg
    is playing. A visit is complete once it has had three darts or has been
    stopped -- by a bust or a checkout in x01, by a win in cricket.
    """
    if isinstance(outcome, x01.VisitOutcome):
        return VisitVerdict(
            score_before=outcome.score_before,
            score_after=outcome.score_after,
            is_bust=outcome.is_bust,
            is_complete=outcome.is_bust or outcome.checkout or len(outcome.darts) == 3,
        )
    return VisitVerdict(
        score_before=outcome.points_before,
        score_after=outcome.points_after,
        is_bust=False,
        is_complete=outcome.win or len(outcome.darts) == 3,
    )


def dart_flags(board: Board, outcome: VisitOutcome, index: int, prior: object) -> DartFlags:
    """The judgements on dart `index` of a replayed visit.

    Cricket has no bust, no in-rule and no checkout, so every dart counts and
    what it actually did lives in its `cricket_dart_effects` row instead.

    `prior` is the throwing team's state immediately before that dart, which is
    what #7 judges a checkout attempt against. It has to be supplied rather than
    read off `outcome`, because a bust rewinds every dart's reported state to
    the start of the visit.
    """
    if not isinstance(outcome, x01.VisitOutcome):
        return DartFlags(counted=True, caused_bust=False, was_checkout_attempt=False)
    assert isinstance(prior, x01.X01TeamState)
    assert isinstance(board.rules, x01.X01Config)
    dart = outcome.darts[index]
    return DartFlags(
        counted=dart.counted,
        caused_bust=dart.bust is not None,
        was_checkout_attempt=prior.is_open
        and was_checkout_attempt(prior.remaining, board.rules.out_rule),
    )


def counted_flags(outcome: VisitOutcome) -> tuple[bool, ...]:
    """Whether each dart of a replayed visit counts, in throwing order."""
    if isinstance(outcome, x01.VisitOutcome):
        return tuple(dart.counted for dart in outcome.darts)
    return (True,) * len(outcome.darts)


def darts_thrown(leg: LegState, team_index: int) -> int:
    """Darts one team actually threw in a leg, busts and misses included."""
    return sum(
        len(visit.outcome.darts) for visit in leg.visits if visit.thrower.team_index == team_index
    )


def team_cache(board: Board, leg: LegState, team_index: int) -> TeamCache:
    """One team's position, counted off the replay rather than off the rows."""
    team_state = leg.teams[team_index]
    thrown = darts_thrown(leg, team_index)
    if isinstance(team_state, x01.X01TeamState):
        return TeamCache(
            team_id=board.team_ids[team_index],
            remaining=team_state.remaining,
            is_open=team_state.is_open,
            darts_thrown=thrown,
            points=0,
        )
    return TeamCache(
        team_id=board.team_ids[team_index],
        remaining=None,
        is_open=None,
        darts_thrown=thrown,
        points=team_state.points,
        marks=team_state.marks_by_target,
    )


def cache_rows(board: Board, leg: LegState) -> list[TeamCache]:
    """The cache rows this leg should hold, in `team_id` order.

    Empty for a leg that should hold none: one nobody has thrown into, which
    has nothing to resume, and one that has been won, which is reconstructed
    from its darts. That invariant is #13's and is what the seeded fixture
    stores; `play.rebuild_caches` turns an empty list into a delete.
    """
    if leg.winner is not None or not leg.darts:
        return []
    rows = [team_cache(board, leg, i) for i in range(len(board.teams))]
    return sorted(rows, key=lambda row: row.team_id)
