"""Pure visit rotation and leg starter selection."""

from enum import StrEnum

from darts.engine.types import Team, Thrower


class StartRule(StrEnum):
    ALTERNATE = "alternate"
    LOSER_STARTS = "loser_starts"
    WINNER_STARTS = "winner_starts"
    FIXED = "fixed"


def thrower_for(teams: tuple[Team, ...], team_index: int, team_visits: int) -> Thrower:
    """A member keeps all darts of a visit, even when that visit ends early."""
    if not 0 <= team_index < len(teams) or team_visits < 0:
        raise ValueError("invalid team index or visit count")
    members = teams[team_index].members
    index = team_visits % len(members)
    return Thrower(team_index, index, members[index])


def starting_team(
    n_teams: int,
    leg_index: int,
    rule: StartRule = StartRule.ALTERNATE,
    fixed_team: int = 0,
    previous_winner: int | None = None,
    previous_starter: int = 0,
) -> int:
    """First leg starts at fixed_team, except alternate always starts at zero.

    For multiple losers, choose the next non-winner after the previous starter.
    """
    if n_teams < 1 or leg_index < 0:
        raise ValueError("positive team count and nonnegative leg index required")
    if not 0 <= fixed_team < n_teams or not 0 <= previous_starter < n_teams:
        raise ValueError("starter outside team range")
    if previous_winner is not None and not 0 <= previous_winner < n_teams:
        raise ValueError("winner outside team range")
    if rule is StartRule.ALTERNATE:
        return leg_index % n_teams
    if rule is StartRule.FIXED or leg_index == 0:
        return fixed_team
    if previous_winner is None:
        raise ValueError("this start rule requires the previous winner")
    if rule is StartRule.WINNER_STARTS:
        return previous_winner
    if n_teams == 1:
        return 0
    candidate = (previous_starter + 1) % n_teams
    return (candidate + 1) % n_teams if candidate == previous_winner else candidate
