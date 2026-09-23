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


def test_colour_is_assigned_without_being_asked_for(client: TestClient) -> None:
    """Adding a player without opening the picker still gives them a free colour."""
    ana = client.post("/api/players", json={"display_name": "Ana"}).json()
    ben = client.post("/api/players", json={"display_name": "Ben"}).json()
    assert (ana["accent_index"], ben["accent_index"]) == (1, 2)
    assert ana["short_name"] is None


def test_a_chosen_colour_and_short_name_round_trip(client: TestClient) -> None:
    created = client.post(
        "/api/players", json={"display_name": "Ana", "short_name": " A.B ", "accent_index": 5}
    )
    assert created.status_code == 201
    assert created.json()["short_name"] == "A.B"
    assert created.json()["accent_index"] == 5
    assert client.get("/api/players").json() == [created.json()]


def test_patch_leaves_out_what_it_does_not_mention(client: TestClient) -> None:
    """PATCH is partial: a rename must not silently drop a colour."""
    ana = client.post(
        "/api/players", json={"display_name": "Ana", "short_name": "A.B", "accent_index": 3}
    ).json()
    url = f"/api/players/{ana['id']}"

    renamed = client.patch(url, json={"display_name": "Ana B."}).json()
    assert (renamed["short_name"], renamed["accent_index"]) == ("A.B", 3)

    recoloured = client.patch(url, json={"display_name": "Ana B.", "accent_index": 6}).json()
    assert (recoloured["short_name"], recoloured["accent_index"]) == ("A.B", 6)


def test_patch_with_an_explicit_null_clears_the_field(client: TestClient) -> None:
    ana = client.post(
        "/api/players", json={"display_name": "Ana", "short_name": "A.B", "accent_index": 3}
    ).json()
    cleared = client.patch(
        f"/api/players/{ana['id']}",
        json={"display_name": "Ana", "short_name": None, "accent_index": None},
    ).json()
    assert (cleared["short_name"], cleared["accent_index"]) == (None, None)


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"display_name": "Ana", "accent_index": 0}, "accent_index"),
        ({"display_name": "Ana", "accent_index": 9}, "accent_index"),
        ({"display_name": "Ana", "accent_index": "red"}, "accent_index"),
        ({"display_name": "Ana", "short_name": "TooLongName"}, "short_name"),
    ],
)
def test_invalid_identity_is_a_422_naming_the_field(
    client: TestClient, payload: dict, field: str
) -> None:
    """A colour outside the palette is the request's fault, not the database's."""
    response = client.post("/api/players", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["detail"][0]["loc"] == ["body", field]
    assert client.get("/api/players").json() == []


def test_a_short_name_is_measured_after_trimming(client: TestClient) -> None:
    """Eight characters wrapped in spaces is still eight characters."""
    response = client.post(
        "/api/players", json={"display_name": "Ana", "short_name": "  Bartlett  "}
    )
    assert response.status_code == 201
    assert response.json()["short_name"] == "Bartlett"


def test_a_ninth_player_shares_rather_than_being_refused(client: TestClient) -> None:
    for index in range(8):
        assert client.post("/api/players", json={"display_name": f"P{index}"}).status_code == 201
    ninth = client.post("/api/players", json={"display_name": "P8"})
    assert ninth.status_code == 201
    assert ninth.json()["accent_index"] == 1


def test_concurrent_creates_never_hand_out_the_same_colour_twice(client: TestClient) -> None:
    """Two phones adding a player at once: the colour is chosen inside the write."""
    from concurrent.futures import ThreadPoolExecutor

    def create(index: int) -> int | None:
        response = client.post("/api/players", json={"display_name": f"Player {index}"})
        assert response.status_code == 201
        accent: int | None = response.json()["accent_index"]
        return accent

    with ThreadPoolExecutor(max_workers=8) as pool:
        accents = sorted(pool.map(create, range(8)))
    assert accents == list(range(1, 9))
