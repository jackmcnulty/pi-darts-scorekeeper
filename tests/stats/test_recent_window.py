"""`last_matches`: each player's own last N matches.

#27 needs a recent average beside a lifetime one, and this is the filter that
makes "recent" a count of matches rather than a date. The claims worth testing
are not arithmetic -- the metrics are already covered by the golden values -- but
the three decisions in the predicate:

* the window is **per player**, so one request describes each player over their
  own last N and not over whichever matches happened most recently;
* the window is **drawn from the same scope as the query**, so
  `game_type=x01&last_matches=1` is the last x01 match rather than the x01 part
  of the last match;
* a window **wider than the history** is the history, not an error and not a
  short report.

The #13 seed is small enough to name every expectation outright. Participation:
Ana in matches 1, 2 and 4; Ben in 1, 2 and 4; Cal in 2 and 4; Dee in 2 only; Eve
and Fin in 3 and 5. Match N was created on day N, so "most recent" is "highest
id" throughout.
"""

import sqlite3

import pytest
from seed import MATCHES, PLAYERS

from darts.stats.queries import StatsFilter, statement
from darts.stats.report import PlayerStats, leaderboard, player_stats

#: Who played in what, read off the fixture rather than restated, so that a
#: change to the seed cannot leave this file quietly describing the old one.
PARTICIPATION: dict[int, list[int]] = {
    player_id: sorted(
        match.id for match in MATCHES if any(player_id in team for team in match.members)
    )
    for player_id in range(1, len(PLAYERS) + 1)
}


def report(conn: sqlite3.Connection, player_id: int, **kwargs: object) -> PlayerStats:
    # The name is supplied rather than looked up, because one test deliberately
    # asks about a player id past the end of the fixture.
    name = PLAYERS[player_id - 1] if player_id <= len(PLAYERS) else f"Player {player_id}"
    return player_stats(
        conn,
        player_id,
        name,
        False,
        StatsFilter(**kwargs),  # type: ignore[arg-type]
    )


def test_the_fixture_is_the_one_this_module_describes() -> None:
    """Every expectation below is read from this; if it changed, say so here."""
    assert PARTICIPATION == {1: [1, 2, 4], 2: [1, 2, 4], 3: [2, 4], 4: [2], 5: [3, 5], 6: [3, 5]}


@pytest.mark.parametrize("player_id", sorted(PARTICIPATION))
def test_a_window_of_one_is_that_players_own_last_match(
    seeded: sqlite3.Connection, player_id: int
) -> None:
    """The per-player claim, and the whole reason this is a pair set.

    Ana's last match is 4 and Eve's is 5. A window over "the last match" globally
    would describe match 5 for both, crediting Eve's darts to nobody and leaving
    Ana with an empty report. Each player instead gets their own, which is what
    makes the same number comparable down a leaderboard column.
    """
    last = PARTICIPATION[player_id][-1]
    windowed = report(seeded, player_id, last_matches=1)
    just_that_match = report(seeded, player_id, match_id=last)
    assert windowed == just_that_match
    assert windowed.matches_played == 1
    assert windowed.darts_thrown == just_that_match.darts_thrown


def test_players_last_matches_really_do_differ(seeded: sqlite3.Connection) -> None:
    """Guards the test above against passing vacuously.

    If every player's last match were the same one, `last_matches=1` would agree
    with a global window and the per-player assertion would prove nothing.
    """
    assert len({matches[-1] for matches in PARTICIPATION.values()}) > 1


@pytest.mark.parametrize("player_id", sorted(PARTICIPATION))
def test_a_window_wider_than_the_history_is_the_whole_history(
    seeded: sqlite3.Connection, player_id: int
) -> None:
    """Asking for ten when you have played three is a report about three."""
    played = len(PARTICIPATION[player_id])
    lifetime = report(seeded, player_id)
    for width in (played, played + 1, 99):
        assert report(seeded, player_id, last_matches=width) == lifetime, width
    assert lifetime.matches_played == played


@pytest.mark.parametrize("player_id", sorted(PARTICIPATION))
def test_matches_played_is_what_the_window_actually_covered(
    seeded: sqlite3.Connection, player_id: int
) -> None:
    """The number a screen can truthfully put in front of a reader.

    The echo says what was *asked for*; this says what was found. #27's card
    labels its recent column from this, so "last 3 matches" for a player who has
    played three is honest where "last 10" would not be.
    """
    played = len(PARTICIPATION[player_id])
    for width in range(1, played + 3):
        assert report(seeded, player_id, last_matches=width).matches_played == min(width, played)


def test_the_window_is_narrowed_by_the_same_filter_as_the_query(
    seeded: sqlite3.Connection,
) -> None:
    """`{scope}` in the predicate: the last x01 match, not the x01 part of the last.

    Ana's matches are 1 (x01), 2 (x01) and 4 (cricket), so her most recent match
    is cricket and her most recent *x01* match is 2. A window that ignored
    `game_type` would pick match 4, find no x01 darts in it and report a player
    with no x01 history at all -- while still calling itself one match.
    """
    assert PARTICIPATION[1] == [1, 2, 4]
    windowed = report(seeded, 1, game_type="x01", last_matches=1)
    assert windowed == report(seeded, 1, game_type="x01", match_id=2)
    assert windowed.x01.darts_thrown > 0
    assert windowed.matches_played == 1

    # And the unscoped window really would have chosen the cricket match, which
    # is what makes the assertion above a distinction rather than a coincidence.
    assert report(seeded, 1, last_matches=1) == report(seeded, 1, match_id=4)


def test_since_and_last_matches_compose_rather_than_override(
    seeded: sqlite3.Connection,
) -> None:
    """A date and a count are independent narrowings, and both apply.

    Ana's matches 2 and 4 are on or after day 2, so `since` leaves two and the
    window then takes the later one. Neither alone gives that answer.
    """
    boundary = str(seeded.execute("SELECT created_at FROM matches WHERE id = 2").fetchone()[0])
    both = report(seeded, 1, since=boundary, last_matches=1)
    assert both == report(seeded, 1, match_id=4)
    assert report(seeded, 1, since=boundary).matches_played == 2
    assert report(seeded, 1, last_matches=1).matches_played == 1


def test_a_window_on_a_player_who_has_never_played_is_empty_not_an_error(
    seeded: sqlite3.Connection,
) -> None:
    """There is no such thing as their last match, and that is answerable."""
    # Player ids run to len(PLAYERS); one past the end has no participation at
    # all, which is the same shape as #22's freshly created player.
    nobody = report(seeded, len(PLAYERS) + 1, last_matches=10)
    assert nobody.matches_played == 0
    assert nobody.darts_thrown == 0
    assert nobody.x01.three_dart_average is None
    assert nobody.cricket.marks_per_round is None
    assert nobody.segments == ()


def test_the_leaderboard_windows_each_player_separately(seeded: sqlite3.Connection) -> None:
    """The form table: every row is that player over their own last match.

    `min_darts=0` because the seed's legs are short and this is about who is on
    the table and what their row says, not about the threshold.
    """
    form = leaderboard(seeded, StatsFilter(last_matches=1), 0)
    assert form.rows, "a form table over a seed with x01 darts should not be empty"
    for row in form.rows:
        own = report(seeded, row.player_id, last_matches=1)
        assert row.darts_thrown == own.x01.darts_thrown, row.display_name
        assert row.three_dart_average == own.x01.three_dart_average, row.display_name
        assert row.best_checkout == own.x01.best_checkout, row.display_name


def test_a_form_table_is_not_the_lifetime_table(seeded: sqlite3.Connection) -> None:
    """Guards the test above from passing because the window changed nothing.

    Scoped to x01 so that both tables rank the same four players and the only
    difference is the window. Every seeded player's last *x01* match is match 2,
    so Ana and Ben lose match 1's darts while Cal and Dee -- who never played
    match 1 -- are unchanged. That mixture is the point: a window narrows the
    people it applies to and leaves the rest alone.
    """
    scoped = StatsFilter(game_type="x01")
    lifetime = {row.player_id: row.darts_thrown for row in leaderboard(seeded, scoped, 0).rows}
    form = {
        row.player_id: row.darts_thrown
        for row in leaderboard(seeded, StatsFilter(game_type="x01", last_matches=1), 0).rows
    }
    assert set(form) == set(lifetime), "the window should not drop an x01 player entirely"
    # Nobody gains darts by being windowed, and somebody loses some.
    assert all(form[pid] <= lifetime[pid] for pid in form)
    assert any(form[pid] < lifetime[pid] for pid in form)


def test_a_window_can_drop_a_player_off_the_leaderboard_altogether(
    seeded: sqlite3.Connection,
) -> None:
    """A real consequence of the window, and one #27's empty states must expect.

    Unscoped, every seeded player's most recent match is cricket except Dee's, so
    a form table over one match has almost nobody on it -- the leaderboard ranks
    by an x01 average and most of these players threw no x01 darts in their last
    game. That is the honest answer rather than a bug: they have no recent x01
    form.
    """
    lifetime = {row.player_id for row in leaderboard(seeded, StatsFilter(), 0).rows}
    form = {row.player_id for row in leaderboard(seeded, StatsFilter(last_matches=1), 0).rows}
    assert form < lifetime
    # Dee played only match 2, an x01 match, so she is the one who survives.
    assert form == {4}


def test_the_predicate_is_a_pair_set_over_a_view_and_binds_its_width() -> None:
    """The mechanism, asserted on the SQL text rather than inferred from results.

    A flat `match_id IN (...)` would be the easy spelling and would credit one
    player with another's window; `tests/db/test_view_bypass.py` reads `sql/` and
    cannot see this predicate, so the view is asserted here too.
    """
    text = statement("x01_totals", StatsFilter(last_matches=10).params(player_id=1))
    assert "(d.player_id, d.match_id) IN (" in text
    assert "ROW_NUMBER() OVER (PARTITION BY w.player_id" in text
    assert "FROM v_match_players w" in text
    assert "ranked.rn <= :last_matches" in text
    # The width travels as a binding, and no predicate is ever an OR against one.
    assert ":last_matches" in text
    assert "IS NULL OR" not in text
    # No base table: the window reads the view, like every query in this layer.
    for table in (" matches ", " team_members ", " teams "):
        assert table not in text


def test_the_windows_own_scope_carries_every_other_bound_filter() -> None:
    """What makes "my last ten cricket matches" mean that, and not recurse."""
    params = StatsFilter(
        game_type="cricket", variant="quick", since="2026-01-02T00:00:00.000Z", last_matches=5
    ).params(player_id=2)
    text = statement("x01_totals", params)
    for inner in (
        "AND w.game_type = :game_type",
        "AND w.variant = :variant",
        "AND w.match_created_at >= :since",
        "AND w.player_id = :player_id",
    ):
        assert inner in text, inner
    # The window is not inside itself. Counted on the window's own partition
    # rather than on `ROW_NUMBER()`, because x01.sql uses one of its own to
    # number a player's darts within a leg.
    assert text.count("PARTITION BY w.player_id") == 1


def test_an_unset_window_contributes_no_predicate() -> None:
    """Lifetime is the absence of the filter, not a very wide one."""
    text = statement("x01_totals", StatsFilter().params(player_id=1))
    assert "last_matches" not in text
    assert "ROW_NUMBER() OVER (PARTITION BY w.player_id" not in text
