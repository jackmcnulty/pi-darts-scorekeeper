"""create_match: the four tables, the one transaction, and everything it refuses."""

import sqlite3

import pytest
from repofixtures import CRICKET, X01_501, count, counts, x01_with

from darts.db.connection import transaction
from darts.repo import matches as matches_repo
from darts.repo.config import GameConfig
from darts.repo.errors import InvalidMatchError, NotFoundError
from darts.repo.legs import legs_for_match
from darts.repo.matches import CreatedMatch, TeamSpec, create_match, get_match


def test_2v2_501_writes_one_match_two_teams_four_members_one_leg(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    """The headline acceptance criterion, counted table by table."""
    assert counts(db) == {"matches": 1, "teams": 2, "team_members": 4, "legs": 1}
    assert len(doubles.team_ids) == 2
    assert doubles.leg_id


def test_2v2_teams_are_not_solo(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    match = get_match(db, doubles.match_id)
    assert [team.is_solo for team in match.teams] == [False, False]
    assert [team.name for team in match.teams] == ["Reds", "Blues"]
    assert [len(team.members) for team in match.teams] == [2, 2]


def test_solo_four_player_match(db: sqlite3.Connection, players: list[int]) -> None:
    """Four solo players are four teams of one, every one flagged is_solo."""
    with transaction(db):
        created = create_match(db, X01_501, [TeamSpec((pid,)) for pid in players])

    assert counts(db) == {"matches": 1, "teams": 4, "team_members": 4, "legs": 1}
    match = get_match(db, created.match_id)
    assert len(match.teams) == 4
    for team in match.teams:
        assert team.is_solo is True
        assert len(team.members) == 1


def test_is_solo_follows_the_member_count_not_the_caller(
    db: sqlite3.Connection, players: list[int]
) -> None:
    """Nothing in the schema ties is_solo to the member count, so the repo does.

    A caller cannot ask for a two-person team that claims to be solo, because
    `TeamSpec` has no is_solo to ask with -- it is derived.
    """
    with transaction(db):
        created = create_match(
            db, X01_501, [TeamSpec((players[0], players[1])), TeamSpec((players[2],))]
        )
    rows = db.execute(
        "SELECT t.is_solo, count(*) AS members FROM teams t "
        "JOIN team_members tm ON tm.team_id = t.id WHERE t.match_id = ? "
        "GROUP BY t.id ORDER BY t.team_index",
        (created.match_id,),
    ).fetchall()
    assert [(row["is_solo"], row["members"]) for row in rows] == [(0, 2), (1, 1)]


def test_members_keep_their_throwing_order(db: sqlite3.Connection, players: list[int]) -> None:
    with transaction(db):
        created = create_match(
            db, X01_501, [TeamSpec((players[2], players[0])), TeamSpec((players[1],))]
        )
    team = get_match(db, created.match_id).teams[0]
    assert [m.member_index for m in team.members] == [0, 1]
    assert [m.player_id for m in team.members] == [players[2], players[0]]


def test_cricket_match_is_created_too(db: sqlite3.Connection, players: list[int]) -> None:
    with transaction(db):
        created = create_match(db, CRICKET, [TeamSpec((players[0],)), TeamSpec((players[1],))])
    assert get_match(db, created.match_id).config == CRICKET


# --- leg 0 -------------------------------------------------------------------


def test_leg_zero_is_created_with_a_real_starting_team(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    legs = legs_for_match(db, doubles.match_id)
    assert len(legs) == 1
    assert legs[0].leg_index == 0
    assert legs[0].starting_team_id in doubles.team_ids
    assert legs[0].winner_team_id is None
    assert legs[0].completed_at is None


@pytest.mark.parametrize(
    ("start_rule", "fixed_team", "expected_index"),
    [
        # ALTERNATE ignores fixed_team and always opens on team 0 for leg 0.
        ("alternate", 1, 0),
        ("fixed", 1, 1),
        ("fixed", 0, 0),
        # For leg 0 there is no previous winner, so these fall back to fixed_team.
        ("loser_starts", 2, 2),
        ("winner_starts", 1, 1),
    ],
)
def test_start_rule_decides_who_opens_leg_zero(
    db: sqlite3.Connection,
    players: list[int],
    start_rule: str,
    fixed_team: int,
    expected_index: int,
) -> None:
    config = x01_with(start_rule=start_rule, fixed_team=fixed_team)
    with transaction(db):
        created = create_match(db, config, [TeamSpec((pid,)) for pid in players[:3]])
    leg = legs_for_match(db, created.match_id)[0]
    assert leg.starting_team_id == created.team_ids[expected_index]


def test_fixed_team_outside_the_team_range_writes_nothing(
    db: sqlite3.Connection, players: list[int]
) -> None:
    """`starting_team` is called before the first INSERT precisely for this."""
    config = x01_with(start_rule="fixed", fixed_team=5)
    with pytest.raises(ValueError, match="starter outside team range"), transaction(db):
        create_match(db, config, [TeamSpec((players[0],)), TeamSpec((players[1],))])
    assert counts(db) == {"matches": 0, "teams": 0, "team_members": 0, "legs": 0}


# --- atomicity ---------------------------------------------------------------


def test_create_match_refuses_to_run_outside_a_transaction(
    db: sqlite3.Connection, players: list[int]
) -> None:
    """Without a transaction it could not keep its one promise, so it declines."""
    with pytest.raises(InvalidMatchError, match="must run inside a transaction"):
        create_match(db, X01_501, [TeamSpec((players[0],))])
    assert counts(db) == {"matches": 0, "teams": 0, "team_members": 0, "legs": 0}


def test_injected_failure_leaves_zero_rows(
    db: sqlite3.Connection, players: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion: fail partway through and nothing survives.

    The failure is injected at leg 0, which is the last of the four writes, so
    the match, both teams and all four members are already on disk when it
    fires. They must all go.
    """

    def boom(*args: object, **kwargs: object) -> int:
        raise sqlite3.IntegrityError("injected failure")

    monkeypatch.setattr(matches_repo, "create_leg", boom)

    teams = [TeamSpec((players[0], players[2])), TeamSpec((players[1], players[3]))]
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"), transaction(db):
        create_match(db, X01_501, teams)

    assert counts(db) == {"matches": 0, "teams": 0, "team_members": 0, "legs": 0}
    # The players themselves were committed earlier and are untouched.
    assert count(db, "players") == 4


def test_a_failure_spares_an_earlier_match(
    db: sqlite3.Connection, doubles: CreatedMatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback undoes the failed match, not the committed one before it."""

    def boom(*args: object, **kwargs: object) -> int:
        raise sqlite3.IntegrityError("injected failure")

    monkeypatch.setattr(matches_repo, "create_leg", boom)

    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        create_match(db, X01_501, [TeamSpec((1,)), TeamSpec((2,))])

    assert counts(db) == {"matches": 1, "teams": 2, "team_members": 4, "legs": 1}
    assert get_match(db, doubles.match_id).id == doubles.match_id


# --- what it refuses ---------------------------------------------------------


def test_no_teams_is_rejected(db: sqlite3.Connection) -> None:
    with pytest.raises(InvalidMatchError, match="at least one team"), transaction(db):
        create_match(db, X01_501, [])
    assert count(db, "matches") == 0


def test_empty_team_is_rejected(db: sqlite3.Connection, players: list[int]) -> None:
    with pytest.raises(InvalidMatchError, match="team 1 has no players"), transaction(db):
        create_match(db, X01_501, [TeamSpec((players[0],)), TeamSpec(())])
    assert count(db, "matches") == 0


def test_a_player_cannot_be_on_two_teams(db: sqlite3.Connection, players: list[int]) -> None:
    """The primary key is (team_id, player_id), so the schema would allow this."""
    with pytest.raises(InvalidMatchError, match="is on team 0 and team 1"), transaction(db):
        create_match(db, X01_501, [TeamSpec((players[0],)), TeamSpec((players[0],))])
    assert count(db, "matches") == 0


def test_a_player_cannot_be_on_one_team_twice(db: sqlite3.Connection, players: list[int]) -> None:
    with pytest.raises(InvalidMatchError, match="is on team 0 and team 0"), transaction(db):
        create_match(db, X01_501, [TeamSpec((players[0], players[0]))])
    assert count(db, "matches") == 0


def test_unknown_player_is_rejected(db: sqlite3.Connection, players: list[int]) -> None:
    with pytest.raises(NotFoundError, match="no player with id 99"), transaction(db):
        create_match(db, X01_501, [TeamSpec((players[0],)), TeamSpec((99,))])
    assert count(db, "matches") == 0


# --- reading back ------------------------------------------------------------


def test_get_match_returns_the_config_that_was_written(
    db: sqlite3.Connection, doubles: CreatedMatch
) -> None:
    match = get_match(db, doubles.match_id)
    assert match.config == X01_501
    assert isinstance(match.config, GameConfig)
    assert match.created_at
    assert match.completed_at is None
    assert match.winner_team_id is None


def test_get_match_orders_teams_by_index(db: sqlite3.Connection, doubles: CreatedMatch) -> None:
    match = get_match(db, doubles.match_id)
    assert [team.team_index for team in match.teams] == [0, 1]
    assert [team.id for team in match.teams] == list(doubles.team_ids)


def test_get_missing_match_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no match with id 99"):
        get_match(db, 99)


def test_two_matches_do_not_share_teams(db: sqlite3.Connection, players: list[int]) -> None:
    with transaction(db):
        first = create_match(db, X01_501, [TeamSpec((players[0],)), TeamSpec((players[1],))])
    with transaction(db):
        second = create_match(db, CRICKET, [TeamSpec((players[0],)), TeamSpec((players[1],))])
    assert set(first.team_ids).isdisjoint(second.team_ids)
    assert len(get_match(db, second.match_id).teams) == 2
