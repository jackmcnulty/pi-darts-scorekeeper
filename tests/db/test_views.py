"""The replaceable query surface: installation, idempotence and what it reports."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from seed import build

from darts.db.backup import create
from darts.db.connection import connection
from darts.db.migrate import migrate
from darts.db.recovery import DatabaseState, check_and_recover
from darts.db.views import VIEWS, ViewError, install_views, view_names
from darts.tools.migrate import main

EXPECTED = ("v_darts", "v_visits")


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    """The whole fixture dataset once, read-only for every test that wants it."""
    conn = build(tmp_path_factory.mktemp("seeded") / "seeded.db")
    yield conn
    conn.close()


def installed(conn: sqlite3.Connection) -> list[str]:
    """View names straight from the schema, duplicates included if any exist."""
    return [
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'view'")
    ]


def test_installing_twice_leaves_exactly_one_copy_of_each_view(db: sqlite3.Connection) -> None:
    assert install_views(db) == EXPECTED
    assert install_views(db) == EXPECTED
    assert sorted(installed(db)) == list(EXPECTED)


def test_a_new_view_needs_no_migration_and_does_not_move_user_version(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    install_views(db)
    ledger = db.execute("SELECT version, name, sha256 FROM schema_migrations").fetchall()
    version = db.execute("PRAGMA user_version").fetchone()[0]

    extended = tmp_path / "views.sql"
    extended.write_text(VIEWS.read_text() + "\nCREATE VIEW v_extra AS SELECT 1 AS one;\n")
    assert install_views(db, extended) == ("v_darts", "v_extra", "v_visits")

    assert db.execute("PRAGMA user_version").fetchone()[0] == version
    assert db.execute("SELECT version, name, sha256 FROM schema_migrations").fetchall() == ledger
    assert db.execute("SELECT one FROM v_extra").fetchone()[0] == 1


def test_a_view_dropped_from_the_file_disappears(db: sqlite3.Connection, tmp_path: Path) -> None:
    """views.sql is the whole set, so removing one is enough to retire it."""
    extended = tmp_path / "views.sql"
    extended.write_text(VIEWS.read_text() + "\nCREATE VIEW v_extra AS SELECT 1 AS one;\n")
    install_views(db, extended)
    assert install_views(db) == EXPECTED


def test_a_changed_definition_replaces_the_old_one(db: sqlite3.Connection, tmp_path: Path) -> None:
    """Dropping unconditionally is what makes this true; IF NOT EXISTS would not."""
    first = tmp_path / "first.sql"
    first.write_text("CREATE VIEW v_thing AS SELECT 1 AS value;\n")
    install_views(db, first)
    assert db.execute("SELECT value FROM v_thing").fetchone()[0] == 1

    second = tmp_path / "second.sql"
    second.write_text("CREATE VIEW v_thing AS SELECT 2 AS value;\n")
    install_views(db, second)
    assert db.execute("SELECT value FROM v_thing").fetchone()[0] == 2


def test_views_are_not_tables(db: sqlite3.Connection) -> None:
    """Backup manifests and the documentation test both filter on type='table'."""
    before = db.execute("SELECT count(*) FROM sqlite_schema WHERE type = 'table'").fetchone()[0]
    install_views(db)
    assert db.execute("SELECT count(*) FROM sqlite_schema WHERE type = 'table'").fetchone()[0] == (
        before
    )
    assert view_names(db) == EXPECTED


def test_views_do_not_reach_the_backup_manifest(tmp_path: Path) -> None:
    """_describe counts rows per table; a view must not become a phantom entry."""
    database = tmp_path / "darts.db"
    with connection(database) as conn:
        migrate(conn)
        install_views(conn)
    counts = create(database).manifest["row_counts"]
    assert set(counts) & set(EXPECTED) == set()
    assert "darts" in counts and "visits" in counts


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE sneaky(id INTEGER PRIMARY KEY);",
        "DROP TABLE darts;",
        "CREATE VIEW v_ok AS SELECT 1 AS one;\nDELETE FROM darts;",
    ],
)
def test_only_create_view_is_accepted(db: sqlite3.Connection, tmp_path: Path, sql: str) -> None:
    path = tmp_path / "bad.sql"
    path.write_text(sql)
    with pytest.raises(ViewError, match="only CREATE VIEW"):
        install_views(db, path)


def test_an_unterminated_statement_is_rejected(db: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "truncated.sql"
    path.write_text("CREATE VIEW v_ok AS SELECT 1 AS one")
    with pytest.raises(ViewError, match="unterminated"):
        install_views(db, path)


def test_a_file_with_no_views_is_rejected(db: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "comments.sql"
    path.write_text("-- nothing to see here\n\n/* not even here */\n")
    with pytest.raises(ViewError, match="no views"):
        install_views(db, path)


def test_a_rejected_file_leaves_the_previous_views_in_place(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    install_views(db)
    path = tmp_path / "bad.sql"
    path.write_text("CREATE VIEW v_ok AS SELECT 1 AS one;\nCREATE TABLE sneaky(id INTEGER);")
    with pytest.raises(ViewError):
        install_views(db, path)
    assert view_names(db) == EXPECTED


def test_v_darts_row_count_equals_the_darts_table(seeded: sqlite3.Connection) -> None:
    """No join in v_darts may drop or duplicate a dart."""
    darts = seeded.execute("SELECT count(*) FROM darts").fetchone()[0]
    assert darts > 0
    assert seeded.execute("SELECT count(*) FROM v_darts").fetchone()[0] == darts
    assert seeded.execute("SELECT count(DISTINCT dart_id) FROM v_darts").fetchone()[0] == darts


def test_v_visits_row_count_equals_the_visits_table(seeded: sqlite3.Connection) -> None:
    visits = seeded.execute("SELECT count(*) FROM visits").fetchone()[0]
    assert visits > 0
    assert seeded.execute("SELECT count(*) FROM v_visits").fetchone()[0] == visits


def test_a_busted_visit_scores_nothing_while_its_darts_survive(
    seeded: sqlite3.Connection,
) -> None:
    busts = seeded.execute(
        "SELECT visit_id, is_bust, total_scored, darts_thrown FROM v_visits WHERE is_bust = 1"
    ).fetchall()
    assert busts, "the fixture must contain at least one bust"
    for visit in busts:
        assert visit["total_scored"] == 0
        assert visit["darts_thrown"] > 0
        darts = seeded.execute(
            "SELECT counted, caused_bust, score FROM v_darts "
            "WHERE visit_id = ? ORDER BY dart_index",
            (visit["visit_id"],),
        ).fetchall()
        assert len(darts) == visit["darts_thrown"]
        # Every dart of the visit is uncounted, including the ones that scored
        # perfectly well before the offending one, which is the whole bust rule.
        assert [d["counted"] for d in darts] == [0] * len(darts)
        assert sum(d["caused_bust"] for d in darts) == 1
        assert any(d["score"] > 0 for d in darts)


def test_a_visit_with_no_darts_still_appears(populated: sqlite3.Connection) -> None:
    install_views(populated)
    row = populated.execute("SELECT darts_thrown, total_scored FROM v_visits").fetchone()
    assert (row["darts_thrown"], row["total_scored"]) == (0, 0)


def test_v_visits_totals_agree_with_the_darts_underneath(seeded: sqlite3.Connection) -> None:
    mismatched = seeded.execute(
        """SELECT v.visit_id FROM v_visits v
           WHERE v.game_type = 'x01' AND v.total_scored != (
               SELECT coalesce(sum(d.score * d.counted), 0)
               FROM v_darts d WHERE d.visit_id = v.visit_id)"""
    ).fetchall()
    assert mismatched == []


def test_v_darts_denormalises_the_match_configuration(seeded: sqlite3.Connection) -> None:
    """A stats query reads game type, rules and identities without another join."""
    row = seeded.execute(
        "SELECT * FROM v_darts WHERE game_type = 'x01' AND counted = 1 ORDER BY dart_id"
    ).fetchone()
    for column in (
        "game_type",
        "start_score",
        "in_rule",
        "out_rule",
        "best_of",
        "player_name",
        "team_index",
        "leg_index",
        "match_id",
    ):
        assert row[column] is not None, column

    cricket = seeded.execute(
        "SELECT variant, cricket_target, cricket_counted_marks FROM v_darts "
        "WHERE game_type = 'cricket' AND cricket_target IS NOT NULL ORDER BY dart_id"
    ).fetchone()
    assert cricket["variant"] in ("standard", "cutthroat", "quick")
    assert cricket["cricket_counted_marks"] >= 0


def test_the_migrate_cli_installs_views(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "cli.db"
    assert main([str(path)]) == 0
    assert capsys.readouterr().out == "schema version 1; applied 1 migration(s); 2 view(s)\n"
    with connection(path) as conn:
        assert view_names(conn) == EXPECTED

    # A second run applies nothing but still rebuilds the surface.
    with connection(path) as conn:
        conn.execute("DROP VIEW v_darts")
    assert main([str(path)]) == 0
    assert capsys.readouterr().out == "schema version 1; applied 0 migration(s); 2 view(s)\n"
    with connection(path) as conn:
        assert view_names(conn) == EXPECTED


def test_boot_recovery_installs_views_on_a_new_database(tmp_path: Path) -> None:
    database = tmp_path / "darts.db"
    assert check_and_recover(database).state is DatabaseState.HEALTHY
    with connection(database) as conn:
        assert view_names(conn) == EXPECTED


def test_a_restored_database_comes_back_with_its_views(tmp_path: Path) -> None:
    """A backup predating a view is still serviceable the moment recovery returns."""
    database = tmp_path / "darts.db"
    check_and_recover(database)
    create(database)

    # The backup now holds the views; drop them to stand in for one taken by an
    # older build, and confirm the boot path puts them back either way.
    with connection(database) as conn:
        for name in view_names(conn):
            conn.execute(f"DROP VIEW {name}")
        assert view_names(conn) == ()

    status = check_and_recover(database)
    assert status.state is DatabaseState.HEALTHY
    with connection(database) as conn:
        assert view_names(conn) == EXPECTED
