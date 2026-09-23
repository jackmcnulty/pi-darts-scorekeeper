"""A deterministic multi-game dataset, built by driving the pure engine.

Every dart is a scripted throw fed through `darts.engine`, and every recorded
flag -- `counted`, `caused_bust`, `is_bust`, the cricket effects and the point
events -- is whatever the real rules produced. Nothing here re-derives scoring
by hand, so #19's golden statistics are checkable against the engine rather
than against arithmetic written out in a fixture.

Determinism
-----------
Every column that the schema would otherwise default from the clock is supplied
explicitly: all IDs are computed from the match, leg, visit and dart indices,
and all timestamps come from `_stamp` off a fixed epoch. The seed uses no
randomness and no wall clock, so `dump` is identical on every run and on every
machine. It is *not* the database file that is stable -- SQLite file bytes carry
page-allocation and version detail that nothing here controls -- but the rows,
which is what a fixture is for. See `dump` for exactly what is compared.

Covered
-------
Six players; a completed solo x01 match including a bust and two checkouts; a
2v2 x01 match on a double in/double out with uncounted opening darts, a
bull finish and a second leg still in progress; one leg of each cricket variant
(`standard` with a closed-out team that had not won, `cutthroat` with points
travelling to opponents, `quick` with wasted surplus); and in-progress legs that
carry the replay caches.

A plain module rather than conftest.py, following `tests/db/dbfixtures.py`: it
is imported directly by tests in other directories and must not need pytest.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from darts.db.connection import connect
from darts.db.migrate import migrate
from darts.db.views import install_views
from darts.engine.checkout import was_checkout_attempt
from darts.engine.cricket import TARGETS, CricketConfig, CricketTeamState, Variant
from darts.engine.cricket import apply_visit as cricket_visit
from darts.engine.rotation import StartRule, starting_team, thrower_for
from darts.engine.throws import Throw
from darts.engine.types import Team
from darts.engine.x01 import Rule, X01Config, X01TeamState
from darts.engine.x01 import apply_dart as x01_dart
from darts.engine.x01 import apply_visit as x01_visit
from darts.engine.x01 import initial_state as x01_initial
from darts.repo.config import GameConfig

#: Player display names, in `players.id` order starting at 1.
PLAYERS: tuple[str, ...] = ("Ana", "Ben", "Cal", "Dee", "Eve", "Fin")

#: The instant every seeded timestamp is measured from. Arbitrary but fixed.
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

#: `schema_migrations.applied_at` is wall-clock by design and belongs to the
#: migration runner, not to the fixture, so it is left out of the comparison.
_UNSEEDED = frozenset({"schema_migrations"})


def _stamp(seconds: int) -> str:
    """A timestamp in the exact format the schema's own defaults produce."""
    return (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@dataclass(frozen=True)
class MatchSpec:
    """One scripted match. `legs` holds throw labels: leg -> visit -> darts."""

    id: int
    game_type: str
    best_of: int
    members: tuple[tuple[int, ...], ...]
    legs: tuple[tuple[tuple[str, ...], ...], ...]
    names: tuple[str | None, ...] = ()
    variant: str | None = None
    start_score: int | None = None
    in_rule: str | None = None
    out_rule: str | None = None

    @property
    def team_names(self) -> tuple[str | None, ...]:
        return self.names or (None,) * len(self.members)

    @property
    def is_solo(self) -> tuple[int, ...]:
        return tuple(int(len(m) == 1) for m in self.members)

    @property
    def config(self) -> GameConfig:
        """The validated settings this match was created with.

        Built through `GameConfig` rather than assembled by hand so that the
        fixture stores exactly what `create_match` stores. #13 wrote this by
        hand, before #14 existed, and included a `teams` key that `GameConfig`
        forbids -- which made every seeded match unreadable through
        `repo.get_match`. The teams were always in the `teams` table anyway.
        """
        return GameConfig(
            game_type=self.game_type,
            best_of=self.best_of,
            variant=self.variant,
            start_score=self.start_score,
            in_rule=self.in_rule,
            out_rule=self.out_rule,
        )

    @property
    def config_json(self) -> str:
        """The settings the match was created with, as the service stores them."""
        return self.config.to_json()

    def team_id(self, team_index: int) -> int:
        return self.id * 100 + team_index

    def leg_id(self, leg_index: int) -> int:
        return self.id * 1000 + leg_index

    @property
    def legs_to_win(self) -> int:
        return self.best_of // 2 + 1


MATCHES: tuple[MatchSpec, ...] = (
    # Solo 301. Leg 0 has Ben busting on his third dart and Ana finishing with
    # a two-dart visit; both legs go to Ana, so the match completes 2-0.
    MatchSpec(
        id=1,
        game_type="x01",
        best_of=3,
        start_score=301,
        in_rule="straight",
        out_rule="double",
        members=((1,), (2,)),
        legs=(
            (
                ("T20", "T20", "T20"),
                ("T20", "T20", "T19"),
                ("20", "20", "20"),
                ("T20", "T20", "20"),
                ("T15", "D8"),
            ),
            (
                ("T20", "T20", "T20"),
                ("T20", "T20", "T20"),
                ("20", "20", "20"),
                ("T20", "T15", "D8"),
            ),
        ),
    ),
    # 2v2 501, double in and double out: the opening darts of each team are
    # thrown but uncounted, and Cal finishes on the inner bull. Leg 1 is left
    # in progress, so the match has no winner and carries a replay cache.
    MatchSpec(
        id=2,
        game_type="x01",
        best_of=3,
        start_score=501,
        in_rule="double",
        out_rule="double",
        members=((1, 3), (2, 4)),
        names=("Reds", "Blues"),
        legs=(
            (
                ("20", "20", "D20"),
                ("T20", "D20", "T20"),
                ("T20", "T20", "T20"),
                ("T20", "T20", "T20"),
                ("T20", "T20", "T20"),
                ("T20", "T20", "T20"),
                ("T17", "BULL"),
            ),
            (
                ("D20", "T20", "T20"),
                ("T20", "T20", "D10"),
            ),
        ),
    ),
    # Standard cricket. Fin closes all seven in visit 5 and does *not* win,
    # because Eve is 111 points ahead; Eve then closes the bull and takes it.
    MatchSpec(
        id=3,
        game_type="cricket",
        best_of=3,
        variant="standard",
        members=((5,), (6,)),
        legs=(
            (
                ("T20", "T20", "T19"),
                ("T20", "T19", "T18"),
                ("T18", "T17", "T17"),
                ("T17", "T16", "T15"),
                ("T16", "T15", "BULL"),
                ("BULL", "25", "MISS"),
                ("25",),
            ),
            (
                ("T20", "T20", "T19"),
                ("T20", "T18", "MISS"),
            ),
        ),
    ),
    # Cut-throat with three solo teams: Ana's only surplus pays both opponents
    # 60 apiece, and she wins on zero points precisely by giving them away.
    MatchSpec(
        id=4,
        game_type="cricket",
        best_of=3,
        variant="cutthroat",
        members=((1,), (2,), (3,)),
        legs=(
            (
                ("T20", "T20", "T19"),
                ("T20", "T19", "T18"),
                ("T20", "T19", "T18"),
                ("T18", "T17", "T16"),
                ("T17", "T16", "T15"),
                ("T17", "T16", "T15"),
                ("T15", "BULL", "25"),
            ),
        ),
    ),
    # Quick cricket: Eve's surplus on a live 20 is wasted anyway, and closing
    # the bull ends the leg on the spot regardless of points.
    MatchSpec(
        id=5,
        game_type="cricket",
        best_of=3,
        variant="quick",
        members=((5,), (6,)),
        legs=(
            (
                ("T20", "T19", "T18"),
                ("T20", "T19", "T18"),
                ("T20", "T17", "T16"),
                ("T17", "T16", "T15"),
                ("T15", "BULL", "25"),
            ),
        ),
    ),
)


@dataclass
class _Leg:
    """What one played leg needs to write, once the engine has resolved it."""

    winner: int | None = None
    darts: list[int] = field(default_factory=list)
    x01: list[X01TeamState] = field(default_factory=list)
    cricket: list[CricketTeamState] = field(default_factory=list)


def _write_visit(
    conn: sqlite3.Connection,
    match: MatchSpec,
    leg_index: int,
    visit_index: int,
    team_index: int,
    player_id: int,
    before: int,
    after: int,
    is_bust: bool,
    is_complete: bool,
) -> int:
    """Insert one visit row and return its id."""
    visit_id = match.leg_id(leg_index) * 100 + visit_index
    conn.execute(
        """INSERT INTO visits(id, leg_id, match_id, team_id, player_id, visit_index,
                              team_visit_index, score_before, score_after, is_bust, is_complete)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            visit_id,
            match.leg_id(leg_index),
            match.id,
            match.team_id(team_index),
            player_id,
            visit_index,
            visit_index // len(match.members),
            before,
            after,
            int(is_bust),
            int(is_complete),
        ),
    )
    return visit_id


def _write_dart(
    conn: sqlite3.Connection,
    match: MatchSpec,
    leg_index: int,
    visit_id: int,
    team_index: int,
    player_id: int,
    dart_index: int,
    seq_in_leg: int,
    throw: Throw,
    counted: bool,
    caused_bust: bool = False,
    attempt: bool = False,
) -> int:
    """Insert one dart row and return its id.

    `client_dart_id` is a readable, globally unique token rather than an opaque
    one: real clients mint UUIDs, but a fixture needs values that reproduce and
    that name the row they belong to when an assertion fails. The `seed:` prefix
    keeps fixture darts obviously distinguishable from anything a client sent.
    """
    dart_id = visit_id * 10 + dart_index
    conn.execute(
        """INSERT INTO darts(id, visit_id, leg_id, team_id, player_id, seq_in_leg, dart_index,
                             segment, multiplier, counted, caused_bust, was_checkout_attempt,
                             client_dart_id, thrown_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            dart_id,
            visit_id,
            match.leg_id(leg_index),
            match.team_id(team_index),
            player_id,
            seq_in_leg,
            dart_index,
            throw.segment,
            throw.multiplier,
            int(counted),
            int(caused_bust),
            int(attempt),
            f"seed:m{match.id}:l{leg_index}:d{seq_in_leg}",
            _stamp(match.id * 86400 + leg_index * 3600 + 600 + seq_in_leg * 20),
        ),
    )
    return dart_id


def _thrower(match: MatchSpec, start: int, visit_index: int) -> tuple[int, int]:
    """The (team index, player id) whose visit this is, per #10's rotation."""
    teams = tuple(Team(tuple(PLAYERS[p - 1] for p in members)) for members in match.members)
    team_index = (start + visit_index) % len(teams)
    thrower = thrower_for(teams, team_index, visit_index // len(teams))
    return team_index, match.members[team_index][thrower.member_index]


def _play_x01_leg(conn: sqlite3.Connection, match: MatchSpec, leg_index: int, start: int) -> _Leg:
    """Drive one x01 leg through the engine and record every row it implies."""
    assert match.start_score is not None and match.in_rule and match.out_rule
    cfg = X01Config(match.start_score, Rule(match.in_rule), Rule(match.out_rule))
    states = [x01_initial(cfg) for _ in match.members]
    leg = _Leg(darts=[0] * len(match.members))
    seq = 0

    for visit_index, labels in enumerate(match.legs[leg_index]):
        team_index, player_id = _thrower(match, start, visit_index)
        throws = tuple(Throw.parse(label) for label in labels)
        before = states[team_index]
        outcome = x01_visit(before, throws, cfg)

        # A bust rewinds every dart's reported state to the visit's opening
        # score, so the running totals a checkout attempt is judged against
        # have to be replayed separately rather than read back off `outcome`.
        running = before
        opening = []
        for throw in throws:
            opening.append(running)
            step = x01_dart(running, throw, cfg)
            running = step.state
            if step.bust is not None or step.checkout:
                break
        assert len(opening) == len(outcome.darts)

        visit_id = _write_visit(
            conn,
            match,
            leg_index,
            visit_index,
            team_index,
            player_id,
            outcome.score_before,
            outcome.score_after,
            outcome.is_bust,
            len(outcome.darts) == 3 or outcome.is_bust or outcome.checkout,
        )
        for dart_index, (dart, state) in enumerate(zip(outcome.darts, opening, strict=True)):
            _write_dart(
                conn,
                match,
                leg_index,
                visit_id,
                team_index,
                player_id,
                dart_index,
                seq,
                dart.throw,
                dart.counted,
                dart.bust is not None,
                state.is_open and was_checkout_attempt(state.remaining, cfg.out_rule),
            )
            seq += 1

        states[team_index] = outcome.state
        leg.darts[team_index] = outcome.state.darts
        if outcome.checkout:
            leg.winner = team_index
            break

    leg.x01 = states
    return leg


def _play_cricket_leg(
    conn: sqlite3.Connection, match: MatchSpec, leg_index: int, start: int
) -> _Leg:
    """Drive one cricket leg through the engine and record every row it implies."""
    assert match.variant is not None
    cfg = CricketConfig(Variant(match.variant))
    states = [CricketTeamState(marks=(0,) * len(TARGETS), points=0) for _ in match.members]
    leg = _Leg(darts=[0] * len(match.members))
    seq = 0

    for visit_index, labels in enumerate(match.legs[leg_index]):
        team_index, player_id = _thrower(match, start, visit_index)
        others = [i for i in range(len(match.members)) if i != team_index]
        throws = tuple(Throw.parse(label) for label in labels)
        outcome = cricket_visit(states[team_index], throws, cfg, tuple(states[i] for i in others))

        visit_id = _write_visit(
            conn,
            match,
            leg_index,
            visit_index,
            team_index,
            player_id,
            outcome.points_before,
            outcome.points_after,
            False,
            len(outcome.darts) == 3 or outcome.win,
        )
        for dart_index, dart in enumerate(outcome.darts):
            # Cricket has no bust and no in-rule, so every dart counts and the
            # detail of what it did lives in the effects row, as the data model
            # says. A dart that missed every target still records an effect,
            # with a NULL target and nothing else.
            dart_id = _write_dart(
                conn,
                match,
                leg_index,
                visit_id,
                team_index,
                player_id,
                dart_index,
                seq,
                dart.throw,
                counted=True,
            )
            conn.execute(
                "INSERT INTO cricket_dart_effects VALUES (?, ?, ?, ?, ?)",
                (dart_id, dart.target, dart.counted_marks, dart.surplus_marks, int(dart.wasted)),
            )
            for event in dart.point_events:
                recipient = team_index if event.recipient is None else others[event.recipient]
                conn.execute(
                    "INSERT INTO cricket_point_events VALUES (?, ?, ?, ?, ?)",
                    (
                        dart_id,
                        match.leg_id(leg_index),
                        match.id,
                        match.team_id(recipient),
                        event.points,
                    ),
                )
            seq += 1

        states[team_index] = outcome.state
        for index, state in zip(others, outcome.opponents, strict=True):
            states[index] = state
        leg.darts[team_index] += len(outcome.darts)
        if outcome.win:
            leg.winner = team_index
            break

    leg.cricket = states
    return leg


def _write_caches(conn: sqlite3.Connection, match: MatchSpec, leg_index: int, leg: _Leg) -> None:
    """Populate the replay caches for a leg that is still being played.

    These tables exist to resume an interrupted leg, so a finished leg has no
    business holding a row in them; every completed leg here leaves them empty
    and is reconstructed from its darts instead.
    """
    if leg.winner is not None:
        return
    for team_index in range(len(match.members)):
        state = leg.x01[team_index] if leg.x01 else None
        conn.execute(
            """INSERT INTO leg_team_state(leg_id, team_id, match_id, remaining, is_open,
                                          darts_thrown, points)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                match.leg_id(leg_index),
                match.team_id(team_index),
                match.id,
                state.remaining if state else None,
                int(state.is_open) if state else None,
                leg.darts[team_index],
                0 if state else leg.cricket[team_index].points,
            ),
        )
        if not leg.cricket:
            continue
        for target, marks in leg.cricket[team_index].marks_by_target.items():
            conn.execute(
                "INSERT INTO cricket_leg_state VALUES (?, ?, ?, ?)",
                (match.leg_id(leg_index), match.team_id(team_index), target, marks),
            )


def seed(conn: sqlite3.Connection) -> None:
    """Write the whole dataset into an already-migrated database.

    One transaction: a half-seeded fixture is never useful, and the foreign
    keys make the order matter.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        for player_id, name in enumerate(PLAYERS, start=1):
            conn.execute(
                "INSERT INTO players(id, display_name, is_archived, created_at) VALUES (?,?,0,?)",
                (player_id, name, _stamp(player_id * 60)),
            )

        for match in MATCHES:
            conn.execute(
                """INSERT INTO matches(id, config_json, game_type, variant, start_score,
                                       in_rule, out_rule, best_of, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    match.id,
                    match.config_json,
                    match.game_type,
                    match.variant,
                    match.start_score,
                    match.in_rule,
                    match.out_rule,
                    match.best_of,
                    _stamp(match.id * 86400),
                ),
            )
            for team_index, members in enumerate(match.members):
                conn.execute(
                    "INSERT INTO teams(id, match_id, team_index, name, is_solo) VALUES (?,?,?,?,?)",
                    (
                        match.team_id(team_index),
                        match.id,
                        team_index,
                        match.team_names[team_index],
                        match.is_solo[team_index],
                    ),
                )
                for member_index, player_id in enumerate(members):
                    conn.execute(
                        "INSERT INTO team_members VALUES (?, ?, ?)",
                        (match.team_id(team_index), player_id, member_index),
                    )

            wins = [0] * len(match.members)
            completed: str | None = None
            for leg_index in range(len(match.legs)):
                start = starting_team(len(match.members), leg_index, StartRule.ALTERNATE)
                started = match.id * 86400 + leg_index * 3600 + 600
                conn.execute(
                    """INSERT INTO legs(id, match_id, leg_index, starting_team_id, started_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        match.leg_id(leg_index),
                        match.id,
                        leg_index,
                        match.team_id(start),
                        _stamp(started),
                    ),
                )
                play = _play_x01_leg if match.game_type == "x01" else _play_cricket_leg
                leg = play(conn, match, leg_index, start)
                _write_caches(conn, match, leg_index, leg)
                if leg.winner is None:
                    continue
                wins[leg.winner] += 1
                completed = _stamp(started + 3000)
                conn.execute(
                    "UPDATE legs SET winner_team_id = ?, completed_at = ? WHERE id = ?",
                    (match.team_id(leg.winner), completed, match.leg_id(leg_index)),
                )

            if max(wins) >= match.legs_to_win:
                conn.execute(
                    "UPDATE matches SET winner_team_id = ?, completed_at = ? WHERE id = ?",
                    (match.team_id(wins.index(max(wins))), completed, match.id),
                )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def build(path: Path) -> sqlite3.Connection:
    """Create, migrate, install views into and seed a database at `path`.

    Returns the open connection; the caller closes it.
    """
    conn = connect(path)
    try:
        migrate(conn)
        install_views(conn)
        seed(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def dump(conn: sqlite3.Connection) -> str:
    """The canonical text of everything the seed wrote, as sorted JSON.

    Column names travel with the rows so a reordered or renamed column shows up
    as a difference rather than silently shifting values, and rows are sorted so
    that SQLite's scan order is never part of the answer.
    """
    tables = sorted(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        if str(row[0]) not in _UNSEEDED
    )
    snapshot: dict[str, object] = {
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "tables": {
            name: {
                "columns": [str(c[1]) for c in conn.execute(f'PRAGMA table_info("{name}")')],
                "rows": sorted(list(row) for row in conn.execute(f'SELECT * FROM "{name}"')),
            }
            for name in tables
        },
    }
    return json.dumps(snapshot, indent=2, sort_keys=True)


def digest(conn: sqlite3.Connection) -> str:
    """A stable hash of `dump`, for a one-line equality assertion."""
    return hashlib.sha256(dump(conn).encode("utf-8")).hexdigest()
