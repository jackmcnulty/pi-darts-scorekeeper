"""GameConfig: that it rejects whatever the schema rejects, and writes one truth twice."""

import itertools
import json
import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from repofixtures import count

from darts.db.connection import transaction
from darts.repo.config import PROMOTED_COLUMNS, GameConfig
from darts.repo.matches import TeamSpec, create_match

VALID_X01: dict[str, object] = {
    "game_type": "x01",
    "best_of": 3,
    "start_score": 501,
    "in_rule": "double",
    "out_rule": "double",
}
VALID_CRICKET: dict[str, object] = {"game_type": "cricket", "best_of": 3, "variant": "standard"}


@st.composite
def game_configs(draw: st.DrawFn) -> GameConfig:
    """Every configuration the model considers valid, and only those.

    `best_of` is generated as `2n + 1` rather than filtered, so Hypothesis never
    has to discard an example to satisfy the odd-number rule.
    """
    common: dict[str, object] = {
        "best_of": 2 * draw(st.integers(min_value=0, max_value=20)) + 1,
        "start_rule": draw(
            st.sampled_from(["alternate", "loser_starts", "winner_starts", "fixed"])
        ),
        "fixed_team": 0,
    }
    if draw(st.booleans()):
        return GameConfig(
            game_type="x01",
            start_score=draw(st.integers(min_value=1, max_value=1001)),
            in_rule=draw(st.sampled_from(["straight", "double", "master"])),
            out_rule=draw(st.sampled_from(["straight", "double", "master"])),
            **common,
        )
    return GameConfig(
        game_type="cricket",
        variant=draw(st.sampled_from(["standard", "cutthroat", "quick"])),
        **common,
    )


@given(config=game_configs())
@settings(
    deadline=None,
    # The database fixture is function-scoped and Hypothesis reuses it across
    # examples. Safe here: each example inserts its own match and reads back
    # only that match, so examples cannot observe each other's rows.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_config_columns_match_json(db: sqlite3.Connection, config: GameConfig) -> None:
    """For every valid config, config_json and the promoted columns agree.

    The criterion read literally: write a real match, then read both
    representations back out of SQLite and compare them column by column.
    """
    db.execute("INSERT OR IGNORE INTO players(id, display_name) VALUES (1, 'Ana')")
    with transaction(db):
        created = create_match(db, config, [TeamSpec(player_ids=(1,))])

    row = db.execute(
        f"SELECT config_json, {', '.join(PROMOTED_COLUMNS)} FROM matches WHERE id = ?",
        (created.match_id,),
    ).fetchone()
    stored = json.loads(row["config_json"])

    for column in PROMOTED_COLUMNS:
        assert row[column] == stored[column] == config.columns[column], column
    assert GameConfig.from_json(row["config_json"]) == config


@given(config=game_configs())
@settings(deadline=None)
def test_config_round_trips_through_json(config: GameConfig) -> None:
    """The two fields with no promoted column survive the round trip too."""
    restored = GameConfig.from_json(config.to_json())
    assert restored == config
    assert restored.start_rule == config.start_rule
    assert restored.fixed_team == config.fixed_team


# Every combination below is offered both to the model and to a raw INSERT. The
# point is not that any particular one is valid, but that the two always agree
# on which are: the model is meant to mirror the big CHECK in 0001_init.sql, and
# a mirror checked only by eye stops being a mirror.
_PRODUCT = list(
    itertools.product(
        ["x01", "cricket"],  # game_type
        [None, "standard", "nonsense"],  # variant
        [None, 501, 0],  # start_score
        [None, "double", "nonsense"],  # in_rule
        [None, "double"],  # out_rule
        [3, 2, 0],  # best_of
    )
)


@pytest.mark.parametrize(
    ("game_type", "variant", "start", "in_rule", "out_rule", "best_of"), _PRODUCT
)
def test_model_agrees_with_schema_check(
    db: sqlite3.Connection,
    game_type: str,
    variant: str | None,
    start: int | None,
    in_rule: str | None,
    out_rule: str | None,
    best_of: int,
) -> None:
    try:
        GameConfig(
            game_type=game_type,
            variant=variant,
            start_score=start,
            in_rule=in_rule,
            out_rule=out_rule,
            best_of=best_of,
        )
        model_accepts = True
    except ValidationError:
        model_accepts = False

    try:
        db.execute(
            "INSERT INTO matches(config_json, game_type, variant, start_score, in_rule, "
            "out_rule, best_of) VALUES ('{}', ?, ?, ?, ?, ?, ?)",
            (game_type, variant, start, in_rule, out_rule, best_of),
        )
        schema_accepts = True
    except sqlite3.IntegrityError:
        schema_accepts = False

    assert model_accepts == schema_accepts


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"out_rule": "double"}, id="cricket-with-an-out-rule"),
        pytest.param({"in_rule": "straight"}, id="cricket-with-an-in-rule"),
        pytest.param({"start_score": 501}, id="cricket-with-a-start-score"),
        pytest.param({"variant": None}, id="cricket-without-a-variant"),
        pytest.param({"variant": "nonsense"}, id="cricket-with-an-unknown-variant"),
    ],
)
def test_cricket_rejects_x01_fields(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GameConfig(**{**VALID_CRICKET, **changes})


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"out_rule": None}, id="x01-without-an-out-rule"),
        pytest.param({"in_rule": None}, id="x01-without-an-in-rule"),
        pytest.param({"start_score": None}, id="x01-without-a-start-score"),
        pytest.param({"start_score": 0}, id="x01-starting-from-zero"),
        pytest.param({"variant": "standard"}, id="x01-with-a-variant"),
        pytest.param({"out_rule": "nonsense"}, id="x01-with-an-unknown-out-rule"),
    ],
)
def test_x01_requires_its_own_fields(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GameConfig(**{**VALID_X01, **changes})


@pytest.mark.parametrize("best_of", [0, -1, 2, 4])
def test_best_of_must_be_odd_and_positive(best_of: int) -> None:
    with pytest.raises(ValidationError):
        GameConfig(**{**VALID_X01, "best_of": best_of})


def test_unknown_key_is_rejected() -> None:
    """A stale client sending a setting we never had must fail, not be ignored."""
    with pytest.raises(ValidationError):
        GameConfig(**{**VALID_X01, "double_in": True})


def test_config_is_frozen() -> None:
    config = GameConfig(**VALID_X01)
    with pytest.raises(ValidationError):
        config.best_of = 5


def test_negative_fixed_team_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GameConfig(**{**VALID_X01, "fixed_team": -1})


def test_unknown_game_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GameConfig(**{**VALID_X01, "game_type": "killer"})


def test_invalid_config_writes_nothing(db: sqlite3.Connection) -> None:
    """The criterion's exact wording: rejected *before any row is written*."""
    with pytest.raises(ValidationError):
        GameConfig(**{**VALID_X01, "best_of": 2})
    assert count(db, "matches") == 0
