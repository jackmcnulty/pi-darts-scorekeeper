"""Setup API contracts, field locations, persisted status and bounded history."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from darts.config import Settings
from darts.db.connection import connection, transaction
from darts.repo.matches import set_match_winner

X01 = {
    "game_type": "x01",
    "start_score": 501,
    "in_rule": "straight",
    "out_rule": "double",
    "best_of": 3,
}
CRICKET = {"game_type": "cricket", "variant": "standard", "best_of": 1}


@pytest.fixture
def payload(client: TestClient) -> dict:
    ids = [
        client.post("/api/players", json={"display_name": n}).json()["id"]
        for n in ("Ana", "Ben", "Cal", "Dee")
    ]
    return {
        "config": dict(X01),
        "teams": [
            {"player_ids": ids[:2], "name": "Reds"},
            {"player_ids": ids[2:], "name": "Blues"},
        ],
    }


@pytest.mark.parametrize(
    ("config", "field", "value"),
    [
        (CRICKET, "variant", None),
        (X01, "out_rule", None),
        (CRICKET, "out_rule", "double"),
        (X01, "best_of", 2),
        (X01, "best_of", 0),
        (X01, "best_of", -1),
        (X01, "start_score", None),
        (X01, "in_rule", None),
        (X01, "variant", "standard"),
        (CRICKET, "start_score", 501),
        (CRICKET, "in_rule", "straight"),
        (X01, "start_score", 0),
        (X01, "out_rule", "bogus"),
        (CRICKET, "variant", "bogus"),
        (X01, "game_type", "bogus"),
        (X01, "best_of", "bogus"),
        (X01, "fixed_team", -1),
        (X01, "start_rule", "bogus"),
        (X01, "unknown", True),
    ],
)
def test_config_validation_matrix(
    client: TestClient, payload: dict, config: dict, field: str, value: object
) -> None:
    payload["config"] = dict(config)
    if value is None:
        payload["config"].pop(field)
    else:
        payload["config"][field] = value
    response = client.post("/api/matches", json=payload)
    assert response.status_code == 422
    errors = response.json()["error"]["detail"]
    assert any(e["loc"] == ["body", "config", field] and e["msg"] for e in errors)
    assert client.get("/api/matches").json()["total"] == 0


@pytest.mark.parametrize(
    ("teams", "loc"),
    [
        ([], ["teams"]),
        ([{"player_ids": [1]}], ["teams"]),
        ([{"player_ids": []}, {"player_ids": [2]}], ["teams", 0, "player_ids"]),
        ([{"player_ids": [0]}, {"player_ids": [2]}], ["teams", 0, "player_ids", 0]),
        ([{"player_ids": [1]}, {"player_ids": [1]}], ["teams"]),
    ],
)
def test_team_validation_matrix(client: TestClient, teams: list, loc: list) -> None:
    response = client.post("/api/matches", json={"config": X01, "teams": teams})
    assert response.status_code == 422
    assert any(e["loc"] == ["body", *loc] and e["msg"] for e in response.json()["error"]["detail"])


def test_fixed_team_out_of_range(client: TestClient, payload: dict) -> None:
    payload["config"]["fixed_team"] = 2
    response = client.post("/api/matches", json=payload)
    assert response.status_code == 422
    assert "fixed_team" in response.json()["error"]["detail"][0]["msg"]


@pytest.mark.parametrize("config", [X01, CRICKET])
def test_create_detail_and_historical_members(
    client: TestClient, payload: dict, config: dict
) -> None:
    payload["config"] = config
    response = client.post("/api/matches", json=payload)
    assert response.status_code == 201
    match = response.json()
    assert match["status"] == "in_progress"
    assert match["current_leg_id"] > 0
    assert match["completed_at"] is match["abandoned_at"] is match["winner_team_id"] is None
    assert [t["name"] for t in match["teams"]] == ["Reds", "Blues"]
    assert [m["display_name"] for t in match["teams"] for m in t["members"]] == [
        "Ana",
        "Ben",
        "Cal",
        "Dee",
    ]
    assert [m["member_index"] for m in match["teams"][0]["members"]] == [0, 1]
    assert all(not t["is_solo"] for t in match["teams"])
    assert client.get(f"/api/matches/{match['id']}").json() == match
    player_id = payload["teams"][0]["player_ids"][0]
    client.post(f"/api/players/{player_id}/archive")
    detail = client.get(f"/api/matches/{match['id']}").json()
    assert detail["teams"][0]["members"][0]["is_archived"] is True
    assert client.post("/api/matches", json=payload).status_code == 422


def test_status_filter_abandonment_and_pagination(
    client: TestClient, payload: dict, settings: Settings
) -> None:
    matches = [client.post("/api/matches", json=payload).json() for _ in range(4)]
    done, abandoned, older, newer = matches
    with connection(settings.db_path) as conn, transaction(conn):
        set_match_winner(conn, done["id"], done["teams"][0]["id"])
        conn.execute("UPDATE matches SET created_at = '2026-01-01T00:00:00.000Z'")
    url = f"/api/matches/{abandoned['id']}/abandon"
    response = client.post(url)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "abandoned" and body["abandoned_at"].endswith("Z")
    assert body["completed_at"] is body["winner_team_id"] is None
    assert client.post(url).json() == body
    assert client.post(f"/api/matches/{done['id']}/abandon").status_code == 409
    for status, expected in (
        ("in_progress", [newer["id"], older["id"]]),
        ("complete", [done["id"]]),
        ("abandoned", [abandoned["id"]]),
    ):
        page = client.get(f"/api/matches?status={status}").json()
        assert [m["id"] for m in page["items"]] == expected
        assert page["total"] == len(expected)
    page = client.get("/api/matches?limit=1&offset=1").json()
    assert page["total"] == 4 and page["limit"] == 1 and page["offset"] == 1
    assert [m["id"] for m in page["items"]] == [older["id"]]
    page = client.get("/api/matches?status=in_progress&limit=1&offset=1").json()
    assert page["total"] == 2 and page["items"][0]["id"] == older["id"]
    assert client.get("/api/matches?offset=100").json()["items"] == []


@pytest.mark.parametrize("query", ["status=wrong", "limit=0", "limit=101", "offset=-1"])
def test_invalid_list_query(client: TestClient, query: str) -> None:
    response = client.get("/api/matches?" + query)
    assert response.status_code == 422
    assert response.json()["error"]["detail"][0]["loc"][0] == "query"


def test_missing_matches_and_players(client: TestClient, payload: dict) -> None:
    assert client.get("/api/matches/999").status_code == 404
    assert client.post("/api/matches/999/abandon").status_code == 404
    broken = deepcopy(payload)
    broken["teams"][1]["player_ids"] = [999]
    assert client.post("/api/matches", json=broken).status_code == 404
    assert client.get("/api/matches").json()["total"] == 0


def test_setup_routes_precede_spa_and_have_response_schemas(served: TestClient) -> None:
    assert served.get("/api/players").json() == []
    assert served.get("/api/matches").json()["items"] == []
    schema = served.get("/api/openapi.json").json()
    for path, method, code in [
        ("/api/players", "get", "200"),
        ("/api/players", "post", "201"),
        ("/api/players/{player_id}", "patch", "200"),
        ("/api/players/{player_id}/archive", "post", "200"),
        ("/api/matches", "get", "200"),
        ("/api/matches", "post", "201"),
        ("/api/matches/{match_id}", "get", "200"),
        ("/api/matches/{match_id}/abandon", "post", "200"),
    ]:
        assert schema["paths"][path][method]["responses"][code]["content"]["application/json"][
            "schema"
        ]


def test_resume_list_tracks_the_next_leg(
    client: TestClient, payload: dict, settings: Settings
) -> None:
    from darts.engine.throws import Throw
    from darts.services import play

    payload["config"].update(start_score=1, out_rule="straight")
    match = client.post("/api/matches", json=payload).json()
    with connection(settings.db_path) as conn:
        state = play.throw(
            conn, leg_id=match["current_leg_id"], dart=Throw(1, 1), client_dart_id="win-first-leg"
        )
    page = client.get("/api/matches?status=in_progress").json()
    assert page["total"] == 1
    assert page["items"][0]["current_leg_id"] == state.active_leg_id
    assert state.active_leg_id != match["current_leg_id"]


def test_default_page_is_bounded(client: TestClient, payload: dict) -> None:
    for _ in range(51):
        assert client.post("/api/matches", json=payload).status_code == 201
    page = client.get("/api/matches").json()
    assert page["total"] == 51
    assert page["limit"] == 50 and page["offset"] == 0
    assert len(page["items"]) == 50
    last = client.get("/api/matches?offset=50").json()
    assert len(last["items"]) == 1
    assert last["items"][0]["id"] not in {item["id"] for item in page["items"]}
