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
        "/api/matches/{match_id}/state",
        "/api/legs/{leg_id}/darts",
        "/api/legs/{leg_id}/undo",
        "/api/legs/{leg_id}/checkout",
        "/api/stats/players/{player_id}",
        "/api/stats/leaderboard",
        "/api/stats/matches/{match_id}",
    }


def test_every_play_route_documents_an_explicit_response_model(client: TestClient) -> None:
    """The TypeScript client is generated from this, so an undeclared body is an untyped one."""
    paths = client.get("/api/openapi.json").json()["paths"]
    play = {
        ("/api/matches/{match_id}/state", "get"): "MatchStateResponse",
        ("/api/legs/{leg_id}/darts", "post"): "MatchStateResponse",
        ("/api/legs/{leg_id}/undo", "post"): "MatchStateResponse",
        ("/api/legs/{leg_id}/checkout", "get"): "CheckoutResponse",
    }
    for (path, method), model in play.items():
        content = paths[path][method]["responses"]["200"]["content"]
        assert content["application/json"]["schema"]["$ref"].endswith(f"/{model}")


def test_every_stats_route_documents_an_explicit_response_model(client: TestClient) -> None:
    """Same rule as the play routes: #19's shapes are generated into the client too."""
    paths = client.get("/api/openapi.json").json()["paths"]
    stats = {
        ("/api/stats/players/{player_id}", "get"): "PlayerReportResponse",
        ("/api/stats/leaderboard", "get"): "LeaderboardResponse",
        ("/api/stats/matches/{match_id}", "get"): "MatchReportResponse",
    }
    for (path, method), model in stats.items():
        content = paths[path][method]["responses"]["200"]["content"]
        assert content["application/json"]["schema"]["$ref"].endswith(f"/{model}")


def test_the_stats_filters_are_documented_as_query_parameters(client: TestClient) -> None:
    """The four the ticket names, on all three routes, plus the leaderboard's threshold.

    FastAPI only expands a Pydantic query model when it is a route's *only*
    query parameter, which is why `min_darts` lives inside the leaderboard's
    model. If that ever regressed, these would collapse to one parameter called
    `applied` and every request would 422.
    """
    paths = client.get("/api/openapi.json").json()["paths"]
    for path in (
        "/api/stats/players/{player_id}",
        "/api/stats/leaderboard",
        "/api/stats/matches/{match_id}",
    ):
        query = {
            parameter["name"]
            for parameter in paths[path]["get"]["parameters"]
            if parameter["in"] == "query"
        }
        assert {"game_type", "variant", "since", "match_id"} <= query, path
    leaderboard = paths["/api/stats/leaderboard"]["get"]["parameters"]
    assert any(parameter["name"] == "min_darts" for parameter in leaderboard)


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
