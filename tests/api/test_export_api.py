"""The export endpoints over HTTP: media types, streaming, and the file.

The *contents* of the two CSVs are `tests/services/test_export_csv.py`'s
business and are not re-asserted here. What is only true over HTTP is asserted
here: that the bodies stream rather than buffer, that the connection a stream
runs on is closed when the stream ends, and that an abandoned download leaves no
temporary behind.
"""

import csv
import io
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from apifixtures import SCHEMA_VERSION, make_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from seed import build

from darts.api import export as route
from darts.config import Settings
from darts.db.durability import SIDECARS, read_only_uri
from darts.services import export as service
from darts.services import snapshot as snapshot_service
from darts.stats.queries import StatsFilter


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    resolved = make_settings(tmp_path)
    build(resolved.db_path).close()
    return resolved


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as started:
        yield started


def rows(body: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body)))


# --- what a client receives -------------------------------------------------


@pytest.mark.parametrize(
    ("path", "filename"),
    [("/api/export/darts.csv", "darts.csv"), ("/api/export/matches.csv", "matches.csv")],
)
def test_a_csv_export_is_served_as_a_named_utf8_attachment(
    client: TestClient, path: str, filename: str
) -> None:
    """A browser must save it as a file, not render it, and not guess the encoding."""
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    assert rows(response.text)


def test_the_darts_export_has_one_row_per_dart(client: TestClient, settings: Settings) -> None:
    with closing(sqlite3.connect(read_only_uri(settings.db_path), uri=True)) as conn:
        stored = conn.execute("SELECT count(*) FROM darts").fetchone()[0]

    body = rows(client.get("/api/export/darts.csv").text)
    assert len(body) == stored > 0
    assert list(body[0]) == list(service.DARTS_HEADER)


def test_the_inner_bull_survives_the_http_round_trip(client: TestClient) -> None:
    """#20's comment, over the wire: `is_double` alone would have lost this."""
    body = rows(client.get("/api/export/darts.csv").text)
    bulls = [row for row in body if row["label"] == "BULL"]
    assert bulls
    for row in bulls:
        assert (row["segment"], row["multiplier"], row["score"]) == ("25", "2", "50")
    assert all(row["score"] != "50" for row in body if row["label"].startswith("D"))


def test_the_matches_export_has_one_row_per_match(client: TestClient, settings: Settings) -> None:
    with closing(sqlite3.connect(read_only_uri(settings.db_path), uri=True)) as conn:
        stored = conn.execute("SELECT count(*) FROM matches").fetchone()[0]

    body = rows(client.get("/api/export/matches.csv").text)
    assert len(body) == stored > 0
    assert list(body[0]) == list(service.MATCHES_HEADER)


# --- the filters ------------------------------------------------------------


def test_the_exports_take_the_same_filters_the_stats_endpoints_do(
    client: TestClient,
) -> None:
    assert {
        row["match_id"] for row in rows(client.get("/api/export/darts.csv?match_id=2").text)
    } == {"2"}
    cricket = rows(client.get("/api/export/matches.csv?game_type=cricket").text)
    assert {row["game_type"] for row in cricket} == {"cricket"}
    assert rows(client.get("/api/export/darts.csv?since=2026-01-04T00:00:00Z").text)


@pytest.mark.parametrize(
    "query",
    ["match_id=0", "match_id=-1", "game_type=snooker", "variant=nonsense", "since=yesterday"],
)
def test_a_bad_filter_is_a_field_level_422_not_a_500(client: TestClient, query: str) -> None:
    """Validated during request parsing, so a typo never reaches a handler."""
    response = client.get(f"/api/export/darts.csv?{query}")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["detail"][0]["loc"]


def test_an_unknown_filter_is_refused_rather_than_silently_ignored(client: TestClient) -> None:
    """`?gametype=x01` must not be answered with unfiltered numbers."""
    assert client.get("/api/export/darts.csv?gametype=x01").status_code == 422


def test_min_darts_is_only_on_the_statistics_export(client: TestClient) -> None:
    """Neither CSV has a ranking to threshold, so the parameter is not theirs."""
    assert client.get("/api/export/stats.json?min_darts=50").status_code == 200
    assert client.get("/api/export/darts.csv?min_darts=50").status_code == 422


# --- stats.json -------------------------------------------------------------


def test_the_statistics_export_is_a_self_describing_document(client: TestClient) -> None:
    document = client.get("/api/export/stats.json").json()

    assert set(document) == {
        "generated_at",
        "schema_version",
        "filter",
        "leaderboard",
        "players",
    }
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["generated_at"].endswith("Z")
    assert document["filter"] == {
        "game_type": None,
        "variant": None,
        "since": None,
        "match_id": None,
    }


def test_the_export_leaderboard_withholds_nobody_by_default(client: TestClient) -> None:
    """Defaulting to 50 like the live leaderboard would ship an empty table."""
    document = client.get("/api/export/stats.json").json()
    assert document["leaderboard"]["min_darts"] == 0
    assert document["leaderboard"]["ranked_by"] == "three_dart_average"
    assert document["leaderboard"]["rows"]

    thresholded = client.get("/api/export/stats.json?min_darts=50").json()
    assert thresholded["leaderboard"]["min_darts"] == 50
    assert len(thresholded["leaderboard"]["rows"]) < len(document["leaderboard"]["rows"])


def test_every_player_gets_a_report_including_archived_ones(
    client: TestClient, settings: Settings
) -> None:
    """An export of the history that dropped a retired player would misreport it."""
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.execute("UPDATE players SET is_archived = 1 WHERE id = 1")
        conn.commit()
        total = conn.execute("SELECT count(*) FROM players").fetchone()[0]

    document = client.get("/api/export/stats.json").json()
    assert len(document["players"]) == total
    assert any(player["is_archived"] for player in document["players"])
    # ...while the leaderboard still leaves them off, as #19 decided.
    ranked = {row["player_id"] for row in document["leaderboard"]["rows"]}
    assert 1 not in ranked


def test_the_document_agrees_with_the_endpoint_it_is_built_from(client: TestClient) -> None:
    """Same numbers as `/api/stats/*`; the export is a repackaging, not a rewrite."""
    document = client.get("/api/export/stats.json").json()
    exported = {player["player_id"]: player for player in document["players"]}

    for player_id, player in exported.items():
        live = client.get(f"/api/stats/players/{player_id}").json()["player"]
        assert player == live

    live_board = client.get("/api/stats/leaderboard?min_darts=0").json()
    assert document["leaderboard"]["rows"] == live_board["rows"]


def test_the_filter_is_echoed_back_into_the_saved_file(client: TestClient) -> None:
    """A file read months later has to be able to say what it was narrowed to."""
    document = client.get("/api/export/stats.json?game_type=cricket&variant=quick").json()
    assert document["filter"]["game_type"] == "cricket"
    assert document["filter"]["variant"] == "quick"


# --- streaming --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/export/darts.csv", "/api/export/matches.csv"])
def test_a_csv_export_streams_rather_than_buffering(client: TestClient, path: str) -> None:
    """No `content-length`: the body is produced as it is sent, not sized up front."""
    with client.stream("GET", path) as response:
        assert response.status_code == 200
        assert "content-length" not in response.headers
        chunks = list(response.iter_lines())

    assert len(chunks) > 1
    assert chunks[0].startswith("match_id,")


def test_the_first_line_is_available_before_the_last_row_is_read(
    client: TestClient,
) -> None:
    """The header arrives from a generator that has not finished the table."""
    with client.stream("GET", "/api/export/darts.csv") as response:
        first = next(response.iter_lines())
        assert first == ",".join(service.DARTS_HEADER)


def test_the_connection_a_stream_ran_on_is_closed_afterwards(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason these routes do not use `deps.ConnectionDep`.

    The connection must outlive the handler -- a dependency's would not -- and it
    must not outlive the response, or every download would leak one.
    """
    opened: list[sqlite3.Connection] = []
    genuine = route.connection

    def record(*args: object, **kwargs: object) -> object:
        handle = genuine(*args, **kwargs)  # type: ignore[arg-type]

        class Recording:
            def __enter__(self) -> sqlite3.Connection:
                conn = handle.__enter__()
                opened.append(conn)
                return conn

            def __exit__(self, *exc: object) -> object:
                return handle.__exit__(*exc)  # type: ignore[arg-type]

        return Recording()

    monkeypatch.setattr(route, "connection", record)
    assert client.get("/api/export/darts.csv").status_code == 200

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


def test_abandoning_a_csv_download_closes_its_connection(settings: Settings) -> None:
    """Starlette closes the body generator on disconnect; the `with` block unwinds.

    Driven directly, because a `TestClient` cannot be disconnected mid-response
    and `GeneratorExit` at the `yield` is exactly what a disconnect produces.
    """
    opened: list[sqlite3.Connection] = []

    def produce(conn: sqlite3.Connection) -> Iterator[str]:
        opened.append(conn)
        yield from service.darts_csv(conn, StatsFilter())

    stream = route._streamed(settings, produce)
    assert next(stream).startswith("match_id,")
    assert opened and opened[0].in_transaction

    stream.close()

    # Closed, not merely finished: `in_transaction` itself raises on a closed
    # connection, which is the strongest statement available here. That the
    # read transaction is rolled back rather than left open is asserted at the
    # service layer, in tests/services/test_export_csv.py.
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


# --- the database download --------------------------------------------------


def test_the_downloaded_database_is_a_fresh_self_contained_copy(
    client: TestClient, tmp_path: Path
) -> None:
    """#20's headline criterion, as the properties DB Browser and DuckDB need."""
    response = client.get("/api/export/db")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.sqlite3"
    assert response.headers["content-disposition"] == 'attachment; filename="darts.db"'
    assert response.content.startswith(b"SQLite format 3\x00")

    downloaded = tmp_path / "downloaded.db"
    downloaded.write_bytes(response.content)
    for suffix in SIDECARS:
        assert not downloaded.with_name(downloaded.name + suffix).exists()

    with closing(sqlite3.connect(read_only_uri(downloaded), uri=True)) as conn:
        assert [row[0] for row in conn.execute("PRAGMA integrity_check")] == ["ok"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM darts").fetchone()[0] > 0
        assert conn.execute("SELECT count(*) FROM v_darts").fetchone()[0] > 0


def test_the_download_contains_play_the_live_wal_had_not_checkpointed(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    """Serving the live file would have handed back a database missing this row.

    The point of copying through `Connection.backup()` rather than sending
    `darts.db`: a commit that is still only in the `-wal` sidecar is in the
    copy, and would not have been in the bare file.
    """
    wal = settings.db_path.with_name(settings.db_path.name + "-wal")
    downloaded = tmp_path / "downloaded.db"
    # The connection stays open across the download: closing the last one
    # checkpoints and removes the WAL, which would erase the very condition
    # being tested.
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("INSERT INTO players(display_name) VALUES ('Gus')")
        conn.commit()
        assert wal.exists() and wal.stat().st_size > 0, "the commit is only in the sidecar"

        downloaded.write_bytes(client.get("/api/export/db").content)

    with closing(sqlite3.connect(read_only_uri(downloaded), uri=True)) as conn:
        names = {row[0] for row in conn.execute("SELECT display_name FROM players")}
    assert "Gus" in names


def test_the_download_does_not_touch_the_published_snapshot(
    client: TestClient, settings: Settings
) -> None:
    """A download must never be able to leave the shared file half-written."""
    client.post("/api/admin/snapshot")
    published = (settings.snapshot_dir / "darts-latest.db").read_bytes()

    assert client.get("/api/export/db").status_code == 200

    assert (settings.snapshot_dir / "darts-latest.db").read_bytes() == published


def test_the_download_leaves_no_temporary_behind(client: TestClient, settings: Settings) -> None:
    for _ in range(3):
        assert client.get("/api/export/db").status_code == 200

    leftovers = [path.name for path in settings.snapshot_dir.iterdir()]
    assert leftovers == []


def test_an_abandoned_download_still_deletes_its_temporary(
    settings: Settings, tmp_path: Path
) -> None:
    """What Starlette does to the body generator when the client goes away.

    Driven directly rather than through the client because there is no way to
    disconnect a `TestClient` mid-response -- and `GeneratorExit` at the `yield`
    is exactly what a disconnect produces.
    """
    copy = snapshot_service.copy_of(settings.db_path, settings.snapshot_dir)
    assert copy.is_file()

    body = route.stream_copy(copy)
    assert next(body).startswith(b"SQLite format 3\x00")
    assert copy.is_file(), "still there mid-download"

    body.close()

    assert not copy.exists(), "an abandoned download must not leave the card filling up"


def test_a_completed_download_streams_the_whole_file_and_then_deletes_it(
    settings: Settings,
) -> None:
    copy = snapshot_service.copy_of(settings.db_path, settings.snapshot_dir)
    expected = copy.read_bytes()

    assert b"".join(route.stream_copy(copy)) == expected

    assert not copy.exists()
