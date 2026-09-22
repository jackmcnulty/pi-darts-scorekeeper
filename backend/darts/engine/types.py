"""Frozen, rule-free values shared by rotation and replay."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Team:
    """Members in visit order; strings are caller-owned member identifiers."""

    members: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError("a team must have at least one member")


@dataclass(frozen=True, slots=True)
class Thrower:
    team_index: int
    member_index: int
    member: str
