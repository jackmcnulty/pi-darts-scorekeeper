"""The three statistics endpoints over HTTP.

The context-managed client is deliberate, as everywhere in `tests/api`: a bare
`TestClient(app)` never runs the lifespan, so the boot check never installs the
views and every one of these queries would fail against a database nothing had
prepared.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from apifixtures import make_settings
from fastapi.testclient import TestClient
from seed import build

from darts.api.main import create_app
from darts.api.stats import DEFAULT_MIN_DARTS


@pytest.fixture
def seeded_client(tmp_path: Path) -> Iterator[TestClient]:
    """An app serving the #13 seed, with its lifespan run."""
    settings = make_settings(tmp_path)
    build(settings.db_path).close()
    with TestClient(create_app(settings)) as client:
        yield client


def player(client: TestClient, player_id: int, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/stats/players/{player_id}", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_a_player_report_carries_the_filter_it_applied(seeded_client: TestClient) -> None:
    """#27 labels a chart with this rather than re-reading the address bar."""
    body = player(seeded_client, 1)
    assert body["filter"] == {
        "game_type": None,
        "variant": None,
        "since": None,
        "match_id": None,
    }
    assert body["player"]["display_name"] == "Ana"
    assert body["player"]["x01"]["best_checkout"] == 121
    assert body["player"]["cricket"]["marks_per_round"] == 8.0


def test_the_echoed_since_is_the_text_the_query_compared(seeded_client: TestClient) -> None:
    """Sent as an ISO instant, echoed in the format the rows are stored in."""
    body = player(seeded_client, 1, since="2026-01-03T00:00:00Z")
    assert body["filter"]["since"] == "2026-01-03T00:00:00.000Z"


def test_filters_compose_over_http(seeded_client: TestClient) -> None:
    # The seed creates match N on day N, so this boundary drops match 1 (Ana's
    # solo 301) and keeps match 2 (the 2v2), while cricket is dropped by the
    # game type. Neither filter alone does both.
    both = player(seeded_client, 1, game_type="x01", since="2026-01-03T00:00:00Z")
    by_type = player(seeded_client, 1, game_type="x01")
    by_date = player(seeded_client, 1, since="2026-01-03T00:00:00Z")
    assert both["filter"] == {
        "game_type": "x01",
        "variant": None,
        "since": "2026-01-03T00:00:00.000Z",
        "match_id": None,
    }
    assert both["player"]["cricket"]["darts_thrown"] == 0
    assert by_date["player"]["cricket"]["darts_thrown"] > 0
    assert 0 < both["player"]["darts_thrown"] < by_type["player"]["darts_thrown"]
    assert both["player"]["darts_thrown"] < by_date["player"]["darts_thrown"]


def test_a_player_with_no_darts_is_a_well_formed_two_hundred(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """The criterion: nulls and zeroes, not an error."""
    response = seeded_client.post("/api/players", json={"display_name": "Newcomer"})
    assert response.status_code == 201
    body = player(seeded_client, int(response.json()["id"]))["player"]
    assert body["darts_thrown"] == 0
    assert body["x01"]["three_dart_average"] is None
    assert body["x01"]["bands"]["one_eighties"] == 0
    assert body["cricket"]["marks_per_round"] is None
    assert len(body["cricket"]["targets"]) == 7
    assert body["segments"] == []


def test_a_player_who_does_not_exist_is_a_404(seeded_client: TestClient) -> None:
    """Different from a player with no darts, and reported differently."""
    response = seeded_client.get("/api/stats/players/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "query",
    [
        {"game_type": "nonsense"},
        {"variant": "nonsense"},
        {"since": "not-a-date"},
        {"match_id": "0"},
        {"match_id": "-1"},
        {"gametype": "x01"},
    ],
)
def test_a_bad_query_parameter_is_a_field_level_422(
    seeded_client: TestClient, query: dict[str, str]
) -> None:
    """Validated during request parsing, so the failure names the field.

    A `ValueError` raised inside the handler would be a 500 and would blame the
    server for the client's typo -- including `?gametype=`, which `extra=forbid`
    turns into a refusal rather than silently unfiltered numbers.
    """
    response = seeded_client.get("/api/stats/players/1", params=query)
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert any("query" in error["loc"] for error in body["detail"])


def test_the_leaderboard_is_ranked_and_thresholded(seeded_client: TestClient) -> None:
    body = seeded_client.get("/api/stats/leaderboard", params={"min_darts": 0}).json()
    assert body["ranked_by"] == "three_dart_average"
    assert body["min_darts"] == 0
    averages = [row["three_dart_average"] for row in body["rows"]]
    assert averages == sorted(averages, reverse=True)
    # Dee threw three darts and hit a 180, which is exactly what a threshold is for.
    assert body["rows"][0]["display_name"] == "Dee"
    assert body["rows"][0]["darts_thrown"] == 3


def test_the_default_threshold_keeps_a_three_dart_player_off_the_table(
    seeded_client: TestClient,
) -> None:
    default = seeded_client.get("/api/stats/leaderboard").json()
    assert default["min_darts"] == DEFAULT_MIN_DARTS
    assert default["rows"] == [], "nobody in the seed has thrown fifty x01 darts"


def test_an_archived_player_is_off_the_leaderboard_but_keeps_their_stats(
    seeded_client: TestClient,
) -> None:
    """#17 keeps archived players in history and out of pickers; this follows that."""
    before = seeded_client.get("/api/stats/leaderboard", params={"min_darts": 0}).json()
    assert 1 in [row["player_id"] for row in before["rows"]]

    assert seeded_client.post("/api/players/1/archive").status_code == 200

    after = seeded_client.get("/api/stats/leaderboard", params={"min_darts": 0}).json()
    assert 1 not in [row["player_id"] for row in after["rows"]]
    # Her own endpoint still answers, with every number intact.
    body = player(seeded_client, 1)["player"]
    assert body["is_archived"] is True
    assert body["x01"]["best_checkout"] == 121


def test_a_match_report_has_a_line_per_player_and_per_leg(seeded_client: TestClient) -> None:
    body = seeded_client.get("/api/stats/matches/2").json()
    assert body["match"]["match_id"] == 2
    assert body["match"]["game_type"] == "x01"
    assert body["match"]["variant"] is None
    assert [p["player_id"] for p in body["match"]["players"]] == [1, 3, 2, 4]
    # Two legs, and each leg lists only the players who threw in it.
    legs = body["match"]["legs"]
    assert {leg["leg_index"] for leg in legs} == {0, 1}
    for leg in legs:
        assert leg["three_dart_average"] is not None
        assert leg["marks_per_round"] is None


def test_a_cricket_match_reports_marks_per_round_per_leg(seeded_client: TestClient) -> None:
    body = seeded_client.get("/api/stats/matches/3").json()
    assert body["match"]["variant"] == "standard"
    for leg in body["match"]["legs"]:
        assert leg["marks_per_round"] is not None
        assert leg["three_dart_average"] is None


def test_the_match_report_is_scoped_to_that_match(seeded_client: TestClient) -> None:
    """Ana's numbers inside match 1 are not her lifetime numbers."""
    scoped = seeded_client.get("/api/stats/matches/1").json()["match"]
    ana = next(p for p in scoped["players"] if p["player_id"] == 1)
    assert ana["darts_thrown"] < player(seeded_client, 1)["player"]["darts_thrown"]
    assert ana["matches_played"] == 1


def test_a_contradictory_match_id_is_refused(seeded_client: TestClient) -> None:
    """Accepted for uniformity, but it may only name the match in the path."""
    assert seeded_client.get("/api/stats/matches/2", params={"match_id": 2}).status_code == 200
    response = seeded_client.get("/api/stats/matches/2", params={"match_id": 3})
    assert response.status_code == 422
    assert response.json()["error"]["message"].startswith("match_id must name the match")


def test_a_match_that_does_not_exist_is_a_404(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/stats/matches/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_endpoints_read_without_holding_a_write_transaction(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """A report is several queries and must see one consistent database.

    A deferred read transaction is what gives it that; an IMMEDIATE one would
    take the write lock and make looking at a chart block a dart being recorded.
    """
    assert seeded_client.get("/api/stats/players/1").status_code == 200
    # The database is not left locked: another connection can still write.
    other = sqlite3.connect(make_settings(tmp_path).db_path)
    try:
        other.execute("PRAGMA busy_timeout = 1000")
        other.execute("INSERT INTO players(display_name) VALUES ('Writer')")
        other.commit()
    finally:
        other.close()
