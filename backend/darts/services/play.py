"""One dart, one transaction. The only module that writes recorded play.

Every call to `throw` does the same seven things: load the leg's darts, replay
them, replay them again with the new dart on the end, write the rows the
difference implies, refresh the caches, advance the leg and the match if the
dart finished either, and hand back the whole state. The transaction is opened
here and closed here, so a power cut costs the dart in flight and nothing else
(#12).

Nothing on this path re-derives a rule. `engine.replay.replay` decides what a
sequence of darts means and `engine.rotation.starting_team` decides who opens a
leg; `darts.services.derive` turns rows into those types and their answers back
into rows. This module only writes what it is told.

Why the leg is replayed rather than read
----------------------------------------
`leg_team_state` and `cricket_leg_state` are written here and never read here.
They are a cache for other readers, and `rebuild_caches` recomputes them from
`darts` alone. If the service trusted them it would have two sources of truth
and no way to tell which had drifted -- which is exactly what `darts-verify`
exists to check.

Their invariant is narrow and deliberate: **a leg holds cache rows exactly when
it has at least one dart and no winner.** A leg nobody has thrown into has
nothing to resume, and a finished leg is reconstructed from its darts, which is
#13's decision and what the seeded fixture stores. So completing a leg deletes
its cache rows, and undoing back to an empty leg deletes them again.

Why `replay_match` is not used
------------------------------
`engine.replay.replay_match` re-derives each leg's starting team from the start
rule. The database does not need it to: `legs.starting_team_id` is a real
column, written when the leg was opened, and a leg must replay against the team
that actually started it. Asking the engine to work it out a second time would
put a derived answer next to a stored one and offer no way to choose. So each
leg replays against its own stored starter, the match tally is counted off
`legs.winner_team_id`, and `starting_team` -- the same function `create_match`
uses for leg 0 -- is called once, to open the next leg.

Undo is a hard delete
---------------------
The last dart's row is deleted, its cricket effect and point events go with it
by cascade, and if it was the only dart of its visit the visit row goes too --
the schema will not do that for you. There is no `is_undone` flag and no redo:
an undo is a misclick, not data. Busts are the opposite and are preserved in
full, offending dart and all, because a bust is something that really happened.
Undoing a winning dart reopens the leg, and the match with it.
"""

import sqlite3
from collections.abc import Sequence

from darts.db.connection import transaction
from darts.engine import cricket
from darts.engine.replay import LegState, VisitOutcome
from darts.engine.rotation import starting_team
from darts.engine.throws import Throw
from darts.repo.darts import (
    CricketEffect,
    Dart,
    NewDart,
    PointAward,
    append_dart,
    dart_for_client_id,
    darts_for_leg,
    delete_dart,
    record_cricket_effect,
    record_point_awards,
    set_counted,
)
from darts.repo.legs import Leg, create_leg, delete_leg, legs_for_match, set_leg_winner
from darts.repo.legstate import clear_leg_state, write_leg_state
from darts.repo.matches import set_match_winner
from darts.repo.visits import (
    NewVisit,
    Visit,
    create_visit,
    delete_visit,
    update_visit,
    visits_for_leg,
)
from darts.services import derive
from darts.services import state as public
from darts.services.derive import Board, VisitVerdict
from darts.services.errors import (
    IdempotencyConflictError,
    LegCompleteError,
    MatchAbandonedError,
    MatchCompleteError,
    NothingToUndoError,
)


def _sync_counted(
    conn: sqlite3.Connection, leg_id: int, visit_id: int, flags: Sequence[bool]
) -> None:
    """Make the visit's stored `counted` columns agree with the replay.

    Only a bust moves them, and it moves them in both directions: it voids
    darts that counted when they landed, and undoing it counts them again.
    """
    rows = [dart for dart in darts_for_leg(conn, leg_id) if dart.visit_id == visit_id]
    for row, counted in zip(rows, flags, strict=True):
        if row.counted != counted:
            set_counted(conn, row.id, counted)


def _write_visit_row(
    conn: sqlite3.Connection,
    board: Board,
    *,
    visit_index: int,
    team_visit_index: int,
    team_id: int,
    player_id: int,
    dart_index: int,
    verdict: VisitVerdict,
) -> int:
    """Open the visit this dart belongs to, or bring the open one up to date."""
    if dart_index == 0:
        return create_visit(
            conn,
            NewVisit(
                leg_id=board.leg.id,
                match_id=board.match.id,
                team_id=team_id,
                player_id=player_id,
                visit_index=visit_index,
                team_visit_index=team_visit_index,
                score_before=verdict.score_before,
                score_after=verdict.score_after,
                is_bust=verdict.is_bust,
                is_complete=verdict.is_complete,
            ),
        ).id
    visit = visits_for_leg(conn, board.leg.id)[visit_index]
    update_visit(
        conn,
        visit.id,
        score_after=verdict.score_after,
        is_bust=verdict.is_bust,
        is_complete=verdict.is_complete,
    )
    return visit.id


def _write_cricket_rows(
    conn: sqlite3.Connection, board: Board, dart_id: int, team_index: int, outcome: VisitOutcome
) -> None:
    """The effect row for this cricket dart, and one row per team it paid.

    The engine names recipients by their position in the `opponents` tuple it
    was handed, which `replay` builds as the other teams in index order; the
    same construction here turns those back into team ids. `None` means the
    thrower, which only `standard` ever emits.
    """
    assert isinstance(outcome, cricket.VisitOutcome)
    dart = outcome.darts[-1]
    record_cricket_effect(
        conn,
        CricketEffect(
            dart_id=dart_id,
            target=dart.target,
            counted_marks=dart.counted_marks,
            surplus_marks=dart.surplus_marks,
            wasted=dart.wasted,
        ),
    )
    others = [i for i in range(len(board.teams)) if i != team_index]
    record_point_awards(
        conn,
        [
            PointAward(
                dart_id=dart_id,
                leg_id=board.leg.id,
                match_id=board.match.id,
                recipient_team_id=board.team_ids[
                    team_index if event.recipient is None else others[event.recipient]
                ],
                points=event.points,
            )
            for event in dart.point_events
        ],
    )


def _record(
    conn: sqlite3.Connection,
    board: Board,
    before: LegState,
    after: LegState,
    client_dart_id: str,
) -> None:
    """Write every row the new dart implies, in foreign-key order."""
    visit = after.visits[-1]
    team_index = visit.thrower.team_index
    team = board.match.teams[team_index]
    player_id = team.members[visit.thrower.member_index].player_id
    dart_index = len(visit.outcome.darts) - 1
    verdict = derive.visit_verdict(visit.outcome)
    flags = derive.dart_flags(board, visit.outcome, -1, before.teams[team_index])

    visit_id = _write_visit_row(
        conn,
        board,
        visit_index=len(after.visits) - 1,
        team_visit_index=sum(1 for v in after.visits[:-1] if v.thrower.team_index == team_index),
        team_id=team.id,
        player_id=player_id,
        dart_index=dart_index,
        verdict=verdict,
    )
    dart = append_dart(
        conn,
        NewDart(
            visit_id=visit_id,
            leg_id=board.leg.id,
            team_id=team.id,
            player_id=player_id,
            seq_in_leg=len(before.darts),
            dart_index=dart_index,
            segment=after.darts[-1].segment,
            multiplier=after.darts[-1].multiplier,
            counted=flags.counted,
            client_dart_id=client_dart_id,
            caused_bust=flags.caused_bust,
            was_checkout_attempt=flags.was_checkout_attempt,
        ),
    )
    if verdict.is_bust:
        _sync_counted(conn, board.leg.id, visit_id, derive.counted_flags(visit.outcome))
    if not board.is_x01:
        _write_cricket_rows(conn, board, dart.id, team_index, visit.outcome)


def _restore_visit(conn: sqlite3.Connection, board: Board, leg: LegState) -> None:
    """Put the visit an undone dart came from back to what it now is.

    Undoing across a bust is the case that matters: the visit reverts to
    incomplete with the score its surviving darts made, and the darts the bust
    had voided count again. When the undone dart was the only one in its visit
    the row is already gone, and this rewrites the previous visit with its own
    unchanged values -- a no-op worth having over a special case.
    """
    if not leg.visits:
        return
    outcome = leg.visits[-1].outcome
    verdict = derive.visit_verdict(outcome)
    visit_id = visits_for_leg(conn, board.leg.id)[-1].id
    update_visit(
        conn,
        visit_id,
        score_after=verdict.score_after,
        is_bust=verdict.is_bust,
        is_complete=verdict.is_complete,
    )
    _sync_counted(conn, board.leg.id, visit_id, derive.counted_flags(outcome))


def _write_caches(conn: sqlite3.Connection, board: Board, leg: LegState) -> None:
    """Bring the leg's cache rows in line with the replay, deleting if it holds none."""
    rows = derive.cache_rows(board, leg)
    if not rows:
        clear_leg_state(conn, board.leg.id)
        return
    write_leg_state(conn, board.leg.id, board.match.id, rows)


def _leg_is_empty(conn: sqlite3.Connection, leg: Leg) -> bool:
    return leg.winner_team_id is None and not darts_for_leg(conn, leg.id)


def _open_next_leg(conn: sqlite3.Connection, board: Board, previous: Leg) -> None:
    """Create the leg that follows a completed one, with its starting team.

    The rotation reads the stored chain -- who started the previous leg and who
    won it -- so it agrees with `legs.starting_team_id` by construction rather
    than by re-deriving the whole match from the start rule.
    """
    assert previous.winner_team_id is not None
    config = board.match.config
    start = starting_team(
        len(board.teams),
        previous.leg_index + 1,
        config.start_rule,
        config.fixed_team,
        board.team_ids.index(previous.winner_team_id),
        board.team_ids.index(previous.starting_team_id),
    )
    create_leg(
        conn,
        match_id=board.match.id,
        leg_index=previous.leg_index + 1,
        starting_team_id=board.team_ids[start],
    )


def _advance(conn: sqlite3.Connection, board: Board, leg: LegState) -> None:
    """Make the leg, the match and the leg behind them agree with the replay.

    Written as a reconciliation rather than as separate "on win" and "on undo"
    paths: both callers hand it a replayed leg and it writes whatever differs,
    so winning a leg and unwinning it are the same code read in opposite
    directions.
    """
    winner_id = None if leg.winner is None else board.team_ids[leg.winner]
    if board.leg.winner_team_id != winner_id:
        set_leg_winner(conn, board.leg.id, winner_id)

    legs = legs_for_match(conn, board.match.id)
    needed = board.match.config.best_of // 2 + 1
    champion = next(
        (
            team_id
            for team_id in board.team_ids
            if sum(1 for row in legs if row.winner_team_id == team_id) >= needed
        ),
        None,
    )
    if board.match.winner_team_id != champion:
        set_match_winner(conn, board.match.id, champion)

    # One leg is open at a time and it is the last one, so two open legs in a
    # row means undo has just reopened this one and the empty leg behind it --
    # the leg the undone dart opened -- has to go.
    if len(legs) > 1 and legs[-2].winner_team_id is None and _leg_is_empty(conn, legs[-1]):
        delete_leg(conn, legs.pop().id)
    if champion is None and legs[-1].winner_team_id is not None:
        _open_next_leg(conn, board, legs[-1])


def _dart_state(dart: Dart) -> public.DartState:
    return public.DartState(
        dart_id=dart.id,
        dart_index=dart.dart_index,
        segment=dart.segment,
        multiplier=dart.multiplier,
        label=Throw(dart.segment, dart.multiplier).label,
        counted=dart.counted,
        caused_bust=dart.caused_bust,
        was_checkout_attempt=dart.was_checkout_attempt,
    )


def _visit_state(visit: Visit, darts: Sequence[Dart]) -> public.VisitState:
    return public.VisitState(
        visit_id=visit.id,
        team_id=visit.team_id,
        player_id=visit.player_id,
        visit_index=visit.visit_index,
        score_before=visit.score_before,
        score_after=visit.score_after,
        is_bust=visit.is_bust,
        is_complete=visit.is_complete,
        darts=tuple(_dart_state(dart) for dart in darts if dart.visit_id == visit.id),
    )


def _visits(
    conn: sqlite3.Connection, leg_id: int
) -> tuple[public.VisitState | None, public.VisitState | None]:
    """The part-thrown visit and the last finished one, as `LegState` means them.

    Only the final visit of a leg can be incomplete -- a visit ends before the
    next one opens -- so the split is decided by that one row's `is_complete`,
    and the finished visit is whichever of the last two it leaves over. Reading
    the stored flag rather than counting darts keeps this agreeing with
    `derive.visit_verdict`, which is what wrote it: a bust and a checkout both
    finish a visit early and both say so in the column.
    """
    visits = visits_for_leg(conn, leg_id)
    if not visits:
        return None, None
    darts = darts_for_leg(conn, leg_id)
    if visits[-1].is_complete:
        return None, _visit_state(visits[-1], darts)
    previous = _visit_state(visits[-2], darts) if len(visits) > 1 else None
    return _visit_state(visits[-1], darts), previous


def _thrower_state(board: Board, leg: LegState) -> public.Thrower | None:
    if leg.next_thrower is None:
        return None
    team = board.match.teams[leg.next_thrower.team_index]
    member = team.members[leg.next_thrower.member_index]
    return public.Thrower(
        team_id=team.id, player_id=member.player_id, display_name=member.display_name
    )


def _team_leg_state(board: Board, leg: LegState, team_index: int) -> public.TeamLegState:
    cache = derive.team_cache(board, leg, team_index)
    return public.TeamLegState(
        team_id=cache.team_id,
        remaining=cache.remaining,
        is_open=cache.is_open,
        darts_thrown=cache.darts_thrown,
        points=cache.points,
        marks=cache.marks,
    )


def _project(conn: sqlite3.Connection, leg_id: int) -> public.GameState:
    """The public state of a leg, replayed from its darts.

    Deliberately a fresh load rather than the objects the write path was
    holding: what `throw` returns is then exactly what the database will say
    the next time anybody asks it.
    """
    board = derive.load(conn, leg_id)
    leg = derive.replay_stored(conn, board)
    legs = legs_for_match(conn, board.match.id)
    open_leg = next((row for row in legs if row.winner_team_id is None), None)
    current_visit, previous_visit = _visits(conn, leg_id)
    return public.GameState(
        match_id=board.match.id,
        config=board.match.config,
        status=board.match.status,
        teams=board.match.teams,
        legs_won=tuple(
            sum(1 for row in legs if row.winner_team_id == team_id) for team_id in board.team_ids
        ),
        winner_team_id=board.match.winner_team_id,
        is_complete=board.match.winner_team_id is not None,
        active_leg_id=None if open_leg is None else open_leg.id,
        current_leg=public.LegState(
            leg_id=board.leg.id,
            leg_index=board.leg.leg_index,
            starting_team_id=board.leg.starting_team_id,
            winner_team_id=board.leg.winner_team_id,
            is_complete=board.leg.winner_team_id is not None,
            darts_thrown=len(leg.darts),
            darts_left=leg.darts_left,
            next_thrower=_thrower_state(board, leg),
            teams=tuple(_team_leg_state(board, leg, i) for i in range(len(board.teams))),
            current_visit=current_visit,
            previous_visit=previous_visit,
        ),
    )


def _reject_closed(board: Board) -> None:
    """Refuse a write to a leg or a match that has already been decided."""
    if board.match.abandoned_at is not None:
        raise MatchAbandonedError(f"match {board.match.id} is abandoned")
    if board.match.winner_team_id is not None:
        raise MatchCompleteError(f"match {board.match.id} is already won")
    if board.leg.winner_team_id is not None:
        raise LegCompleteError(f"leg {board.leg.id} is already won")


def _check_same_dart(existing: Dart, leg_id: int, dart: Throw) -> None:
    """Check that a reused `client_dart_id` really is describing the same dart."""
    if (
        existing.leg_id != leg_id
        or existing.segment != dart.segment
        or existing.multiplier != dart.multiplier
    ):
        raise IdempotencyConflictError(
            f"client_dart_id {existing.client_dart_id!r} already records a different dart"
        )


def state(conn: sqlite3.Connection, leg_id: int) -> public.GameState:
    """The full public state of a leg. Read-only, and opens no transaction."""
    return _project(conn, leg_id)


def throw(
    conn: sqlite3.Connection, *, leg_id: int, dart: Throw, client_dart_id: str
) -> public.GameState:
    """Record one dart and return the state that follows it.

    Idempotent on `client_dart_id`: a key already in the database inserts
    nothing and returns the state as it stands, so a double tap or a retry over
    a flaky connection cannot record the same dart twice. The key is looked up
    globally, because the column is unique globally; the same key describing a
    *different* dart raises `IdempotencyConflictError` rather than quietly
    returning somebody else's throw.

    Raises `NotFoundError` for an unknown leg, `LegCompleteError` for a leg
    already won and `MatchCompleteError` for a match already won.
    """
    with transaction(conn):
        existing = dart_for_client_id(conn, client_dart_id)
        if existing is not None:
            _check_same_dart(existing, leg_id, dart)
            return _project(conn, leg_id)

        board = derive.load(conn, leg_id)
        _reject_closed(board)
        before = derive.replay_stored(conn, board)
        after = derive.replay(board, (*before.darts, dart))

        _record(conn, board, before, after, client_dart_id)
        _write_caches(conn, board, after)
        _advance(conn, board, after)
        return _project(conn, leg_id)


def undo(conn: sqlite3.Connection, leg_id: int) -> public.GameState:
    """Delete the leg's last dart and return the state that leaves.

    The dart's cricket effect and point events cascade away with it, and its
    visit is removed too if that dart was the only one in it. Undoing the dart
    that won a leg reopens the leg, closes the empty leg that was opened behind
    it, and unwins the match if that leg decided it.

    Raises `NothingToUndoError` on a leg with no darts, and `LegCompleteError`
    if a later leg has already been played into -- that is history, not a
    misclick, and #15 does not reach back across a leg boundary.
    """
    with transaction(conn):
        board = derive.load(conn, leg_id)
        if board.match.abandoned_at is not None:
            raise MatchAbandonedError(f"match {board.match.id} is abandoned")
        darts = darts_for_leg(conn, leg_id)
        if not darts:
            raise NothingToUndoError(f"leg {leg_id} has no darts to undo")
        for row in legs_for_match(conn, board.match.id):
            if row.leg_index > board.leg.leg_index and darts_for_leg(conn, row.id):
                raise LegCompleteError(f"leg {row.id} has been played; leg {leg_id} is history")

        last = darts[-1]
        delete_dart(conn, last.id)
        if all(dart.visit_id != last.visit_id for dart in darts[:-1]):
            delete_visit(conn, last.visit_id)

        after = derive.replay(board, derive.throws_of(darts[:-1]))
        _restore_visit(conn, board, after)
        _write_caches(conn, board, after)
        _advance(conn, board, after)
        return _project(conn, leg_id)


def rebuild_caches(conn: sqlite3.Connection, leg_id: int) -> None:
    """Recompute a leg's cache rows from its darts, in one transaction.

    A finished leg and a leg nobody has thrown into both end with no rows, per
    the invariant in the module docstring, so this deletes as readily as it
    writes. Over a database that is already correct it changes nothing.
    """
    with transaction(conn):
        board = derive.load(conn, leg_id)
        _write_caches(conn, board, derive.replay_stored(conn, board))
