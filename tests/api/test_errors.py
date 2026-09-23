"""One envelope for every failure, and the status each error hierarchy earns."""

from collections.abc import Iterator
from typing import Any

import pytest
from apifixtures import error_app
from fastapi.testclient import TestClient

from darts.api.errors import ErrorCode, code_for_status


@pytest.fixture
def errors() -> Iterator[TestClient]:
    """`raise_server_exceptions=False` so the 500 handler's response is seen.

    TestClient re-raises an unhandled exception by default, which is useful
    everywhere except here, where the response body is the thing under test.
    """
    with TestClient(error_app(), raise_server_exceptions=False) as client:
        yield client


def envelope_of(response: Any) -> dict[str, Any]:
    """Assert the shape once, here, and return the contents."""
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == {"code", "message", "detail"}, error
    assert error["code"] in set(ErrorCode)
    assert isinstance(error["message"], str) and error["message"]
    return error


@pytest.mark.parametrize(
    ("path", "status", "code", "reason"),
    [
        ("/api/not-found", 404, "not_found", "not_found"),
        ("/api/duplicate", 409, "conflict", "duplicate_name"),
        ("/api/invalid-match", 422, "validation_error", "invalid_match"),
        ("/api/repo-error", 400, "invalid_request", "repo"),
        ("/api/leg-complete", 409, "conflict", "leg_complete"),
        ("/api/service-error", 409, "conflict", "service"),
    ],
)
def test_domain_errors_map_to_a_status_and_a_reason(
    errors: TestClient, path: str, status: int, code: str, reason: str
) -> None:
    """An endpoint only has to raise; the mapping lives in one place."""
    response = errors.get(path)
    assert response.status_code == status
    error = envelope_of(response)
    assert error["code"] == code
    assert error["detail"] == {"reason": reason}


def test_a_conflict_says_which_conflict_it_was(errors: TestClient) -> None:
    """Several refusals share one code, so `reason` is what a client branches on."""
    reasons = {
        errors.get(path).json()["error"]["detail"]["reason"]
        for path in ("/api/duplicate", "/api/leg-complete")
    }
    assert reasons == {"duplicate_name", "leg_complete"}


def test_validation_errors_keep_pydantics_field_locations(errors: TestClient) -> None:
    """#17 asserts on `loc` to prove the right field was refused."""
    response = errors.post("/api/validated", json={"name": "Jack"})
    assert response.status_code == 422
    error = envelope_of(response)
    assert error["code"] == "validation_error"
    assert [d["loc"] for d in error["detail"]] == [["body", "score"]]
    assert error["detail"][0]["type"] == "missing"


def test_an_unhandled_bug_leaks_nothing(errors: TestClient) -> None:
    response = errors.get("/api/boom")
    assert response.status_code == 500
    error = envelope_of(response)
    assert error == {"code": "internal", "message": "Internal server error", "detail": None}
    assert "nobody planned" not in response.text


def test_an_unhandled_bug_is_logged_with_its_traceback(
    errors: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("ERROR", logger="darts.api.errors"):
        errors.get("/api/boom")
    (record,) = [r for r in caplog.records if r.name == "darts.api.errors"]
    assert record.exc_info is not None


def test_a_database_failure_is_a_503(errors: TestClient) -> None:
    """A locked or read-only database is a condition of the box, not a bad request."""
    response = errors.get("/api/database")
    assert response.status_code == 503
    error = envelope_of(response)
    assert error["code"] == "service_unavailable"
    assert error["detail"] == {"reason": "OperationalError"}


def test_a_raised_http_exception_keeps_its_message(errors: TestClient) -> None:
    response = errors.get("/api/teapot")
    assert response.status_code == 418
    assert envelope_of(response) == {
        "code": "invalid_request",
        "message": "short and stout",
        "detail": None,
    }


def test_a_structured_http_detail_survives(errors: TestClient) -> None:
    response = errors.get("/api/structured")
    error = envelope_of(response)
    assert error["code"] == "conflict"
    assert error["detail"] == {"reason": "structured"}
    assert error["message"] == "Conflict"


def test_an_unrouted_path_is_json_not_html(errors: TestClient) -> None:
    response = errors.get("/api/nothing-here")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert envelope_of(response)["code"] == "not_found"


def test_a_wrong_method_keeps_its_allow_header(errors: TestClient) -> None:
    """The envelope must not cost the response its headers."""
    response = errors.post("/api/not-found")
    assert response.status_code == 405
    assert "GET" in response.headers["allow"]
    assert envelope_of(response)["code"] == "invalid_request"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, ErrorCode.INVALID_REQUEST),
        (404, ErrorCode.NOT_FOUND),
        (409, ErrorCode.CONFLICT),
        (422, ErrorCode.VALIDATION_ERROR),
        (503, ErrorCode.SERVICE_UNAVAILABLE),
        (418, ErrorCode.INVALID_REQUEST),
        (502, ErrorCode.INTERNAL),
    ],
)
def test_unnamed_statuses_fall_back_to_the_class_of_the_status(
    status: int, code: ErrorCode
) -> None:
    assert code_for_status(status) is code
