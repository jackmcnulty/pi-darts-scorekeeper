"""The schema #18 generates its TypeScript client from."""

from fastapi.testclient import TestClient


def test_the_schema_is_served_under_api(client: TestClient) -> None:
    """Under /api so the Vite proxy and the static mount both leave it alone."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "darts"
    assert set(schema["paths"]) == {
        "/api/healthz",
        "/api/version",
        "/api/players",
        "/api/players/{player_id}",
        "/api/players/{player_id}/archive",
        "/api/matches",
        "/api/matches/{match_id}",
        "/api/matches/{match_id}/abandon",
    }


def test_the_documented_responses_match_what_the_endpoints_return(client: TestClient) -> None:
    healthz = client.get("/api/openapi.json").json()["paths"]["/api/healthz"]["get"]
    assert set(healthz["responses"]) == {"200", "503"}

    report = client.get("/api/healthz").json()
    properties = client.get("/api/openapi.json").json()["components"]["schemas"]["HealthReport"]
    assert set(report) == set(properties["properties"])


def test_the_docs_page_is_under_api_too(client: TestClient) -> None:
    assert client.get("/api/docs").status_code == 200
    # Not at the root, where it would collide with the app's own routes.
    assert client.get("/docs").status_code == 404


def test_the_placeholder_ping_is_gone(client: TestClient) -> None:
    """#16 replaces it with /api/healthz."""
    assert client.get("/api/ping").status_code == 404
