"""Players: the round trip, the name rule, and archiving that keeps everything."""

import sqlite3

import pytest
from repofixtures import X01_501, count, open_visit

from darts.db.connection import transaction
from darts.repo.darts import NewDart, append_dart, darts_for_leg
from darts.repo.errors import DuplicateNameError, InvalidMatchError, NotFoundError
from darts.repo.matches import TeamSpec, create_match, get_match
from darts.repo.players import (
    ACCENT_COUNT,
    ACCENTS,
    SHORT_NAME_MAX,
    archive_player,
    create_player,
    get_player,
    list_players,
    next_accent_index,
    unarchive_player,
    update_player,
)


def test_create_and_read_back(db: sqlite3.Connection) -> None:
    created = create_player(db, "Ana")
    assert created.display_name == "Ana"
    assert created.is_archived is False
    assert created.created_at
    assert get_player(db, created.id) == created


def test_id_is_assigned_by_sqlite(db: sqlite3.Connection) -> None:
    first = create_player(db, "Ana")
    second = create_player(db, "Ben")
    assert second.id > first.id


def test_surrounding_whitespace_is_stripped(db: sqlite3.Connection) -> None:
    assert create_player(db, "  Ana  ").display_name == "Ana"


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_blank_names_are_rejected(db: sqlite3.Connection, name: str) -> None:
    with pytest.raises(ValueError, match="blank"):
        create_player(db, name)
    assert count(db, "players") == 0


def test_get_missing_player_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError, match="no player with id 99"):
        get_player(db, 99)


# --- the name rule -----------------------------------------------------------


@pytest.mark.parametrize("duplicate", ["Ana", "ana", "ANA", "  aNa  "])
def test_duplicate_active_name_is_rejected(db: sqlite3.Connection, duplicate: str) -> None:
    create_player(db, "Ana")
    with pytest.raises(DuplicateNameError):
        create_player(db, duplicate)
    assert count(db, "players") == 1


def test_different_names_are_fine(db: sqlite3.Connection) -> None:
    create_player(db, "Ana")
    create_player(db, "Anna")
    assert count(db, "players") == 2


def test_archiving_releases_the_name(db: sqlite3.Connection) -> None:
    """The decision that archiving frees a name, stated as a test."""
    retired = create_player(db, "Ana")
    archive_player(db, retired.id)
    fresh = create_player(db, "Ana")
    assert fresh.id != retired.id
    assert count(db, "players") == 2


def test_unarchiving_into_a_taken_name_is_rejected(db: sqlite3.Connection) -> None:
    """Restoring Ana when somebody else is now Ana would give the picker two."""
    retired = create_player(db, "Ana")
    archive_player(db, retired.id)
    create_player(db, "ana")
    with pytest.raises(DuplicateNameError):
        unarchive_player(db, retired.id)
    assert get_player(db, retired.id).is_archived is True


# --- update ------------------------------------------------------------------


def test_update_renames(db: sqlite3.Connection) -> None:
    player = create_player(db, "Ana")
    renamed = update_player(db, player.id, display_name="Ana B.")
    assert renamed.display_name == "Ana B."
    assert renamed.id == player.id
    assert renamed.created_at == player.created_at


def test_update_to_own_name_in_another_case_is_allowed(db: sqlite3.Connection) -> None:
    """Fixing your own capitalisation must not collide with yourself."""
    player = create_player(db, "ana")
    assert update_player(db, player.id, display_name="Ana").display_name == "Ana"


def test_update_to_another_active_name_is_rejected(db: sqlite3.Connection) -> None:
    create_player(db, "Ana")
    ben = create_player(db, "Ben")
    with pytest.raises(DuplicateNameError):
        update_player(db, ben.id, display_name="ANA")
    assert get_player(db, ben.id).display_name == "Ben"


def test_update_missing_player_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        update_player(db, 99, display_name="Ana")


def test_update_rejects_a_blank_name(db: sqlite3.Connection) -> None:
    player = create_player(db, "Ana")
    with pytest.raises(ValueError, match="blank"):
        update_player(db, player.id, display_name="  ")
    assert get_player(db, player.id).display_name == "Ana"


# --- listing -----------------------------------------------------------------


def test_list_hides_archived_by_default(db: sqlite3.Connection) -> None:
    ana = create_player(db, "Ana")
    create_player(db, "Ben")
    archive_player(db, ana.id)
    assert [p.display_name for p in list_players(db)] == ["Ben"]


def test_list_can_include_archived(db: sqlite3.Connection) -> None:
    ana = create_player(db, "Ana")
    create_player(db, "Ben")
    archive_player(db, ana.id)
    listed = list_players(db, include_archived=True)
    assert [p.display_name for p in listed] == ["Ana", "Ben"]
    assert [p.is_archived for p in listed] == [True, False]


def test_list_orders_by_name_case_insensitively(db: sqlite3.Connection) -> None:
    for name in ("cal", "Ana", "ben"):
        create_player(db, name)
    assert [p.display_name for p in list_players(db)] == ["Ana", "ben", "cal"]


def test_list_is_empty_on_a_fresh_database(db: sqlite3.Connection) -> None:
    assert list_players(db) == []


# --- archiving ---------------------------------------------------------------


def test_archive_and_unarchive_round_trip(db: sqlite3.Connection) -> None:
    player = create_player(db, "Ana")
    assert archive_player(db, player.id).is_archived is True
    assert unarchive_player(db, player.id).is_archived is False
    assert list_players(db) == [get_player(db, player.id)]


def test_archiving_twice_is_a_no_op(db: sqlite3.Connection) -> None:
    player = create_player(db, "Ana")
    archive_player(db, player.id)
    assert archive_player(db, player.id).is_archived is True


def test_archive_missing_player_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        archive_player(db, 99)


def test_unarchive_missing_player_raises(db: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        unarchive_player(db, 99)


def test_archived_player_cannot_join_a_new_match(db: sqlite3.Connection) -> None:
    """Archiving takes somebody out of the pickers, so picking them is stale."""
    ana = create_player(db, "Ana")
    ben = create_player(db, "Ben")
    archive_player(db, ana.id)
    with pytest.raises(InvalidMatchError, match="archived"), transaction(db):
        create_match(db, X01_501, [TeamSpec((ana.id,)), TeamSpec((ben.id,))])
    assert count(db, "matches") == 0


def test_archiving_preserves_every_dart_and_history(db: sqlite3.Connection) -> None:
    """The acceptance criterion: archive keeps the darts and the match detail."""
    ana = create_player(db, "Ana")
    ben = create_player(db, "Ben")
    with transaction(db):
        created = create_match(db, X01_501, [TeamSpec((ana.id,)), TeamSpec((ben.id,))])
        visit = open_visit(db, created, player_id=ana.id)
        for index in range(3):
            append_dart(
                db,
                NewDart(
                    visit_id=visit,
                    leg_id=created.leg_id,
                    team_id=created.team_ids[0],
                    player_id=ana.id,
                    seq_in_leg=index,
                    dart_index=index,
                    segment=20,
                    multiplier=3,
                    counted=True,
                    client_dart_id=f"dart-{index}",
                ),
            )

    before = darts_for_leg(db, created.leg_id)
    archive_player(db, ana.id)

    assert darts_for_leg(db, created.leg_id) == before
    assert len(before) == 3
    assert {dart.player_id for dart in before} == {ana.id}

    # ...and she is still named in the historical match detail, flagged as gone.
    match = get_match(db, created.match_id)
    member = match.teams[0].members[0]
    assert member.player_id == ana.id
    assert member.display_name == "Ana"
    assert member.is_archived is True

    # ...while having vanished from the picker.
    assert [p.display_name for p in list_players(db)] == ["Ben"]


# --- colour and short name ---------------------------------------------------


def test_a_new_player_is_given_a_colour_nobody_is_using(db: sqlite3.Connection) -> None:
    assert next_accent_index(db) == 1
    ana = create_player(db, "Ana")
    assert ana.accent_index == 1
    assert create_player(db, "Ben").accent_index == 2


def test_the_first_eight_players_get_the_whole_palette(db: sqlite3.Connection) -> None:
    created = [create_player(db, f"Player {i}") for i in range(ACCENT_COUNT)]
    assert sorted(p.accent_index or 0 for p in created) == list(ACCENTS)


def test_a_ninth_player_reuses_the_least_held_colour(db: sqlite3.Connection) -> None:
    """Eight colours cannot cover nine people, so the clash is chosen, not stumbled into."""
    for i in range(ACCENT_COUNT):
        create_player(db, f"Player {i}")
    assert create_player(db, "Ninth").accent_index == 1
    assert create_player(db, "Tenth").accent_index == 2


def test_archiving_returns_a_colour_to_the_pool(db: sqlite3.Connection) -> None:
    ana = create_player(db, "Ana")
    assert ana.accent_index == 1
    archive_player(db, ana.id)
    assert create_player(db, "Ben").accent_index == 1


def test_an_explicitly_chosen_colour_is_honoured_even_if_taken(db: sqlite3.Connection) -> None:
    create_player(db, "Ana")
    assert create_player(db, "Ben", accent_index=1).accent_index == 1


@pytest.mark.parametrize("accent", [0, -1, ACCENT_COUNT + 1])
def test_a_colour_outside_the_palette_is_rejected(db: sqlite3.Connection, accent: int) -> None:
    with pytest.raises(ValueError, match="accent_index"):
        create_player(db, "Ana", accent_index=accent)
    assert count(db, "players") == 0


def test_short_name_is_stored_trimmed(db: sqlite3.Connection) -> None:
    assert create_player(db, "Ana", short_name="  A.B  ").short_name == "A.B"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_short_name_means_there_is_none(db: sqlite3.Connection, blank: str | None) -> None:
    assert create_player(db, "Ana", short_name=blank).short_name is None


def test_an_over_long_short_name_is_refused_rather_than_truncated(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="short_name"):
        create_player(db, "Ana", short_name="A" * (SHORT_NAME_MAX + 1))
    assert count(db, "players") == 0


def test_update_leaves_colour_and_short_name_alone_when_not_mentioned(
    db: sqlite3.Connection,
) -> None:
    """A rename is a rename. Omitting a field is not a request to erase it."""
    ana = create_player(db, "Ana", short_name="A.B", accent_index=3)
    renamed = update_player(db, ana.id, display_name="Ana B.")
    assert (renamed.short_name, renamed.accent_index) == ("A.B", 3)


def test_update_sets_and_clears_each_field_independently(db: sqlite3.Connection) -> None:
    ana = create_player(db, "Ana", short_name="A.B", accent_index=3)
    edited = update_player(db, ana.id, display_name="Ana", short_name="AB", accent_index=5)
    assert (edited.short_name, edited.accent_index) == ("AB", 5)

    cleared = update_player(db, ana.id, display_name="Ana", short_name=None, accent_index=None)
    assert (cleared.short_name, cleared.accent_index) == (None, None)


def test_update_rejects_a_colour_outside_the_palette(db: sqlite3.Connection) -> None:
    ana = create_player(db, "Ana", accent_index=2)
    with pytest.raises(ValueError, match="accent_index"):
        update_player(db, ana.id, display_name="Ana", accent_index=99)
    assert get_player(db, ana.id).accent_index == 2


def test_a_refused_rename_leaves_the_colour_untouched(db: sqlite3.Connection) -> None:
    """The duplicate check runs before the write, so nothing is half applied."""
    create_player(db, "Ana")
    ben = create_player(db, "Ben", accent_index=4)
    with pytest.raises(DuplicateNameError):
        update_player(db, ben.id, display_name="ana", accent_index=7)
    assert get_player(db, ben.id) == ben
