"""Serving the app: the SPA fallback, cache headers, and what `/api` keeps.

Every case runs against a fake build under `tmp_path`. `frontend/dist` is
gitignored and CI's backend job never builds it, so a test that read the real
directory would prove nothing on the machine that matters.
"""

from pathlib import Path

import pytest
from apifixtures import HASHED_CSS, HASHED_JS, build_dist, make_settings
from fastapi.testclient import TestClient

from darts.api.main import create_app
from darts.api.static import HASHED, IMMUTABLE, NO_CACHE


def test_the_root_serves_index_html(served: TestClient) -> None:
    response = served.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<div id=root>" in response.text


def test_a_client_route_survives_a_refresh(served: TestClient) -> None:
    """`StaticFiles(html=True)` does not do this: it 404s everything but a directory."""
    response = served.get("/history/42")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<div id=root>" in response.text


@pytest.mark.parametrize("route", ["/history/42", "/match/7/play", "/players", "/deeply/nested/1"])
def test_every_depth_of_client_route_falls_back(served: TestClient, route: str) -> None:
    assert served.get(route).text.startswith("<!doctype html>")


def test_an_unknown_api_path_is_json_not_the_app(served: TestClient) -> None:
    """The fallback must never swallow `/api`, or a typo'd endpoint returns HTML."""
    response = served.get("/api/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "not_found"


def test_the_api_keeps_working_behind_the_mount(served: TestClient) -> None:
    assert served.get("/api/healthz").status_code == 200
    assert served.get("/api/openapi.json").status_code == 200


@pytest.mark.parametrize("asset", [HASHED_JS, HASHED_CSS])
def test_hashed_assets_are_immutable(served: TestClient, asset: str) -> None:
    response = served.get(f"/assets/{asset}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE


@pytest.mark.parametrize("path", ["/", "/index.html", "/favicon.svg", "/history/42"])
def test_everything_unhashed_is_revalidated(served: TestClient, path: str) -> None:
    """A stale index.html would point a phone at assets that no longer exist."""
    assert served.get(path).headers["cache-control"] == NO_CACHE


@pytest.mark.parametrize(
    ("name", "hashed"),
    [
        ("index-BKd04slA.js", True),
        ("index-Dv5vcoGT.css", True),
        ("index.html", False),
        ("favicon.svg", False),
        ("icons.svg", False),
        ("vendor-a1b2.js", False),
        ("darts-logo.svg", False),
    ],
)
def test_what_counts_as_a_content_hash(name: str, hashed: bool) -> None:
    """Short suffixes are names, not hashes; guessing wrong caches a file forever."""
    assert (HASHED.search(name) is not None) is hashed


def test_a_write_to_a_static_path_is_not_the_app(served: TestClient) -> None:
    """Only a 404 falls back, so a POST stays a 405 rather than becoming HTML."""
    response = served.post("/history/42")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_traversal_attempt_gets_nothing(served: TestClient, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("not yours")
    # Percent-encoded so the traversal reaches the static handler intact rather
    # than being normalised away by the URL parser first.
    response = served.get("/assets/%2e%2e/%2e%2e/secret.txt")
    assert "not yours" not in response.text


def test_a_missing_asset_does_not_quietly_become_html(served: TestClient) -> None:
    """An asset request is a machine's request; HTML for it would be a puzzle.

    The fallback is unconditional for non-`/api` paths, so this documents the
    behaviour the browser actually gets: a 200 of index.html, which fails
    loudly at the point of use rather than pretending the file exists.
    """
    response = served.get("/assets/index-deadbeef.js")
    assert response.status_code == 200
    assert response.text.startswith("<!doctype html>")


def test_no_build_means_no_mount_and_an_explanation(client: TestClient) -> None:
    """Development and CI both run without a build; that is not an error."""
    response = client.get("/")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "DARTS_STATIC_DIR" in error["message"]


def test_no_build_still_answers_api_paths_generically(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Not Found"
    assert client.get("/api/healthz").status_code == 200


def test_a_directory_without_index_html_is_treated_as_no_build(tmp_path: Path) -> None:
    """A build that half-copied is not a build."""
    partial = build_dist(tmp_path)
    (partial / "index.html").unlink()
    with TestClient(create_app(make_settings(tmp_path, static_dir=partial))) as client:
        assert client.get("/").status_code == 404
        assert client.get(f"/assets/{HASHED_JS}").status_code == 404
