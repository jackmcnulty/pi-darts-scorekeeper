from fastapi.testclient import TestClient

from darts import __version__
from darts.api.main import app

client = TestClient(app)


def test_ping() -> None:
    response = client.get("/api/ping")
    assert response.status_code == 200
    assert response.json() == {"pong": True, "version": __version__}
