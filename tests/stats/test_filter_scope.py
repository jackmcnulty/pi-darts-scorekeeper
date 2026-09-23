"""The four filters, alone and composed.

A filter is only useful if narrowing by two things narrows by both, so the
composition tests check against the subset the filter describes rather than
against a remembered number.
"""

import sqlite3

import pytest
from seed import MATCHES, PLAYERS

from darts.stats.queries import StatsFilter, stamp, statement
from darts.stats.report import player_stats

#: Match 3 is the first cricket match in the seed and was created on day 3.
CRICKET_MATCH = 3


def stats(conn: sqlite3.Connection, player_id: int, **kwargs: object) -> object:
    return player_stats(
        conn,
        player_id,
        PLAYERS[player_id - 1],
        False,
        StatsFilter(**kwargs),  # type: ignore[arg-type]
    )


def darts_matching(conn: sqlite3.Connection, player_id: int, where: str) -> int:
    return int(
        conn.execute(
            f"SELECT count(*) FROM v_darts WHERE player_id = ? AND {where}", (player_id,)
        ).fetchone()[0]
    )


def test_game_type_narrows_to_one_family(seeded: sqlite3.Connection) -> None:
    ana = stats(seeded, 1, game_type="x01")
    assert ana.darts_thrown == darts_matching(seeded, 1, "game_type = 'x01'")  # type: ignore[attr-defined]
    assert ana.cricket.darts_thrown == 0  # type: ignore[attr-defined]
    assert ana.x01.darts_thrown > 0  # type: ignore[attr-defined]

    cricket = stats(seeded, 1, game_type="cricket")
    assert cricket.x01.darts_thrown == 0  # type: ignore[attr-defined]
    assert cricket.x01.three_dart_average is None  # type: ignore[attr-defined]
    assert cricket.cricket.darts_thrown > 0  # type: ignore[attr-defined]


def test_x01_metrics_are_x01_only_even_unfiltered(seeded: sqlite3.Connection) -> None:
    """The decision that stops a lifetime average being a number about nothing.

    Ana has thrown both x01 and cricket darts. Her 3-dart average is the same
    whether or not `game_type=x01` was asked for, because the x01 block never
    had any cricket darts in it; only `darts_thrown`, which is a raw count, sees
    both.
    """
    unfiltered, narrowed = stats(seeded, 1), stats(seeded, 1, game_type="x01")
    assert unfiltered.x01 == narrowed.x01  # type: ignore[attr-defined]
    assert unfiltered.darts_thrown > narrowed.darts_thrown  # type: ignore[attr-defined]
    assert unfiltered.cricket.darts_thrown > 0  # type: ignore[attr-defined]
    assert narrowed.cricket.darts_thrown == 0  # type: ignore[attr-defined]


def test_variant_narrows_within_cricket(seeded: sqlite3.Connection) -> None:
    """Ana plays only cut-throat, so `standard` leaves her with nothing."""
    cutthroat = stats(seeded, 1, variant="cutthroat")
    assert cutthroat.cricket.darts_thrown == darts_matching(seeded, 1, "variant = 'cutthroat'")  # type: ignore[attr-defined]
    assert stats(seeded, 1, variant="standard").cricket.darts_thrown == 0  # type: ignore[attr-defined]


def test_variant_excludes_x01_because_x01_has_no_variant(seeded: sqlite3.Connection) -> None:
    """`variant` is NULL for every x01 match, so naming one is naming cricket."""
    assert stats(seeded, 1, variant="cutthroat").x01.darts_thrown == 0  # type: ignore[attr-defined]


def test_match_id_narrows_to_one_match(seeded: sqlite3.Connection) -> None:
    for match in MATCHES:
        for player_id in {p for members in match.members for p in members}:
            scoped = stats(seeded, player_id, match_id=match.id)
            assert scoped.darts_thrown == darts_matching(  # type: ignore[attr-defined]
                seeded, player_id, f"match_id = {match.id}"
            )
            assert scoped.matches_played == 1  # type: ignore[attr-defined]


def test_since_cuts_on_the_matchs_created_at_not_the_darts(
    seeded: sqlite3.Connection,
) -> None:
    """A match is never split across the boundary.

    Every seeded match is created on its own day and its darts are thrown ten
    minutes later, so a `since` between two matches includes all of the later
    one and none of the earlier -- which is the property that makes a per-match
    statistic filtered by time still describe whole matches.
    """
    created = {
        int(row["id"]): str(row["created_at"])
        for row in seeded.execute("SELECT id, created_at FROM matches ORDER BY id")
    }
    boundary = created[CRICKET_MATCH]
    later = {match_id for match_id, at in created.items() if at >= boundary}
    assert 1 not in later and CRICKET_MATCH in later

    for player_id in range(1, len(PLAYERS) + 1):
        scoped = stats(seeded, player_id, since=boundary)
        expected = darts_matching(seeded, player_id, f"match_created_at >= '{boundary}'")
        assert scoped.darts_thrown == expected  # type: ignore[attr-defined]
        # Whole matches: every dart of an included match is in, none of an
        # excluded one is.
        for match_id, at in created.items():
            in_scope = darts_matching(
                seeded, player_id, f"match_id = {match_id} AND match_created_at >= '{boundary}'"
            )
            whole = darts_matching(seeded, player_id, f"match_id = {match_id}")
            assert in_scope == (whole if at >= boundary else 0)


def test_filters_compose(seeded: sqlite3.Connection) -> None:
    """`?game_type=x01&since=...` narrows by both, not by whichever came last."""
    boundary = str(seeded.execute("SELECT created_at FROM matches WHERE id = 2").fetchone()[0])
    for player_id in range(1, len(PLAYERS) + 1):
        both = stats(seeded, player_id, game_type="x01", since=boundary)
        assert both.darts_thrown == darts_matching(  # type: ignore[attr-defined]
            seeded, player_id, f"game_type = 'x01' AND match_created_at >= '{boundary}'"
        )
        # Strictly narrower than either alone, for at least one player.
    assert stats(seeded, 1, game_type="x01", since=boundary).darts_thrown < min(  # type: ignore[attr-defined]
        stats(seeded, 1, game_type="x01").darts_thrown,  # type: ignore[attr-defined]
        stats(seeded, 1, since=boundary).darts_thrown,  # type: ignore[attr-defined]
    )


def test_all_four_filters_at_once(seeded: sqlite3.Connection) -> None:
    """Every predicate present, and the answer still the subset they describe."""
    created = str(
        seeded.execute("SELECT created_at FROM matches WHERE id = ?", (CRICKET_MATCH,)).fetchone()[
            0
        ]
    )
    scoped = stats(
        seeded, 5, game_type="cricket", variant="standard", since=created, match_id=CRICKET_MATCH
    )
    assert scoped.darts_thrown == darts_matching(  # type: ignore[attr-defined]
        seeded,
        5,
        "game_type = 'cricket' AND variant = 'standard' "
        f"AND match_created_at >= '{created}' AND match_id = {CRICKET_MATCH}",
    )
    assert scoped.darts_thrown > 0  # type: ignore[attr-defined]


def test_a_filter_that_matches_nothing_is_an_empty_report_not_an_error(
    seeded: sqlite3.Connection,
) -> None:
    """Contradictory filters are legal and answerable: there are no such darts."""
    nothing = stats(seeded, 1, game_type="x01", variant="standard")
    assert nothing.darts_thrown == 0  # type: ignore[attr-defined]
    assert nothing.x01.three_dart_average is None  # type: ignore[attr-defined]
    assert nothing.cricket.marks_per_round is None  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("kwargs", "present", "absent"),
    [
        (
            {},
            (),
            (
                "d.game_type = :game_type",
                "d.variant = :variant",
                "d.match_created_at >= :since",
                "d.match_id = :match_id",
                "d.player_id = :player_id",
            ),
        ),
        (
            {"game_type": "x01"},
            ("AND d.game_type = :game_type",),
            ("d.variant = :variant", "d.match_id = :match_id"),
        ),
        ({"since": "2026-01-01T00:00:00.000Z"}, ("AND d.match_created_at >= :since",), ()),
        ({"match_id": 2}, ("AND d.match_id = :match_id",), ("d.game_type = :game_type",)),
    ],
)
def test_only_the_bound_predicates_reach_the_statement(
    kwargs: dict[str, object], present: tuple[str, ...], absent: tuple[str, ...]
) -> None:
    """A parameter that is not set contributes no predicate at all.

    This is what buys the index seek: `(:x IS NULL OR col = :x)` is never an
    index seek, because SQLite prepares the statement without knowing what will
    be bound to it. `test_query_plans.py` asserts the consequence; this asserts
    the mechanism.
    """
    text = statement("darts_thrown", StatsFilter(**kwargs).params())  # type: ignore[arg-type]
    assert "IS NULL OR" not in text
    for fragment in present:
        assert fragment in text
    for fragment in absent:
        assert fragment not in text


def test_a_datetime_becomes_the_text_the_rows_are_stored_in() -> None:
    """`since` is compared as text, so it has to be in the schema's own format."""
    from datetime import UTC, datetime, timedelta, timezone

    assert stamp(datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)) == "2026-01-02T03:04:05.678Z"
    # A naive datetime is taken as UTC; an aware one is converted to it.
    assert stamp(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05.000Z"
    east = timezone(timedelta(hours=2))
    assert stamp(datetime(2026, 1, 2, 5, 4, 5, tzinfo=east)) == "2026-01-02T03:04:05.000Z"
    # And the format's lexicographic order is its chronological order, which is
    # what makes `>=` on the text a correct comparison.
    assert stamp(datetime(2026, 1, 2, tzinfo=UTC)) < stamp(datetime(2026, 1, 10, tzinfo=UTC))
