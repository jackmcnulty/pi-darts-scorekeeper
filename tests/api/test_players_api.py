"""Player API writes persist across requests and preserve archived history."""

import pytest
from fastapi.testclient import TestClient


def test_player_crud_and_archive(client: TestClient) -> None:
    assert client.get("/api/players").json() == []
    response = client.post("/api/players", json={"display_name": " Ana "})
    assert response.status_code == 201
    player = response.json()
    assert player["display_name"] == "Ana"
    assert player["is_archived"] is False
    assert player["created_at"].endswith("Z")
    url = f"/api/players/{player['id']}"
    renamed = client.patch(url, json={"display_name": "Ann"})
    assert renamed.status_code == 200
    assert client.get("/api/players").json() == [renamed.json()]
    archived = client.post(url + "/archive")
    assert archived.status_code == 200
    assert archived.json()["is_archived"] is True
    assert client.post(url + "/archive").json() == archived.json()
    assert client.get("/api/players").json() == []
    assert client.get("/api/players?include_archived=true").json() == [archived.json()]
    assert client.post("/api/players", json={"display_name": "ann"}).status_code == 201


def test_duplicate_names_on_create_and_update(client: TestClient) -> None:
    client.post("/api/players", json={"display_name": "Straße"})
    other = client.post("/api/players", json={"display_name": "Ben"}).json()
    for response in (
        client.post("/api/players", json={"display_name": " STRASSE "}),
        client.patch(f"/api/players/{other['id']}", json={"display_name": "strasse"}),
    ):
        assert response.status_code == 409
        assert response.json()["error"]["detail"] == {"reason": "duplicate_name"}
    assert {p["display_name"] for p in client.get("/api/players").json()} == {"Straße", "Ben"}


@pytest.mark.parametrize(
    "payload",
    [{}, {"display_name": " "}, {"display_name": None}, {"display_name": "Ana", "colour": "red"}],
)
def test_invalid_player_payload(client: TestClient, payload: dict) -> None:
    response = client.post("/api/players", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["detail"][0]["loc"][0] == "body"
    assert client.get("/api/players").json() == []


def test_missing_players(client: TestClient) -> None:
    assert client.patch("/api/players/999", json={"display_name": "Ana"}).status_code == 404
    assert client.post("/api/players/999/archive").status_code == 404
    assert client.get("/api/players?include_archived=bogus").status_code == 422


def test_concurrent_player_requests(client: TestClient) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def create(index: int) -> int:
        return client.post("/api/players", json={"display_name": f"Player {index}"}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(create, range(16)))
    assert statuses == [201] * 16
    assert len(client.get("/api/players").json()) == 16


def test_concurrent_duplicate_names_are_atomic(client: TestClient) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def create(index: int) -> int:
        name = "Ana" if index % 2 else " ANA "
        return client.post("/api/players", json={"display_name": name}).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(create, range(8)))
    assert sorted(statuses) == [201, *([409] * 7)]
    assert len(client.get("/api/players").json()) == 1
