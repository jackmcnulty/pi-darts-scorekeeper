"""Cricket through the service, in all three variants.

Mark accounting is identical across the variants and only the surplus differs,
so the tests that matter are the ones about where the surplus goes: to the
thrower under `standard`, to every still-open opponent under `cutthroat`, and
to nobody at all under `quick`.
"""

import sqlite3

import pytest
from servicefixtures import cricket, make_match, pure_leg, throw_labels

from darts.engine.cricket import TARGETS, CricketConfig, Variant
from darts.repo.darts import cricket_effects_for_leg, darts_for_leg, point_awards_for_leg
from darts.repo.legstate import leg_state_for

#: Close 20, 19, 18, 17, 16, 15 and the bull in seven visits, with one surplus.
CLOSE_OUT = [
    "T20", "T20", "T19",
    "MISS", "MISS", "MISS",
    "T19", "T18", "T18",
    "MISS", "MISS", "MISS",
    "T17", "T17", "T16",
    "MISS", "MISS", "MISS",
    "T16", "T15", "T15",
    "MISS", "MISS", "MISS",
    "BULL", "BULL",
]  # fmt: skip


@pytest.mark.parametrize("variant", ["standard", "cutthroat", "quick"])
def test_a_replayed_cricket_leg_matches_the_pure_engine(
    db: sqlite3.Connection, players: list[int], variant: str
) -> None:
    members = ((players[0],), (players[1],))
    created = make_match(db, cricket(variant), members)

    state = throw_labels(db, created.leg_id, CLOSE_OUT)

    expected = pure_leg(CricketConfig(Variant(variant)), 0, members, CLOSE_OUT)
    assert [(t.points, t.marks) for t in state.current_leg.teams] == [
        (t.points, t.marks_by_target)
        for t in expected.teams  # type: ignore[union-attr]
    ]
    assert state.current_leg.darts_thrown == len(expected.darts)


def test_every_cricket_dart_records_an_effect(db: sqlite3.Connection, players: list[int]) -> None:
    """Including a dart that hit no target: that is a NULL target, not a gap."""
    created = make_match(db, cricket("standard"), ((players[0],), (players[1],)))

    throw_labels(db, created.leg_id, ["T20", "12", "MISS"])

    darts = darts_for_leg(db, created.leg_id)
    effects = cricket_effects_for_leg(db, created.leg_id)
    assert len(effects) == len(darts) == 3
    assert [effects[d.id].target for d in darts] == [20, None, None]
    assert [effects[d.id].counted_marks for d in darts] == [3, 0, 0]


def test_standard_surplus_pays_the_thrower(db: sqlite3.Connection, players: list[int]) -> None:
    created = make_match(db, cricket("standard"), ((players[0],), (players[1],)))

    state = throw_labels(db, created.leg_id, ["T20", "T20"])

    awards = point_awards_for_leg(db, created.leg_id)
    assert [(a.recipient_team_id, a.points) for a in awards] == [(created.team_ids[0], 60)]
    assert state.current_leg.teams[0].points == 60
    assert state.current_leg.teams[1].points == 0


def test_cutthroat_surplus_pays_every_open_opponent_in_full(
    db: sqlite3.Connection, players: list[int]
) -> None:
    """Three surplus marks on 20 cost two opponents 60 each, not 30."""
    members = ((players[0],), (players[1],), (players[2],))
    created = make_match(db, cricket("cutthroat"), members)

    state = throw_labels(db, created.leg_id, ["T20", "T20"])

    awards = point_awards_for_leg(db, created.leg_id)
    assert sorted((a.recipient_team_id, a.points) for a in awards) == [
        (created.team_ids[1], 60),
        (created.team_ids[2], 60),
    ]
    assert [t.points for t in state.current_leg.teams] == [0, 60, 60]


def test_quick_surplus_pays_nobody_and_is_marked_wasted(
    db: sqlite3.Connection, players: list[int]
) -> None:
    created = make_match(db, cricket("quick"), ((players[0],), (players[1],)))

    state = throw_labels(db, created.leg_id, ["T20", "T20"])

    assert point_awards_for_leg(db, created.leg_id) == []
    effects = cricket_effects_for_leg(db, created.leg_id)
    surplus = [e for e in effects.values() if e.surplus_marks]
    assert [(e.surplus_marks, e.wasted) for e in surplus] == [(3, True)]
    assert [t.points for t in state.current_leg.teams] == [0, 0]


def test_quick_ends_on_the_closing_dart(db: sqlite3.Connection, players: list[int]) -> None:
    """Closing is not winning under the other two; under `quick` it is."""
    created = make_match(db, cricket("quick"), ((players[0],), (players[1],)))

    state = throw_labels(db, created.leg_id, CLOSE_OUT)

    assert state.current_leg.winner_team_id == created.team_ids[0]
    assert state.current_leg.next_thrower is None


def test_the_cricket_cache_holds_marks_and_points(
    db: sqlite3.Connection, players: list[int]
) -> None:
    created = make_match(db, cricket("standard"), ((players[0],), (players[1],)))

    throw_labels(db, created.leg_id, ["T20", "T20", "T19"])

    cached = leg_state_for(db, created.leg_id)
    assert [c.remaining for c in cached] == [None, None]
    assert [c.is_open for c in cached] == [None, None]
    assert cached[0].points == 60
    assert cached[0].marks == {20: 3, 19: 3, 18: 0, 17: 0, 16: 0, 15: 0, 25: 0}
    assert cached[1].marks == dict.fromkeys(TARGETS, 0)
    assert [c.darts_thrown for c in cached] == [3, 0]
