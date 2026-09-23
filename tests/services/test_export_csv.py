"""The two published CSV formats, over the seeded fixture.

The headers are a contract. Nothing here reads a column by index into a list it
also built; the assertions name columns the way a spreadsheet does, so a
reordering that a laxer test would wave through fails here.
"""

import csv
import io
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from seed import build

from darts.db.connection import transaction
from darts.engine.throws import BULL, DOUBLE, Throw
from darts.repo.matches import abandon_match
from darts.services import export
from darts.stats.queries import StatsFilter


@pytest.fixture
def seeded(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = build(tmp_path / "seeded.db")
    try:
        yield conn
    finally:
        conn.close()


def parse(lines: Iterator[str]) -> list[dict[str, str]]:
    """Read the export back the way a consumer would, through `csv` itself."""
    return list(csv.DictReader(io.StringIO("".join(lines))))


def header_of(lines: Iterator[str]) -> list[str]:
    return next(csv.reader(io.StringIO(next(lines))))


def darts(conn: sqlite3.Connection, **narrow: Any) -> list[dict[str, str]]:
    return parse(export.darts_csv(conn, StatsFilter(**narrow)))


def matches(conn: sqlite3.Connection, **narrow: Any) -> list[dict[str, str]]:
    return parse(export.matches_csv(conn, StatsFilter(**narrow)))


# --- the published headers -------------------------------------------------


def test_the_darts_header_is_exactly_the_documented_one(seeded: sqlite3.Connection) -> None:
    """Names, order and count. Changing any of them breaks somebody's column 18."""
    assert header_of(export.darts_csv(seeded, StatsFilter())) == [
        "match_id",
        "match_created_at",
        "game_type",
        "variant",
        "leg_id",
        "leg_index",
        "team_id",
        "team_index",
        "team_name",
        "player_id",
        "player_name",
        "visit_id",
        "visit_index",
        "team_visit_index",
        "seq_in_leg",
        "dart_index",
        "dart_id",
        "label",
        "segment",
        "multiplier",
        "score",
        "counted",
        "caused_bust",
        "was_checkout_attempt",
        "thrown_at",
        "visit_score_before",
        "visit_score_after",
        "visit_is_bust",
        "cricket_target",
        "cricket_counted_marks",
        "cricket_surplus_marks",
        "cricket_wasted",
    ]


def test_the_matches_header_is_exactly_the_documented_one(seeded: sqlite3.Connection) -> None:
    assert header_of(export.matches_csv(seeded, StatsFilter())) == [
        "match_id",
        "status",
        "created_at",
        "completed_at",
        "abandoned_at",
        "game_type",
        "variant",
        "start_score",
        "in_rule",
        "out_rule",
        "best_of",
        "legs_played",
        "legs_completed",
        "darts_thrown",
        "winner_team_id",
        "winner_team_name",
        "teams",
        "players",
    ]


def test_the_headers_are_what_the_module_publishes(seeded: sqlite3.Connection) -> None:
    """The constants and the files agree, so documentation can quote the constants."""
    assert header_of(export.darts_csv(seeded, StatsFilter())) == list(export.DARTS_HEADER)
    assert header_of(export.matches_csv(seeded, StatsFilter())) == list(export.MATCHES_HEADER)


def test_every_documented_column_is_written_for_every_row(seeded: sqlite3.Connection) -> None:
    """`csv.DictReader` would leave a short row's tail as None; none are short."""
    for row in darts(seeded):
        assert set(row) == set(export.DARTS_HEADER)
        assert None not in row.values()
    for row in matches(seeded):
        assert set(row) == set(export.MATCHES_HEADER)
        assert None not in row.values()


# --- one row per dart ------------------------------------------------------


def test_there_is_exactly_one_row_per_recorded_dart(seeded: sqlite3.Connection) -> None:
    """Against the base table, deliberately: the view must not multiply rows."""
    stored = seeded.execute("SELECT count(*) FROM darts").fetchone()[0]
    rows = darts(seeded)
    assert stored > 0
    assert len(rows) == stored
    assert len({row["dart_id"] for row in rows}) == stored


def test_a_cricket_dart_does_not_multiply_into_one_row_per_recipient(
    seeded: sqlite3.Connection,
) -> None:
    """`cricket_point_events` is one row per recipient and is deliberately absent."""
    events = seeded.execute("SELECT count(*) FROM cricket_point_events").fetchone()[0]
    assert events > 0, "the seed must contain cutthroat points for this to mean anything"
    cricket = seeded.execute(
        "SELECT count(*) FROM darts d JOIN legs l ON l.id = d.leg_id "
        "JOIN matches m ON m.id = l.match_id WHERE m.game_type = 'cricket'"
    ).fetchone()[0]
    assert len(darts(seeded, game_type="cricket")) == cricket


def test_busted_and_uncounted_darts_are_exported(seeded: sqlite3.Connection) -> None:
    """They were thrown. `counted` and `caused_bust` say what became of them."""
    rows = darts(seeded)
    assert any(row["caused_bust"] == "1" for row in rows)
    assert any(row["counted"] == "0" for row in rows)


def test_rows_are_in_match_leg_and_throw_order(seeded: sqlite3.Connection) -> None:
    """Part of the format: two exports of the same data must diff as identical."""
    keys = [
        (int(row["match_id"]), int(row["leg_index"]), int(row["seq_in_leg"]))
        for row in darts(seeded)
    ]
    assert keys == sorted(keys)


# --- Jack's requirement on #20: the inner bull must survive -----------------


def test_the_inner_bull_is_distinguishable_from_the_doubles_ring(
    seeded: sqlite3.Connection,
) -> None:
    """#20's comment, asserted against this serialisation rather than upstream.

    `Throw.is_double` is True for both, so a row carrying only that flag would
    have flattened the two together unrecoverably. Every one of the four columns
    below keeps them apart, and the export carries all four.
    """
    rows = darts(seeded)
    bulls = [row for row in rows if row["label"] == "BULL"]
    doubles = [row for row in rows if row["label"].startswith("D")]
    assert bulls, "the seed contains a bull finish"
    assert doubles, "and darts in the doubles ring"

    for row in bulls:
        assert row["segment"] == str(BULL)
        assert row["multiplier"] == str(DOUBLE)
        assert row["score"] == "50"
    for row in doubles:
        assert row["segment"] != str(BULL)
        assert row["score"] != "50"

    # No pair of them agrees on any of the three, so any single column recovers it.
    for column in ("label", "segment", "score"):
        assert {row[column] for row in bulls}.isdisjoint({row[column] for row in doubles})


def test_every_exported_label_parses_back_to_the_throw_it_came_from(
    seeded: sqlite3.Connection,
) -> None:
    """A full round trip through `Throw.parse`, the documented inverse of `label`."""
    seen = set()
    for row in darts(seeded):
        throw = Throw(int(row["segment"]), int(row["multiplier"]))
        assert Throw.parse(row["label"]) == throw
        assert row["score"] == str(throw.score)
        seen.add(throw)
    assert Throw(BULL, DOUBLE) in seen


def test_the_outer_bull_is_not_the_inner_one(seeded: sqlite3.Connection) -> None:
    """`25` and `BULL` share a segment and differ everywhere else that matters."""
    assert Throw.parse("25") == Throw(BULL, 1)
    assert Throw.parse("BULL") == Throw(BULL, DOUBLE)
    assert Throw(BULL, 1).label != Throw(BULL, DOUBLE).label
    assert Throw(BULL, 1).score != Throw(BULL, DOUBLE).score


# --- empty cells, not invented values --------------------------------------


def test_an_absent_value_is_an_empty_cell(seeded: sqlite3.Connection) -> None:
    """NULL is written as nothing at all, never as `0`, `None` or `null`."""
    x01 = [row for row in darts(seeded, game_type="x01")]
    assert x01
    for row in x01:
        assert row["variant"] == ""
        assert row["cricket_target"] == ""
        assert row["cricket_counted_marks"] == ""
    assert "None" not in "".join(export.darts_csv(seeded, StatsFilter()))


def test_a_cricket_dart_on_a_target_carries_its_marks(seeded: sqlite3.Connection) -> None:
    on_target = [row for row in darts(seeded, game_type="cricket") if row["cricket_target"]]
    assert on_target
    for row in on_target:
        assert row["cricket_counted_marks"].isdigit()
        assert row["cricket_surplus_marks"].isdigit()


def test_booleans_are_written_as_the_zero_or_one_the_schema_stores(
    seeded: sqlite3.Connection,
) -> None:
    for row in darts(seeded):
        for column in ("counted", "caused_bust", "was_checkout_attempt", "visit_is_bust"):
            assert row[column] in {"0", "1"}, column


# --- matches.csv -----------------------------------------------------------


def test_there_is_exactly_one_row_per_match(seeded: sqlite3.Connection) -> None:
    stored = seeded.execute("SELECT count(*) FROM matches").fetchone()[0]
    rows = matches(seeded)
    assert len(rows) == stored
    assert [int(row["match_id"]) for row in rows] == sorted(int(row["match_id"]) for row in rows)


def test_a_2v2_is_flattened_into_two_sides(seeded: sqlite3.Connection) -> None:
    """The seed's second match is Ana+Cal against Ben+Dee, in team and member order."""
    row = matches(seeded, match_id=2)[0]
    assert row["teams"] == "Reds vs Blues"
    assert row["players"] == "Ana+Cal vs Ben+Dee"


def test_a_solo_team_is_named_after_its_player(seeded: sqlite3.Connection) -> None:
    """#17 only names a team when somebody types one, so a singles match has none."""
    assert seeded.execute("SELECT name FROM teams WHERE match_id = 1").fetchone()["name"] is None
    row = matches(seeded, match_id=1)[0]
    assert row["teams"] == "Ana vs Ben"
    assert row["players"] == "Ana vs Ben"
    assert row["winner_team_name"] == "Ana"


def test_the_status_vocabulary_is_the_one_the_rest_of_the_api_uses(
    seeded: sqlite3.Connection,
) -> None:
    statuses = {row["status"] for row in matches(seeded)}
    assert statuses <= {"in_progress", "complete", "abandoned"}
    assert matches(seeded, match_id=1)[0]["status"] == "complete"


def test_a_completed_match_carries_its_winner_and_an_unfinished_one_does_not(
    seeded: sqlite3.Connection,
) -> None:
    complete = matches(seeded, match_id=1)[0]
    assert complete["winner_team_id"] != ""
    assert complete["completed_at"] != ""
    unfinished = matches(seeded, match_id=2)[0]
    assert unfinished["winner_team_id"] == ""
    assert unfinished["winner_team_name"] == ""
    assert unfinished["completed_at"] == ""


def test_the_match_dart_count_agrees_with_the_darts_export(seeded: sqlite3.Connection) -> None:
    """The cross-check that makes both files trustworthy together."""
    for row in matches(seeded):
        match_id = int(row["match_id"])
        assert int(row["darts_thrown"]) == len(darts(seeded, match_id=match_id)), match_id


def test_leg_counts_come_from_the_legs_not_from_the_darts(seeded: sqlite3.Connection) -> None:
    for row in matches(seeded):
        match_id = int(row["match_id"])
        played, completed = seeded.execute(
            "SELECT count(*), count(completed_at) FROM legs WHERE match_id = ?", (match_id,)
        ).fetchone()
        assert int(row["legs_played"]) == played
        assert int(row["legs_completed"]) == completed


# --- the filters ------------------------------------------------------------


def test_match_id_narrows_both_files_to_one_match(seeded: sqlite3.Connection) -> None:
    assert {row["match_id"] for row in darts(seeded, match_id=3)} == {"3"}
    assert [row["match_id"] for row in matches(seeded, match_id=3)] == ["3"]


def test_game_type_and_variant_narrow_the_export(seeded: sqlite3.Connection) -> None:
    assert {row["game_type"] for row in darts(seeded, game_type="cricket")} == {"cricket"}
    cutthroat = matches(seeded, game_type="cricket", variant="cutthroat")
    assert [row["variant"] for row in cutthroat] == ["cutthroat"]


def test_since_cuts_on_the_matchs_creation_so_no_match_is_split(
    seeded: sqlite3.Connection,
) -> None:
    later = darts(seeded, since="2026-01-04T00:00:00.000Z")
    assert {row["match_id"] for row in later} == {"3", "4", "5"}
    # Every dart of every match that survived the cut is present, not just the
    # ones thrown after the instant.
    assert len(later) == sum(len(darts(seeded, match_id=m)) for m in (3, 4, 5))


def test_a_filter_matching_nothing_still_writes_the_header(seeded: sqlite3.Connection) -> None:
    """An empty export is a valid CSV with column names, not a zero-byte file."""
    empty = list(export.darts_csv(seeded, StatsFilter(match_id=9999)))
    assert len(empty) == 1
    assert empty[0].startswith("match_id,")
    assert parse(iter(empty)) == []


# --- streaming --------------------------------------------------------------


def test_the_exports_are_generators_that_yield_the_header_first(
    seeded: sqlite3.Connection,
) -> None:
    """The whole result is never a list: the first line is available immediately."""
    for stream in (
        export.darts_csv(seeded, StatsFilter()),
        export.matches_csv(seeded, StatsFilter()),
    ):
        assert isinstance(stream, Iterator)
        first = next(stream)
        assert first.endswith(export.LINE_TERMINATOR)
        assert "," in first
        stream.close()  # type: ignore[attr-defined]


def test_taking_one_line_does_not_read_the_whole_table(seeded: sqlite3.Connection) -> None:
    """Proved by the transaction: it is still open after the header, and only then."""
    stream = export.darts_csv(seeded, StatsFilter())
    assert not seeded.in_transaction
    next(stream)
    assert seeded.in_transaction, "the read transaction is open, mid-stream"
    stream.close()  # type: ignore[attr-defined]
    assert not seeded.in_transaction, "and closed when the consumer goes away"


def test_abandoning_an_export_rolls_its_read_transaction_back(
    seeded: sqlite3.Connection,
) -> None:
    """What Starlette does to the generator when a client disconnects."""
    stream = export.matches_csv(seeded, StatsFilter())
    next(stream)
    next(stream)
    stream.close()  # type: ignore[attr-defined]
    assert not seeded.in_transaction


def test_the_line_terminator_is_the_one_rfc_4180_asks_for(seeded: sqlite3.Connection) -> None:
    text = "".join(export.darts_csv(seeded, StatsFilter()))
    assert export.LINE_TERMINATOR == "\r\n"
    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", "")


def test_a_value_containing_a_comma_is_quoted_by_csv_itself(
    seeded: sqlite3.Connection,
) -> None:
    """Nothing here formats a field by hand, so quoting is `csv`'s problem."""
    seeded.execute("UPDATE players SET display_name = ? WHERE id = 1", ('Ana "the arrow", Jr',))
    row = parse(export.darts_csv(seeded, StatsFilter(match_id=1)))[0]
    assert row["player_name"] == 'Ana "the arrow", Jr'


def test_an_abandoned_match_is_exported_as_abandoned(seeded: sqlite3.Connection) -> None:
    """The third member of the status vocabulary; the seed has no abandoned match.

    0002 makes `abandoned_at` and `winner_team_id` mutually exclusive, so an
    abandoned match can never carry a winner -- and its darts stay in
    `darts.csv`, because they were thrown.
    """
    with transaction(seeded):
        abandon_match(seeded, 2)

    row = matches(seeded, match_id=2)[0]
    assert row["status"] == "abandoned"
    assert row["abandoned_at"] != ""
    assert row["completed_at"] == ""
    assert row["winner_team_id"] == ""
    assert row["winner_team_name"] == ""
    assert int(row["darts_thrown"]) == len(darts(seeded, match_id=2)) > 0
