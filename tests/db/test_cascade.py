import sqlite3

import pytest


@pytest.mark.parametrize("parent", ["matches", "legs", "visits", "darts"])
def test_delete_cascades_to_effects_and_each_recipient(
    populated: sqlite3.Connection, parent: str
) -> None:
    db = populated
    db.execute("""INSERT INTO darts(id,visit_id,leg_id,team_id,player_id,seq_in_leg,dart_index,
        segment,multiplier,counted,client_dart_id)
        VALUES(10000,1000,100,10,1,0,0,20,3,1,'first')""")
    db.execute("INSERT INTO cricket_dart_effects VALUES (10000,20,0,3,0)")
    db.executemany("INSERT INTO cricket_point_events VALUES (10000,100,1,?,60)", [(20,), (30,)])
    for team_id in (10, 20, 30):
        db.execute("INSERT INTO leg_team_state VALUES (100,?,1,NULL,NULL,0,0)", (team_id,))
        db.execute("INSERT INTO cricket_leg_state VALUES (100,?,20,0)", (team_id,))
    # Completed winner references must not prevent the cascade through teams.
    db.execute("UPDATE matches SET winner_team_id=10 WHERE id=1")
    db.execute("UPDATE legs SET winner_team_id=10 WHERE id=100")
    ids = {"matches": 1, "legs": 100, "visits": 1000, "darts": 10000}
    db.execute(f"DELETE FROM {parent} WHERE id=?", (ids[parent],))
    empty = ["darts", "cricket_dart_effects", "cricket_point_events"]
    if parent in ("matches", "legs"):
        empty += ["legs", "visits", "leg_team_state", "cricket_leg_state"]
    if parent == "matches":
        empty += ["matches", "teams", "team_members"]
    for table in empty:
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert db.execute("SELECT count(*) FROM players").fetchone()[0] == 3
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_cache_deletion_does_not_delete_source_darts(populated: sqlite3.Connection) -> None:
    db = populated
    db.execute("""INSERT INTO darts(visit_id,leg_id,team_id,player_id,seq_in_leg,dart_index,
        segment,multiplier,counted,client_dart_id) VALUES(1000,100,10,1,0,0,20,3,1,'first')""")
    db.execute("INSERT INTO leg_team_state VALUES (100,10,1,NULL,NULL,1,0)")
    db.execute("INSERT INTO cricket_leg_state VALUES (100,10,20,3)")
    db.execute("DELETE FROM leg_team_state")
    assert db.execute("SELECT count(*) FROM cricket_leg_state").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM darts").fetchone()[0] == 1


def test_archive_preserves_membership_and_player_cannot_be_deleted(
    populated: sqlite3.Connection,
) -> None:
    populated.execute("UPDATE players SET is_archived=1 WHERE id=1")
    assert (
        populated.execute("SELECT count(*) FROM team_members WHERE player_id=1").fetchone()[0] == 1
    )
    with pytest.raises(sqlite3.IntegrityError):
        populated.execute("DELETE FROM players WHERE id=1")
