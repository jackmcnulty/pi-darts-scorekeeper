"""Derive legs and matches from recorded throws; undo drops a throw and replays.

There is no cached or mutable public game state. Visits contain only actual
throws: busts advance rotation immediately; throws after a win are rejected.
Member visit counters reset at the beginning of each leg.
"""

from dataclasses import dataclass
from typing import cast

from darts.engine import cricket, x01
from darts.engine.rotation import StartRule, starting_team, thrower_for
from darts.engine.throws import Throw
from darts.engine.types import Team, Thrower

Rules = x01.X01Config | cricket.CricketConfig
TeamState = x01.X01TeamState | cricket.CricketTeamState
VisitOutcome = x01.VisitOutcome | cricket.VisitOutcome


@dataclass(frozen=True, slots=True)
class LegConfig:
    rules: Rules
    starting_team: int = 0


@dataclass(frozen=True, slots=True)
class Visit:
    thrower: Thrower
    outcome: VisitOutcome
    complete: bool


@dataclass(frozen=True, slots=True)
class LegState:
    darts: tuple[Throw, ...]
    teams: tuple[TeamState, ...]
    visits: tuple[Visit, ...]
    next_thrower: Thrower | None
    darts_left: int
    winner: int | None


def replay(config: LegConfig, teams: tuple[Team, ...], darts: tuple[Throw, ...]) -> LegState:
    """Rebuild an entire leg, including partial visits and reverted busts."""
    if not teams or not 0 <= config.starting_team < len(teams):
        raise ValueError("a valid starting team is required")
    rules = config.rules
    states: list[TeamState]
    if isinstance(rules, x01.X01Config):
        states = [x01.initial_state(rules) for _ in teams]
    else:
        states = [cricket.initial_state(rules) for _ in teams]
    visits: list[Visit] = []
    counts = [0] * len(teams)
    active = config.starting_team
    offset = 0
    winner = None
    next_thrower: Thrower | None = thrower_for(teams, active, 0)
    darts_left = 3
    while offset < len(darts):
        throws = darts[offset : offset + 3]
        state = states[active]
        outcome: VisitOutcome
        if isinstance(rules, x01.X01Config):
            assert isinstance(state, x01.X01TeamState)
            outcome = x01.apply_visit(state, throws, rules)
            won = outcome.checkout
            stopped = outcome.bust is not None or won
        else:
            assert isinstance(state, cricket.CricketTeamState)
            indices = tuple(i for i in range(len(teams)) if i != active)
            # Every state was initialized from this same leg's rules above.
            cricket_opponents = cast(
                tuple[cricket.CricketTeamState, ...], tuple(states[i] for i in indices)
            )
            outcome = cricket.apply_visit(state, throws, rules, cricket_opponents)
            for i, updated in zip(indices, outcome.opponents, strict=True):
                states[i] = updated
            won = outcome.win
            stopped = won
        states[active] = outcome.state
        used = len(outcome.darts)
        complete = stopped or used == 3
        visits.append(Visit(thrower_for(teams, active, counts[active]), outcome, complete))
        offset += used
        if won:
            winner = active
            next_thrower = None
            darts_left = 0
            if offset != len(darts):
                raise ValueError("recorded darts continue after the leg was won")
        elif complete:
            counts[active] += 1
            active = (active + 1) % len(teams)
            next_thrower = thrower_for(teams, active, counts[active])
            darts_left = 3
        else:
            next_thrower = visits[-1].thrower
            darts_left = 3 - used
    return LegState(darts, tuple(states), tuple(visits), next_thrower, darts_left, winner)


def undo(config: LegConfig, teams: tuple[Team, ...], state: LegState) -> LegState:
    """Undo one actual dart. On an empty leg this returns the opening state."""
    return replay(config, teams, state.darts[:-1])


@dataclass(frozen=True, slots=True)
class MatchConfig:
    rules: Rules
    best_of: int
    start_rule: StartRule = StartRule.ALTERNATE
    fixed_team: int = 0

    def __post_init__(self) -> None:
        if self.best_of < 1 or self.best_of % 2 == 0:
            raise ValueError("best_of must be a positive odd number")


@dataclass(frozen=True, slots=True)
class MatchState:
    legs: tuple[LegState, ...]
    legs_won: tuple[int, ...]
    current_leg: LegState | None
    winner: int | None


def replay_match(
    config: MatchConfig, teams: tuple[Team, ...], legs: tuple[tuple[Throw, ...], ...]
) -> MatchState:
    """Tally replayed legs; expose a fresh opening leg until the match is won.

    Only the final supplied leg may be unfinished. No recorded leg may follow
    match completion. Multi-team matches still require the same winning tally;
    they can consequently take more than best_of total legs.
    """
    tally = [0] * len(teams)
    results: list[LegState] = []
    previous_winner = None
    starter = 0
    winner = None
    current = None
    for index in range(len(legs) + 1):
        starter = starting_team(
            len(teams), index, config.start_rule, config.fixed_team, previous_winner, starter
        )
        leg = replay(
            LegConfig(config.rules, starter), teams, legs[index] if index < len(legs) else ()
        )
        if index == len(legs):
            current = leg
            break
        results.append(leg)
        if leg.winner is None:
            if index != len(legs) - 1:
                raise ValueError("an unfinished leg cannot precede another leg")
            current = leg
            break
        previous_winner = leg.winner
        tally[leg.winner] += 1
        if tally[leg.winner] == (config.best_of + 1) // 2:
            if index != len(legs) - 1:
                raise ValueError("recorded legs continue after the match was won")
            winner = leg.winner
            break
    return MatchState(tuple(results), tuple(tally), current, winner)
