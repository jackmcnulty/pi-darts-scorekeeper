"""Which darts count, and for what.

The rule the ticket states outright: a busted visit contributes its real dart
count and zero points. The rest follow from it -- an uncounted double-in dart
was still thrown, an unfinished leg's darts were still thrown, and an undone
dart was not thrown at all because #15 deleted the row.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from seed import PLAYERS

from darts.stats.queries import StatsFilter, run
from darts.stats.report import PlayerStats, X01Stats, player_stats


@contextmanager
def rolled_back(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Change the database, look at the result, and leave it exactly as it was.

    The seeded connection is shared by every test in the session, and it is in
    autocommit mode -- so a bare UPDATE would be permanent and the next test
    would be reading a database this one edited. An explicit transaction that
    always rolls back is what makes these mutations safe to run against it.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    finally:
        conn.rollback()


def stats_for(conn: sqlite3.Connection, player_id: int, **kwargs: object) -> PlayerStats:
    return player_stats(
        conn,
        player_id,
        PLAYERS[player_id - 1] if player_id <= len(PLAYERS) else "Newcomer",
        False,
        StatsFilter(**kwargs),  # type: ignore[arg-type]
    )


def x01_of(conn: sqlite3.Connection, player_id: int) -> X01Stats:
    return stats_for(conn, player_id).x01


def test_a_busted_visit_costs_its_darts_and_scores_nothing(
    seeded: sqlite3.Connection,
) -> None:
    """Ben busts on the third dart of match 1, leg 0, visit 3 ("T20", "T20", "20").

    He is on 124 when the visit starts: T20 leaves 64, T20 leaves 4, and the 20
    cannot be taken. All three darts are uncounted by the bust rule, so the visit
    scores 0 -- but 140 was put on the board and three darts were thrown, and all
    three belong in the denominator of his 3-dart average. Dropping them would
    flatter him; counting the 140 would be scoring a visit the rules rubbed out.
    """
    bust = seeded.execute(
        "SELECT visit_id, player_id, darts_thrown, total_scored FROM v_visits WHERE is_bust = 1"
    ).fetchall()
    assert len(bust) == 1, "the seed is expected to contain exactly one bust"
    visit = bust[0]
    assert (visit["darts_thrown"], visit["total_scored"]) == (3, 0)
    assert (
        seeded.execute(
            "SELECT sum(score) FROM v_darts WHERE visit_id = ?", (visit["visit_id"],)
        ).fetchone()[0]
        == 140
    ), "the darts hit 140 between them; the bust is what made the visit worth nothing"

    ben = x01_of(seeded, int(visit["player_id"]))
    totals = seeded.execute(
        """SELECT count(*) AS darts, coalesce(sum(counted * score), 0) AS points
           FROM v_darts WHERE player_id = ? AND game_type = 'x01'""",
        (visit["player_id"],),
    ).fetchone()
    assert ben.darts_thrown == totals["darts"]
    assert ben.points_scored == totals["points"]
    assert ben.three_dart_average == 3.0 * totals["points"] / totals["darts"]


def test_the_busted_visit_is_not_in_any_scoring_band(seeded: sqlite3.Connection) -> None:
    """It scored 0, so it is not a 60+ visit even though 60 was on the board."""
    scores = [
        int(row["total_scored"])
        for row in seeded.execute(
            "SELECT total_scored FROM v_visits WHERE player_id = 2 AND game_type = 'x01'"
        )
    ]
    assert 0 in scores
    assert x01_of(seeded, 2).bands.sixty_plus == sum(1 for s in scores if s >= 60)


def test_uncounted_opening_darts_are_darts_thrown_scoring_nothing(
    seeded: sqlite3.Connection,
) -> None:
    """Match 2 is double-in: the darts before the double are thrown but score 0.

    They are real darts at a real board and they belong in the denominator, the
    same as a bust's. The seed exists partly to make this case unavoidable.
    """
    uncounted = seeded.execute(
        """SELECT player_id, count(*) AS darts, sum(score) AS board
           FROM v_darts WHERE match_id = 2 AND counted = 0 GROUP BY player_id"""
    ).fetchall()
    assert uncounted, "match 2 is expected to contain uncounted opening darts"
    for row in uncounted:
        assert row["board"] > 0, "these darts hit the board; they just did not count"
        every_dart = seeded.execute(
            "SELECT count(*) FROM v_darts WHERE match_id = 2 AND player_id = ?",
            (row["player_id"],),
        ).fetchone()[0]
        scoped = stats_for(seeded, int(row["player_id"]), match_id=2)
        assert scoped.x01.darts_thrown == every_dart


def test_darts_in_an_unfinished_leg_still_count(seeded: sqlite3.Connection) -> None:
    """Match 2's leg 1 is in progress. Its darts were thrown, so they are counted."""
    unfinished = seeded.execute(
        "SELECT id FROM legs WHERE match_id = 2 AND winner_team_id IS NULL"
    ).fetchall()
    assert len(unfinished) == 1
    leg_id = int(unfinished[0]["id"])
    in_leg = seeded.execute(
        "SELECT player_id, count(*) AS darts FROM v_darts WHERE leg_id = ? GROUP BY player_id",
        (leg_id,),
    ).fetchall()
    assert in_leg
    for row in in_leg:
        elsewhere = seeded.execute(
            "SELECT count(*) FROM v_darts WHERE player_id = ? AND leg_id != ?",
            (row["player_id"], leg_id),
        ).fetchone()[0]
        assert stats_for(seeded, int(row["player_id"])).darts_thrown == elsewhere + int(
            row["darts"]
        )


def test_undone_darts_are_absent_entirely(seeded: sqlite3.Connection) -> None:
    """#15 hard-deletes an undone dart, so there is nothing for #19 to exclude.

    Deleting the row is what makes that true, and this test is what would notice
    if undo ever became a soft delete: the statistics have no flag to filter on
    and would silently start counting darts nobody threw.
    """
    before = stats_for(seeded, 1)
    with rolled_back(seeded) as conn:
        victim = conn.execute(
            "SELECT id, segment, multiplier FROM darts WHERE player_id = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.execute("DELETE FROM darts WHERE id = ?", (victim["id"],))
        after = stats_for(conn, 1)

        assert after.darts_thrown == before.darts_thrown - 1
        assert sum(s.darts for s in after.segments) == before.darts_thrown - 1
        hit = (int(victim["segment"]), int(victim["multiplier"]))
        was = next(s.darts for s in before.segments if (s.segment, s.multiplier) == hit)
        now = next(
            (s.darts for s in after.segments if (s.segment, s.multiplier) == hit),
            0,
        )
        assert now == was - 1

    assert stats_for(seeded, 1) == before, "the rollback must leave the fixture untouched"


def test_a_player_with_no_darts_gets_zeroes_and_nulls_not_an_error(
    seeded: sqlite3.Connection,
) -> None:
    """A count is 0, an average is None. Neither is an error, and neither is a 0 average."""
    with rolled_back(seeded) as conn:
        conn.execute("INSERT INTO players(id, display_name) VALUES (99, 'Newcomer')")
        stats = stats_for(conn, 99)

    assert (stats.darts_thrown, stats.legs_played, stats.legs_won) == (0, 0, 0)
    assert (stats.matches_played, stats.matches_won) == (0, 0)
    assert stats.segments == ()
    assert stats.x01.darts_thrown == 0
    assert stats.x01.visits == 0
    assert stats.x01.points_scored == 0
    assert stats.x01.three_dart_average is None
    assert stats.x01.first_nine_average is None
    assert stats.x01.first_nine_darts == 0
    assert stats.x01.highest_visit is None
    assert stats.x01.average_visit is None
    assert stats.x01.best_checkout is None
    assert stats.x01.checkout_percentage is None
    assert stats.x01.checkout_attempts == 0
    assert stats.x01.bands.one_eighties == 0
    assert stats.cricket.darts_thrown == 0
    assert stats.cricket.marks == 0
    assert stats.cricket.marks_per_round is None
    assert [target.target for target in stats.cricket.targets] == [20, 19, 18, 17, 16, 15, 25]
    assert [target.hit_rate for target in stats.cricket.targets] == [None] * 7


def test_an_abandoned_matchs_darts_count_but_its_legs_are_won_by_nobody(
    seeded: sqlite3.Connection,
) -> None:
    """#17 added abandonment after #19 was written.

    The darts were really thrown, so they stay in every scoring metric. An
    abandoned match has no winner -- 0002 makes `abandoned_at` and
    `winner_team_id` mutually exclusive -- so the win counts need no clause to
    exclude it, and this proves they behave that way rather than by luck.
    """
    before = stats_for(seeded, 1)
    assert before.matches_won == 1

    with rolled_back(seeded) as conn:
        conn.execute(
            """UPDATE matches SET abandoned_at = '2026-01-05T00:00:00.000Z'
               WHERE id = 2 AND completed_at IS NULL"""
        )
        assert conn.execute("SELECT count(*) FROM matches WHERE abandoned_at IS NOT NULL")
        after = stats_for(conn, 1)

    assert after.darts_thrown == before.darts_thrown
    assert after.x01 == before.x01
    assert after.matches_played == before.matches_played
    assert after.matches_won == before.matches_won == 1
    assert after.legs_won == before.legs_won


def test_every_query_agrees_on_how_many_darts_a_player_threw(
    seeded: sqlite3.Connection,
) -> None:
    """The families are separate statements; they must still describe one board."""
    for player_id in range(1, len(PLAYERS) + 1):
        params = StatsFilter().params(player_id=player_id)
        total = run(seeded, "darts_thrown", params)
        x01 = run(seeded, "x01_totals", params)
        cricket = run(seeded, "cricket_totals", params)
        by_segment = sum(int(row["darts"]) for row in run(seeded, "segment_frequency", params))

        assert (int(total[0]["darts_thrown"]) if total else 0) == by_segment
        assert by_segment == (int(x01[0]["darts_thrown"]) if x01 else 0) + (
            int(cricket[0]["darts_thrown"]) if cricket else 0
        )
