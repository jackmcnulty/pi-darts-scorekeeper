"""The validated description of a match, and the one source of both writes.

`matches` stores its configuration twice: once as JSON in `config_json`, and
once in the promoted columns the indexes and the stats layer read. Storing a
fact twice is how the two come to disagree, so exactly one object produces
both. `create_match` takes a `GameConfig`, asks it for `columns` and for
`to_json`, and writes what it is given.

The model mirrors the big CHECK in `0001_init.sql` deliberately and exactly: an
x01 match carries a start score and both rules and no variant, a cricket match
carries a variant and none of the others, and `best_of` is odd and positive.
The CHECK stays as the backstop, but it is never the thing that reports the
error -- #14 requires an invalid config to be rejected *before any row is
written*, and by the time SQLite complains a statement has already run.
`tests/repo/test_config.py` proves the two agree rather than assuming it.

The rule and variant names are the engine's own enums, so the strings written
to the database cannot drift from the strings the engine dispatches on.

Two fields have no promoted column and live in `config_json` alone:
`start_rule` and `fixed_team`, which decide who throws first in each leg.
`create_match` needs them for leg 0; #15 reads them back for legs 1 and up.
"""

import json
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from darts.engine.cricket import Variant
from darts.engine.rotation import StartRule
from darts.engine.x01 import Rule

#: The promoted columns of `matches`, in the order `columns` returns them.
PROMOTED_COLUMNS: tuple[str, ...] = (
    "game_type",
    "variant",
    "start_score",
    "in_rule",
    "out_rule",
    "best_of",
)


class GameType(StrEnum):
    """Which family of rules a match is played under."""

    X01 = "x01"
    CRICKET = "cricket"


class GameConfig(BaseModel):
    """Everything a match is configured with, validated once at creation.

    Frozen and `extra="forbid"`: an unknown key is a typo or a stale client,
    and silently dropping it would mean the match is not the match that was
    asked for.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    game_type: GameType
    #: Legs needed to win is best_of // 2 + 1, which requires an odd best_of.
    best_of: int = Field(ge=1)
    variant: Variant | None = None
    start_score: int | None = Field(default=None, ge=1)
    in_rule: Rule | None = None
    out_rule: Rule | None = None
    #: How each leg's starting team is chosen. No promoted column.
    start_rule: StartRule = StartRule.ALTERNATE
    #: The team `start_rule` starts from, where the rule consults one.
    fixed_team: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_odd_best_of(self) -> Self:
        if self.best_of % 2 == 0:
            raise ValueError(f"best_of must be odd; got {self.best_of!r}")
        return self

    @model_validator(mode="after")
    def _check_fields_match_game_type(self) -> Self:
        """Each game type requires its own fields and forbids the other's."""
        if self.game_type is GameType.X01:
            required: tuple[str, ...] = ("start_score", "in_rule", "out_rule")
            forbidden: tuple[str, ...] = ("variant",)
        else:
            required = ("variant",)
            forbidden = ("start_score", "in_rule", "out_rule")
        if missing := [name for name in required if getattr(self, name) is None]:
            raise ValueError(f"{self.game_type} requires {', '.join(missing)}")
        if present := [name for name in forbidden if getattr(self, name) is not None]:
            raise ValueError(f"{self.game_type} does not take {', '.join(present)}")
        return self

    @property
    def columns(self) -> dict[str, object]:
        """The promoted columns of `matches`, ready to bind to an INSERT.

        Taken from the same dump `to_json` serialises, so the two writes cannot
        differ even in the representation of an enum.
        """
        dumped = self.model_dump(mode="json")
        return {name: dumped[name] for name in PROMOTED_COLUMNS}

    def to_json(self) -> str:
        """`config_json`, with sorted keys so equal configs serialise equally."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "GameConfig":
        """Re-validate a stored `config_json`. Round-trips `to_json` exactly."""
        return cls.model_validate_json(raw)
