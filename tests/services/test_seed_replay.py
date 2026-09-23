"""Replay the seeded fixture's darts through the service and compare the rows.

The fixture was written for #13 by driving the pure engine directly, before any
of this existed. Feeding its darts back through `play.throw` one at a time and
getting the same visits, the same `counted` and `caused_bust` flags, the same
cricket effects and the same point events is the strongest statement available
that the service records what the rules actually say -- across both x01 games,
a doubles rotation, a bust, a bull finish and all three cricket variants, with
no expected values written out by hand anywhere.
"""

import sqlite3

import pytest
from seed import MATCHES, PLAYERS, MatchSpec, seed
from servicefixtures import make_match, snapshot

from darts.engine.throws import Throw
from darts.repo.darts import darts_for_leg
from darts.repo.legs import legs_for_match
from darts.repo.matches import CreatedMatch, get_match
from darts.repo.players import create_player
from darts.services import play


@pytest.fixture
def seeded(db: sqlite3.Connection) -> sqlite3.Connection:
    """The #13 fixture, in the same database the service will write into.

    One database rather than two: the seeded matches take the low ids and the
    replayed one is created afterwards, so nothing collides and there is no
    second connection to keep in step.
    """
    seed(db)
    return db


def _seed_team_ids(spec: MatchSpec) -> list[int]:
    return [spec.team_id(index) for index in range(len(spec.members))]


def _replay_spec(conn: sqlite3.Connection, spec: MatchSpec) -> CreatedMatch:
    """Recreate one seeded match and throw all of its recorded darts at it.

    The players are fresh, because a name is unique among active players and
    the seed already holds every one of them.
    """
    members = [
        [create_player(conn, f"{PLAYERS[p - 1]} again").id for p in team] for team in spec.members
    ]
    created = make_match(conn, spec.config, members)
    leg_id: int | None = created.leg_id
    for leg_index in range(len(spec.legs)):
        assert leg_id is not None, "the service ended the match before the seed did"
        for dart in darts_for_leg(conn, spec.leg_id(leg_index)):
            state = play.throw(
                conn,
                leg_id=leg_id,
                dart=Throw(dart.segment, dart.multiplier),
                client_dart_id=f"replay:{dart.client_dart_id}",
            )
        leg_id = state.active_leg_id
    return created


@pytest.mark.parametrize("spec", MATCHES, ids=lambda spec: f"match{spec.id}-{spec.game_type}")
def test_the_service_reproduces_the_seeded_rows(
    seeded: sqlite3.Connection, spec: MatchSpec
) -> None:
    created = _replay_spec(seeded, spec)

    legs = legs_for_match(seeded, created.match_id)
    for leg_index, leg in enumerate(legs[: len(spec.legs)]):
        expected = snapshot(seeded, spec.leg_id(leg_index), _seed_team_ids(spec))
        assert snapshot(seeded, leg.id, created.team_ids) == expected, f"leg {leg_index}"
    # The seed stops after its scripted legs; the service opens the next one
    # whenever the match is still alive, so any extra leg must be an empty one.
    for extra in legs[len(spec.legs) :]:
        assert darts_for_leg(seeded, extra.id) == []
        assert extra.winner_team_id is None


@pytest.mark.parametrize("spec", MATCHES, ids=lambda spec: f"match{spec.id}-{spec.game_type}")
def test_the_service_reproduces_the_seeded_winners(
    seeded: sqlite3.Connection, spec: MatchSpec
) -> None:
    created = _replay_spec(seeded, spec)
    seed_teams = _seed_team_ids(spec)

    def as_index(team_id: int | None) -> int | None:
        return None if team_id is None else seed_teams.index(team_id)

    def as_new(team_id: int | None) -> int | None:
        index = as_index(team_id)
        return None if index is None else created.team_ids[index]

    assert get_match(seeded, created.match_id).winner_team_id == as_new(
        get_match(seeded, spec.id).winner_team_id
    )
    original = {leg.leg_index: leg for leg in legs_for_match(seeded, spec.id)}
    for leg in legs_for_match(seeded, created.match_id):
        if leg.leg_index not in original:
            continue  # An empty leg the service opened past the seed's script.
        assert leg.winner_team_id == as_new(original[leg.leg_index].winner_team_id)
        assert leg.starting_team_id == as_new(original[leg.leg_index].starting_team_id)
