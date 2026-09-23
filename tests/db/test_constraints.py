import sqlite3

import pytest

from darts.engine.throws import ALL_THROWS, Throw


def insert_dart(db: sqlite3.Connection, **changes: int | str) -> None:
    values: dict[str, int | str] = {
        "visit_id": 1000,
        "leg_id": 100,
        "team_id": 10,
        "player_id": 1,
        "seq_in_leg": 0,
        "dart_index": 0,
        "segment": 20,
        "multiplier": 3,
        "counted": 1,
        "caused_bust": 0,
        "was_checkout_attempt": 0,
        "client_dart_id": "dart-1",
    }
    values.update(changes)
    db.execute(
        f"INSERT INTO darts ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
        tuple(values.values()),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"segment": -1},
        {"segment": 21},
        {"segment": 24},
        {"segment": 26},
        {"multiplier": -1},
        {"multiplier": 4},
        {"multiplier": 5},
        {"segment": 0, "multiplier": 1},
        {"segment": 0, "multiplier": 2},
        {"segment": 0, "multiplier": 3},
        {"segment": 20, "multiplier": 0},
        {"segment": 25, "multiplier": 0},
        {"segment": 25, "multiplier": 3},
        {"dart_index": -1},
        {"dart_index": 3},
        {"seq_in_leg": -1},
        {"counted": -1},
        {"counted": 2},
        {"caused_bust": -1},
        {"caused_bust": 2},
        {"was_checkout_attempt": -1},
        {"was_checkout_attempt": 2},
        {"client_dart_id": ""},
        {"caused_bust": 1, "counted": 1},
    ],
)
def test_illegal_darts_fail_sqlite_check(
    populated: sqlite3.Connection, changes: dict[str, int | str]
) -> None:
    # Require CHECK in the message: a foreign-key/unique failure is not evidence.
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        insert_dart(populated, **changes)


@pytest.mark.parametrize("throw", sorted(ALL_THROWS, key=lambda t: t.label))
def test_all_63_engine_throws_are_accepted(populated: sqlite3.Connection, throw: Throw) -> None:
    insert_dart(populated, segment=throw.segment, multiplier=throw.multiplier)


def test_duplicate_client_id_and_sequence_rejected(populated: sqlite3.Connection) -> None:
    insert_dart(populated)
    with pytest.raises(sqlite3.IntegrityError, match="client_dart_id"):
        insert_dart(populated, seq_in_leg=1, dart_index=1)
    with pytest.raises(sqlite3.IntegrityError):
        insert_dart(populated, client_dart_id="new", dart_index=1)
    with pytest.raises(sqlite3.IntegrityError):
        insert_dart(populated, client_dart_id="new", seq_in_leg=1)


def test_player_must_be_visit_thrower(populated: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert_dart(populated, player_id=2)


def test_strict_integer_columns(populated: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        populated.execute("""INSERT INTO darts(visit_id,leg_id,team_id,player_id,seq_in_leg,
            dart_index,segment,multiplier,counted,client_dart_id)
            VALUES (1000,100,10,1,0,0,1.5,1,1,'fraction')""")


def test_bust_rows_are_preserved(populated: sqlite3.Connection) -> None:
    insert_dart(populated, counted=0, caused_bust=1)
    populated.execute("UPDATE visits SET is_bust=1, is_complete=1 WHERE id=1000")
    assert populated.execute("SELECT count(*) FROM darts").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        populated.execute("UPDATE visits SET score_after=20 WHERE id=1000")


def test_cross_match_links_are_rejected(populated: sqlite3.Connection) -> None:
    populated.execute("""INSERT INTO matches(id,config_json,game_type,variant,best_of)
                         VALUES(2,'{}','cricket','quick',1)""")
    populated.execute("INSERT INTO teams VALUES (40,2,0,NULL,1)")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        populated.execute("UPDATE legs SET starting_team_id=40 WHERE id=100")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        populated.execute("UPDATE matches SET winner_team_id=40 WHERE id=1")
    insert_dart(populated)
    dart_id = populated.execute("SELECT id FROM darts").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        populated.execute("INSERT INTO cricket_point_events VALUES (?,100,1,40,60)", (dart_id,))


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE matches SET best_of=2",
        "UPDATE matches SET best_of=0",
        "UPDATE matches SET variant=NULL",
        "UPDATE matches SET out_rule='double'",
        "UPDATE matches SET config_json='broken'",
        "UPDATE matches SET config_json='[]'",
        "UPDATE team_members SET member_index=-1",
        "UPDATE players SET is_archived=2",
    ],
)
def test_other_schema_checks(populated: sqlite3.Connection, sql: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        populated.execute(sql)


def test_partial_indexes_and_query_plans(populated: sqlite3.Connection) -> None:
    plans = populated.execute("""EXPLAIN QUERY PLAN SELECT segment,multiplier FROM darts
                               WHERE player_id=1 AND counted=1""").fetchall()
    assert any("darts_counted_player" in row[3] for row in plans)
    plans = populated.execute("""EXPLAIN QUERY PLAN SELECT * FROM darts
                               WHERE player_id=1 AND was_checkout_attempt=1""").fetchall()
    assert any("darts_checkout_player" in row[3] for row in plans)


@pytest.mark.parametrize("in_rule", ["straight", "double", "master"])
@pytest.mark.parametrize("out_rule", ["straight", "double", "master"])
def test_x01_config_columns_and_solo_team(
    db: sqlite3.Connection, in_rule: str, out_rule: str
) -> None:
    db.execute(
        """INSERT INTO matches(id,config_json,game_type,start_score,in_rule,out_rule,best_of)
        VALUES (1,'{}','x01',501,?,?,5)""",
        (in_rule, out_rule),
    )
    db.execute("INSERT INTO players(id,display_name) VALUES (1,'solo')")
    db.execute("INSERT INTO teams(id,match_id,team_index,is_solo) VALUES (10,1,0,1)")
    db.execute("INSERT INTO team_members VALUES (10,1,0)")
    assert db.execute("SELECT count(*) FROM team_members WHERE team_id=10").fetchone()[0] == 1
    for update in (
        "start_score=NULL",
        "start_score=0",
        "in_rule=NULL",
        "out_rule='invalid'",
        "variant='standard'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute(f"UPDATE matches SET {update}")
