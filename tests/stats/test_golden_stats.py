"""Every metric, against hand-verified expected values for the #13 seed.

Two layers, because either alone would be weaker than it looks:

* `test_every_metric_matches_the_golden_file` compares the whole report against
  `tests/fixtures/expected_stats.json`, which `tests/fixtures/expected.py`
  derives from the seeded rows in plain Python without touching `darts.stats`.
  That catches a query that computes the wrong thing.
* The tests after it pin a sample of the same numbers to arithmetic worked out
  by hand and written into the assertion. That catches a metric *defined*
  wrongly, which the derivation would happily agree with.
"""

import sqlite3
from typing import Any

import pytest
from expected import load
from seed import PLAYERS

from darts.stats.queries import StatsFilter
from darts.stats.report import PlayerStats, player_stats

GOLDEN = load()


def as_dict(stats: PlayerStats) -> dict[str, Any]:
    """The report in the golden file's shape, so the two can be compared whole."""
    return {
        "display_name": stats.display_name,
        "darts_thrown": stats.darts_thrown,
        "legs_played": stats.legs_played,
        "legs_won": stats.legs_won,
        "matches_played": stats.matches_played,
        "matches_won": stats.matches_won,
        "x01": {
            "darts_thrown": stats.x01.darts_thrown,
            "visits": stats.x01.visits,
            "points_scored": stats.x01.points_scored,
            "three_dart_average": stats.x01.three_dart_average,
            "first_nine_average": stats.x01.first_nine_average,
            "first_nine_darts": stats.x01.first_nine_darts,
            "highest_visit": stats.x01.highest_visit,
            "average_visit": stats.x01.average_visit,
            "bands": {
                "one_eighties": stats.x01.bands.one_eighties,
                "one_forty_plus": stats.x01.bands.one_forty_plus,
                "hundred_plus": stats.x01.bands.hundred_plus,
                "sixty_plus": stats.x01.bands.sixty_plus,
            },
            "checkout_attempts": stats.x01.checkout_attempts,
            "checkouts_hit": stats.x01.checkouts_hit,
            "checkout_percentage": stats.x01.checkout_percentage,
            "best_checkout": stats.x01.best_checkout,
        },
        "cricket": {
            "darts_thrown": stats.cricket.darts_thrown,
            "marks": stats.cricket.marks,
            "darts_on_target": stats.cricket.darts_on_target,
            "wasted_darts": stats.cricket.wasted_darts,
            "marks_per_round": stats.cricket.marks_per_round,
            "targets": [
                {
                    "target": target.target,
                    "hits": target.hits,
                    "marks": target.marks,
                    "hit_rate": target.hit_rate,
                }
                for target in stats.cricket.targets
            ],
        },
        "segments": [
            {"segment": s.segment, "multiplier": s.multiplier, "darts": s.darts}
            for s in stats.segments
        ],
    }


def report_for(conn: sqlite3.Connection, player_id: int) -> PlayerStats:
    return player_stats(conn, player_id, PLAYERS[player_id - 1], False, StatsFilter())


@pytest.mark.parametrize("player_id", range(1, len(PLAYERS) + 1))
def test_every_metric_matches_the_golden_file(seeded: sqlite3.Connection, player_id: int) -> None:
    """The whole report, every metric, against the independently derived values."""
    assert as_dict(report_for(seeded, player_id)) == GOLDEN[str(player_id)]


def test_the_golden_file_covers_every_seeded_player(seeded: sqlite3.Connection) -> None:
    """A player added to the seed without a golden value would otherwise be untested."""
    seeded_ids = {str(row[0]) for row in seeded.execute("SELECT id FROM players")}
    assert set(GOLDEN) == seeded_ids


# --- The same numbers, arrived at by hand ---------------------------------
#
# Each of these is worked out from the throw script in `seed.MATCHES` and the
# rules, not read off a query. They are the check on the check: the derivation
# in expected.py and the SQL could share a wrong definition, but neither of them
# wrote the arithmetic below.


def test_anas_best_checkout_is_the_visit_that_won_leg_1_of_match_1(
    seeded: sqlite3.Connection,
) -> None:
    """Match 1 leg 1 ends T20 + T15 + D8 = 60 + 45 + 16 = 121, and Ana wins it.

    Her other checkout, in leg 0, is T15 + D8 = 61, so 121 is the higher. Both
    readings of "best checkout" agree here and always: the winning visit clears
    the whole remaining, so its total and the remaining are one number.
    """
    x01 = report_for(seeded, 1).x01
    assert x01.best_checkout == 60 + 45 + 16 == 121
    assert x01.checkouts_hit == 2
    assert x01.checkout_attempts == 2
    assert x01.checkout_percentage == 100.0


def test_bens_checkout_percentage_is_zero_from_one_attempt(
    seeded: sqlite3.Connection,
) -> None:
    """An attempt that misses is 0%, which is a different answer from no attempt."""
    x01 = report_for(seeded, 2).x01
    assert (x01.checkout_attempts, x01.checkouts_hit) == (1, 0)
    assert x01.checkout_percentage == 0.0
    assert x01.best_checkout is None


def test_anas_cricket_mpr_is_eight(seeded: sqlite3.Connection) -> None:
    """Match 4, cut-throat: Ana throws visits 0, 3 and 6 and nothing else.

        T20 T20 T19  ->  3 + 3 + 3 = 9 marks
        T18 T17 T16  ->  3 + 3 + 3 = 9 marks
        T15 BULL 25  ->  3 + 2 + 1 = 6 marks
                                   -------
                          24 marks from 9 darts

    MPR counts a round as three darts, so 3 x 24 / 9 = 8.0.
    """
    cricket = report_for(seeded, 1).cricket
    assert (cricket.marks, cricket.darts_thrown) == (24, 9)
    assert cricket.marks_per_round == 8.0


def test_a_hit_rate_is_darts_on_that_target_over_every_cricket_dart(
    seeded: sqlite3.Connection,
) -> None:
    """Ana hits the 20 twice in nine cricket darts: 2/9, or 22.22%."""
    cricket = report_for(seeded, 1).cricket
    twenty = next(target for target in cricket.targets if target.target == 20)
    assert (twenty.hits, twenty.marks) == (2, 6)
    assert twenty.hit_rate == pytest.approx(100.0 * 2 / 9)
    assert [target.target for target in cricket.targets] == [20, 19, 18, 17, 16, 15, 25]


def test_a_target_nobody_went_near_is_a_zero_rather_than_a_gap(
    seeded: sqlite3.Connection,
) -> None:
    """Dee threw no cricket darts at all, so all seven targets read zero."""
    cricket = report_for(seeded, 4).cricket
    assert cricket.darts_thrown == 0
    assert cricket.marks_per_round is None
    assert [target.hits for target in cricket.targets] == [0] * 7
    assert [target.hit_rate for target in cricket.targets] == [None] * 7


def test_wins_are_credited_to_every_member_of_the_winning_team(
    seeded: sqlite3.Connection,
) -> None:
    """Ana wins both legs of match 1, leg 0 of the 2v2, and leg 0 of match 4.

    Cal is her partner in the 2v2 and threw the dart that finished leg 0, but
    the leg is the team's, so both of them are credited with exactly one.
    """
    ana, cal = report_for(seeded, 1), report_for(seeded, 3)
    assert (ana.legs_played, ana.legs_won) == (5, 4)
    assert (ana.matches_played, ana.matches_won) == (3, 1)
    assert cal.legs_won == 1
    assert cal.matches_won == 0


def test_an_unfinished_match_is_played_but_not_won(seeded: sqlite3.Connection) -> None:
    """Match 2's leg 1 is still in progress, so nobody has won that match."""
    for player_id in (1, 2, 3, 4):
        stats = report_for(seeded, player_id)
        assert stats.matches_played >= 1
        won = seeded.execute(
            "SELECT count(*) FROM matches WHERE id = 2 AND winner_team_id IS NOT NULL"
        ).fetchone()[0]
        assert won == 0
        assert stats.matches_won == (1 if player_id == 1 else 0)
