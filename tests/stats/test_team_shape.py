"""Team play must not distort an individual statistic.

The criterion: a 2v2 match and four solo matches *containing identical darts*
produce identical per-player statistics. `shapes.py` seats one visit script two
ways so that the darts are provably the same and only the seating differs.

The exception is explicit and agreed: a leg is won by a team. Four solo matches
award four wins where one 2v2 awards one win to two players, and no arrangement
of the numbers makes those equal. So the scoring metrics are asserted identical
and the win metrics are asserted *different*, on purpose -- a test that quietly
left them out would not be recording the decision.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from shapes import SCRIPT, TEAMS_2V2, four_solo, two_versus_two

from darts.stats.queries import StatsFilter
from darts.stats.report import PlayerStats, player_stats

#: The four players who throw in both arrangements. The fifth is the spare
#: opponent the solo matches need and nobody asserts about.
THROWERS = (1, 2, 3, 4)


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[sqlite3.Connection, ...]]:
    root: Path = tmp_path_factory.mktemp("shapes")
    team = two_versus_two(str(root / "team.db"))
    solo = four_solo(str(root / "solo.db"))
    yield team, solo
    team.close()
    solo.close()


def stats(conn: sqlite3.Connection, player_id: int) -> PlayerStats:
    return player_stats(conn, player_id, f"P{player_id}", False, StatsFilter())


def test_the_two_shapes_really_do_contain_the_same_darts(
    pair: tuple[sqlite3.Connection, ...],
) -> None:
    """Guard the guard: if the fixture drifted, the comparison below proves nothing."""
    team, solo = pair
    columns = "player_id, segment, multiplier, counted, caused_bust, was_checkout_attempt"
    for conn in (team, solo):
        rows = conn.execute(
            f"SELECT {columns} FROM v_darts WHERE player_id IN (1,2,3,4) "
            "ORDER BY player_id, leg_id, seq_in_leg"
        ).fetchall()
        assert len(rows) == sum(len(labels) for _, labels in SCRIPT)
    assert [tuple(r) for r in team.execute(f"SELECT {columns} FROM v_darts ORDER BY 1,2,3")] == [
        tuple(r) for r in solo.execute(f"SELECT {columns} FROM v_darts ORDER BY 1,2,3")
    ]
    # And they really are seated differently.
    assert team.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert solo.execute("SELECT count(*) FROM matches").fetchone()[0] == len(THROWERS)
    assert max(len(members) for members in TEAMS_2V2) == 2


@pytest.mark.parametrize("player_id", THROWERS)
def test_team_play_does_not_affect_individual_stats(
    pair: tuple[sqlite3.Connection, ...], player_id: int
) -> None:
    """Every scoring metric is identical between the two arrangements."""
    team, solo = pair
    blank = {"legs_played": 0, "legs_won": 0, "matches_played": 0, "matches_won": 0}
    from_team = replace(stats(team, player_id), **blank)
    from_solo = replace(stats(solo, player_id), **blank)
    assert from_team == from_solo


@pytest.mark.parametrize("player_id", THROWERS)
def test_the_first_nine_is_the_players_own_darts_not_the_legs(
    pair: tuple[sqlite3.Connection, ...], player_id: int
) -> None:
    """The reading that makes the criterion hold, and the one that would break it.

    Each player throws nine darts in the 2v2 leg before anyone throws a tenth,
    but they are spread across the leg: in a 2v2 the leg's own first nine darts
    belong to three *different* players. Counting those would make a player's
    first-nine average depend on who they were partnered with.
    """
    team, solo = pair
    assert stats(team, player_id).x01.first_nine_average == (
        stats(solo, player_id).x01.first_nine_average
    )
    # The player threw more than nine darts, so this is a real distinction.
    assert stats(team, player_id).x01.darts_thrown > 9
    assert stats(team, player_id).x01.first_nine_darts == 9
    assert (
        stats(team, player_id).x01.first_nine_average
        != stats(team, player_id).x01.three_dart_average
    )


def test_the_leg_and_match_wins_are_the_documented_exception(
    pair: tuple[sqlite3.Connection, ...],
) -> None:
    """One 2v2 win shared by two players; four solo wins, one each."""
    team, solo = pair
    # In the 2v2, Ana and Cal are partners and take the leg and the match
    # together; Ben and Dee take neither.
    assert [stats(team, p).legs_won for p in THROWERS] == [1, 0, 1, 0]
    assert [stats(team, p).matches_won for p in THROWERS] == [1, 0, 1, 0]
    # Seated solo, every one of them wins their own.
    assert [stats(solo, p).legs_won for p in THROWERS] == [1, 1, 1, 1]
    assert [stats(solo, p).matches_won for p in THROWERS] == [1, 1, 1, 1]
