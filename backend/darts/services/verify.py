"""Recompute everything derived from the raw darts and report what disagrees.

`darts` is the ledger; `visits`, the cricket rows, the two caches and the two
winner columns are all conclusions drawn from it. A conclusion can go stale --
an interrupted write, a restored backup, an older version of the service, a
hand-edited row -- and nothing in the schema would notice. This module replays
every leg and says so.

It shares `darts.services.derive` with the write path on purpose. That makes it
a check of *stored rows against the current rules*, which is the drift a
running Pi actually suffers; it is deliberately not a second implementation of
the rules, and it would not catch a rule that `derive` gets wrong. The service
tests are what hold `derive` to the engine.

`darts-verify` is the console script over it.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from darts.engine import cricket
from darts.engine.replay import LegState
from darts.repo.darts import Dart, cricket_effects_for_leg, darts_for_leg, point_awards_for_leg
from darts.repo.legs import legs_for_match
from darts.repo.legstate import leg_state_for
from darts.repo.matches import get_match
from darts.repo.visits import visits_for_leg
from darts.services import derive
from darts.services.derive import Board


@dataclass(frozen=True, slots=True)
class Drift:
    """One derived value that does not match the darts it comes from."""

    match_id: int
    leg_id: int | None
    subject: str
    stored: str
    expected: str

    def __str__(self) -> str:
        where = f"match {self.match_id}" if self.leg_id is None else f"leg {self.leg_id}"
        return f"{where}: {self.subject} is {self.stored}, expected {self.expected}"


def _drift(board: Board, subject: str, stored: object, expected: object) -> Drift:
    return Drift(
        match_id=board.match.id,
        leg_id=board.leg.id,
        subject=subject,
        stored=repr(stored),
        expected=repr(expected),
    )


def _check_winner(board: Board, leg: LegState) -> Iterator[Drift]:
    """The leg's winner, and the completion timestamp that travels with it."""
    expected = None if leg.winner is None else board.team_ids[leg.winner]
    if board.leg.winner_team_id != expected:
        yield _drift(board, "legs.winner_team_id", board.leg.winner_team_id, expected)
    elif (board.leg.completed_at is None) != (expected is None):
        wanted = "a timestamp" if expected is not None else None
        yield _drift(board, "legs.completed_at", board.leg.completed_at, wanted)


def _check_visits(conn: sqlite3.Connection, board: Board, leg: LegState) -> Iterator[Drift]:
    stored = visits_for_leg(conn, board.leg.id)
    if len(stored) != len(leg.visits):
        yield _drift(board, "visits", len(stored), len(leg.visits))
        return
    for row, replayed in zip(stored, leg.visits, strict=True):
        verdict = derive.visit_verdict(replayed.outcome)
        actual = (row.score_before, row.score_after, row.is_bust, row.is_complete)
        expected = (verdict.score_before, verdict.score_after, verdict.is_bust, verdict.is_complete)
        if actual != expected:
            yield _drift(board, f"visit {row.visit_index}", actual, expected)


def _check_darts(board: Board, rows: list[Dart], leg: LegState) -> Iterator[Drift]:
    """The per-dart judgements, walked in throwing order.

    Each dart is judged against the throwing team's state immediately before
    it, which is a replay of everything up to that dart -- the same measure #7
    defines a checkout attempt by, and the one a bust would otherwise hide.
    """
    for seq, (visit_index, index) in enumerate(_positions(leg)):
        replayed = leg.visits[visit_index]
        prior = derive.replay(board, leg.darts[:seq]).teams[replayed.thrower.team_index]
        flags = derive.dart_flags(board, replayed.outcome, index, prior)
        row = rows[seq]
        actual = (row.counted, row.caused_bust, row.was_checkout_attempt)
        expected = (flags.counted, flags.caused_bust, flags.was_checkout_attempt)
        if actual != expected:
            yield _drift(board, f"dart {row.seq_in_leg}", actual, expected)


def _positions(leg: LegState) -> Iterator[tuple[int, int]]:
    """Every dart of a replayed leg as (visit index, index within the visit)."""
    for visit_index, visit in enumerate(leg.visits):
        for index in range(len(visit.outcome.darts)):
            yield visit_index, index


def _check_cricket(
    conn: sqlite3.Connection, board: Board, rows: list[Dart], leg: LegState
) -> Iterator[Drift]:
    """Effect rows and point events, both keyed on the dart that produced them."""
    effects = cricket_effects_for_leg(conn, board.leg.id)
    awards = point_awards_for_leg(conn, board.leg.id)
    stored = [(a.dart_id, a.recipient_team_id, a.points) for a in awards]
    if board.is_x01:
        if effects or stored:
            yield _drift(board, "cricket rows on an x01 leg", len(effects) + len(stored), 0)
        return
    expected: list[tuple[int, int, int]] = []
    for seq, (visit_index, index) in enumerate(_positions(leg)):
        replayed = leg.visits[visit_index]
        # The leg is cricket, so every visit of it resolved through cricket.
        assert isinstance(replayed.outcome, cricket.VisitOutcome)
        others = [i for i in range(len(board.teams)) if i != replayed.thrower.team_index]
        dart = replayed.outcome.darts[index]
        row = rows[seq]
        effect = effects.get(row.id)
        actual = (
            None
            if effect is None
            else (effect.target, effect.counted_marks, effect.surplus_marks, effect.wasted)
        )
        wanted = (dart.target, dart.counted_marks, dart.surplus_marks, dart.wasted)
        if actual != wanted:
            yield _drift(board, f"cricket effect on dart {row.seq_in_leg}", actual, wanted)
        expected.extend(
            (
                row.id,
                board.team_ids[
                    replayed.thrower.team_index
                    if event.recipient is None
                    else others[event.recipient]
                ],
                event.points,
            )
            for event in dart.point_events
        )
    if sorted(stored) != sorted(expected):
        yield _drift(board, "cricket_point_events", sorted(stored), sorted(expected))


def _check_caches(conn: sqlite3.Connection, board: Board, leg: LegState) -> Iterator[Drift]:
    stored = leg_state_for(conn, board.leg.id)
    expected = derive.cache_rows(board, leg)
    if stored != expected:
        yield _drift(board, "leg cache", stored, expected)


def verify_leg(conn: sqlite3.Connection, leg_id: int) -> list[Drift]:
    """Every derived value of one leg that disagrees with its darts."""
    board = derive.load(conn, leg_id)
    rows = darts_for_leg(conn, leg_id)
    leg = derive.replay(board, derive.throws_of(rows))
    return [
        *_check_winner(board, leg),
        *_check_visits(conn, board, leg),
        *_check_darts(board, rows, leg),
        *_check_cricket(conn, board, rows, leg),
        *_check_caches(conn, board, leg),
    ]


def verify_match(conn: sqlite3.Connection, match_id: int) -> list[Drift]:
    """Every leg of a match, and then the match's own winner."""
    match = get_match(conn, match_id)
    legs = legs_for_match(conn, match_id)
    drifts = [drift for leg in legs for drift in verify_leg(conn, leg.id)]
    needed = match.config.best_of // 2 + 1
    champion = next(
        (
            team.id
            for team in match.teams
            if sum(1 for leg in legs if leg.winner_team_id == team.id) >= needed
        ),
        None,
    )
    if match.winner_team_id != champion:
        drifts.append(
            Drift(
                match_id=match_id,
                leg_id=None,
                subject="matches.winner_team_id",
                stored=repr(match.winner_team_id),
                expected=repr(champion),
            )
        )
    return drifts


def verify_database(conn: sqlite3.Connection) -> list[Drift]:
    """Every match in the database, in id order."""
    ids = [int(row["id"]) for row in conn.execute("SELECT id FROM matches ORDER BY id")]
    return [drift for match_id in ids for drift in verify_match(conn, match_id)]


def counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """How many matches and legs `verify_database` looks at, for the report."""
    matches = int(conn.execute("SELECT count(*) FROM matches").fetchone()[0])
    legs = int(conn.execute("SELECT count(*) FROM legs").fetchone()[0])
    return matches, legs
