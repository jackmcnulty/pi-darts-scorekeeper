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
        "/api/export/matches.csv",
        "/api/export/darts.csv",
        "/api/export/stats.json",
        "/api/export/db",
        "/api/admin/snapshot",
        "/api/admin/rebuild-caches",
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


def test_every_json_export_and_admin_route_documents_an_explicit_response_model(
    client: TestClient,
) -> None:
    """#20's JSON routes are held to exactly the rule #18 and #19 set."""
    paths = client.get("/api/openapi.json").json()["paths"]
    routes = {
        ("/api/export/stats.json", "get"): "StatsExportResponse",
        ("/api/admin/snapshot", "post"): "SnapshotResponse",
        ("/api/admin/rebuild-caches", "post"): "RebuildResponse",
    }
    for (path, method), model in routes.items():
        content = paths[path][method]["responses"]["200"]["content"]
        assert content["application/json"]["schema"]["$ref"].endswith(f"/{model}")


def test_the_streaming_exports_document_a_media_type_instead_of_a_model(
    client: TestClient,
) -> None:
    """A CSV body and a database file are not models, so the media type is the contract.

    Deliberately not the `$ref` assertion above, which these could never satisfy:
    a route that declared `application/json` here would be lying, and one left
    undeclared would generate an untyped client. What is pinned is that each
    declares exactly the one media type it actually serves.
    """
    paths = client.get("/api/openapi.json").json()["paths"]
    streamed = {
        "/api/export/matches.csv": "text/csv",
        "/api/export/darts.csv": "text/csv",
        "/api/export/db": "application/vnd.sqlite3",
    }
    for path, media_type in streamed.items():
        content = paths[path]["get"]["responses"]["200"]["content"]
        assert set(content) == {media_type}, path
        assert "$ref" not in content[media_type]["schema"]
        assert content[media_type]["schema"]["type"] == "string"
    assert (
        paths["/api/export/db"]["get"]["responses"]["200"]["content"]["application/vnd.sqlite3"][
            "schema"
        ]["format"]
        == "binary"
    )


def test_the_export_routes_take_the_same_filters_the_stats_routes_do(
    client: TestClient,
) -> None:
    """Same Pydantic query model, so the same four appear, expanded not collapsed.

    If `Filter` ever stopped being a route's only query parameter, FastAPI would
    silently turn it into one scalar called `applied` and every export would
    422. This is what would catch that.
    """
    paths = client.get("/api/openapi.json").json()["paths"]
    for path in ("/api/export/matches.csv", "/api/export/darts.csv", "/api/export/stats.json"):
        query = {
            parameter["name"]
            for parameter in paths[path]["get"]["parameters"]
            if parameter["in"] == "query"
        }
        assert {"game_type", "variant", "since", "match_id"} <= query, path
        assert "applied" not in query, path

    # `min_darts` is the statistics export's alone: neither CSV has a ranking.
    assert "min_darts" in {
        parameter["name"] for parameter in paths["/api/export/stats.json"]["get"]["parameters"]
    }
    for csv_path in ("/api/export/matches.csv", "/api/export/darts.csv"):
        assert "min_darts" not in {
            parameter["name"] for parameter in paths[csv_path]["get"]["parameters"]
        }


def test_the_admin_routes_are_post_only(client: TestClient) -> None:
    """They are actions, not resources."""
    paths = client.get("/api/openapi.json").json()["paths"]
    assert set(paths["/api/admin/snapshot"]) == {"post"}
    assert set(paths["/api/admin/rebuild-caches"]) == {"post"}


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
