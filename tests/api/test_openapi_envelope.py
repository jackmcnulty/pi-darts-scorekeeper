"""The published schema's account of failure, and whether it is true.

Two halves, and the second is the one that matters. The first checks that the
rewrite in `darts.api.openapi` reached every operation. The second sends real
requests that really fail and validates the bodies against the very model the
schema now advertises -- because a schema that agrees with itself and disagrees
with the server is exactly the state #21 inherited.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from darts.api.errors import ApiError, ErrorCode, ErrorEnvelope
from darts.api.openapi import (
    DEFAULT_DESCRIPTION,
    ENVELOPE_REF,
    JSON,
    METHODS,
    SUPERSEDED,
    DartsApp,
    envelope_schemas,
    with_error_envelope,
)


@pytest.fixture
def schema(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    return dict(response.json())


def operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operation)
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        if method in METHODS
    ]


def test_there_are_operations_to_check(schema: dict[str, Any]) -> None:
    """Guards every loop below: an empty schema would pass all of them."""
    assert len(operations(schema)) == 23


def test_every_failure_response_is_the_envelope(schema: dict[str, Any]) -> None:
    for path, method, operation in operations(schema):
        for status, response in operation["responses"].items():
            if status == "default" or int(status) >= 400:
                ref = response["content"][JSON]["schema"]["$ref"]
                assert ref == ENVELOPE_REF, f"{method.upper()} {path} {status}"


def test_every_operation_documents_a_default_failure(schema: dict[str, Any]) -> None:
    """A route that enumerates only its 422 can still answer 500."""
    for path, method, operation in operations(schema):
        default = operation["responses"]["default"]
        assert default["description"] == DEFAULT_DESCRIPTION, f"{method.upper()} {path}"


def test_success_responses_are_left_alone(schema: dict[str, Any]) -> None:
    """The rewrite is about failures; #18's response models must survive it."""
    ok = schema["paths"]["/api/players"]["get"]["responses"]["200"]
    assert ok["content"][JSON]["schema"]["items"]["$ref"].endswith("/PlayerResponse")


def test_a_route_keeps_its_own_words_for_a_failure(schema: dict[str, Any]) -> None:
    """Only the content is replaced: health's description beats anything generated."""
    unavailable = schema["paths"]["/api/healthz"]["get"]["responses"]["503"]
    assert unavailable["description"] == "Degraded, unwritable, or not yet started"
    assert unavailable["content"][JSON]["schema"]["$ref"] == ENVELOPE_REF


@pytest.mark.parametrize("name", SUPERSEDED)
def test_fastapis_account_of_errors_is_gone(schema: dict[str, Any], name: str) -> None:
    """Nothing references it, and a client should not offer a type nothing sends."""
    assert name not in schema["components"]["schemas"]


def test_the_envelope_and_its_parts_are_published(schema: dict[str, Any]) -> None:
    schemas = schema["components"]["schemas"]
    assert set(schemas) >= {"ErrorEnvelope", "ApiError", "ErrorCode"}
    assert schemas["ErrorEnvelope"]["properties"]["error"]["$ref"].endswith("/ApiError")


def test_the_published_codes_are_the_whole_vocabulary(schema: dict[str, Any]) -> None:
    """The frontend branches on these, so a new code must reach the client."""
    published = schema["components"]["schemas"]["ErrorCode"]["enum"]
    assert published == [code.value for code in ErrorCode]


# --- The half that catches drift -------------------------------------------


def validated(body: Any) -> ErrorEnvelope:
    """The schema promises this parses. Fail loudly and legibly when it does not."""
    try:
        return ErrorEnvelope.model_validate(body)
    except PydanticValidationError as exc:  # pragma: no cover - only on a regression
        message = f"the server sent something the schema forbids: {body}\n{exc}"
        raise AssertionError(message) from exc


def test_a_missing_route_sends_the_published_shape(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert validated(response.json()).error.code is ErrorCode.NOT_FOUND


def test_a_malformed_body_sends_the_published_shape(client: TestClient) -> None:
    """422s are the ones the schema used to describe as `HTTPValidationError`."""
    response = client.post("/api/players", json={})
    assert response.status_code == 422
    envelope = validated(response.json())
    assert envelope.error.code is ErrorCode.VALIDATION_ERROR
    assert isinstance(envelope.error.detail, list)


def test_a_refused_domain_rule_sends_the_published_shape(client: TestClient) -> None:
    """409s carry the `reason` discriminator under the `detail` the schema types."""
    assert client.post("/api/players", json={"display_name": "Ada"}).status_code == 201
    response = client.post("/api/players", json={"display_name": "Ada"})
    assert response.status_code == 409
    envelope = validated(response.json())
    assert envelope.error.code is ErrorCode.CONFLICT
    assert envelope.error.detail == {"reason": "duplicate_name"}


def test_a_missing_record_carries_its_reason(client: TestClient) -> None:
    """A real route raising `NotFoundError`, not the no-build catch-all's bare 404."""
    response = client.get("/api/matches/4242")
    assert response.status_code == 404
    assert validated(response.json()).error.detail == {"reason": "not_found"}


def test_an_absent_detail_round_trips(client: TestClient) -> None:
    """`detail` is optional in the schema, so its absence must parse, not raise."""
    assert ErrorEnvelope(error=ApiError(code=ErrorCode.INTERNAL, message="x")).error.detail is None
    assert validated({"error": {"code": "internal", "message": "x"}}).error.detail is None


# --- The rewrite in isolation ----------------------------------------------


def test_the_schema_is_built_once(client: TestClient) -> None:
    """Rewriting an already-rewritten schema would nest content inside content."""
    app = client.app
    assert isinstance(app, DartsApp)
    assert app.openapi() is app.openapi()


def test_rewriting_twice_changes_nothing(schema: dict[str, Any]) -> None:
    """Belt and braces on the above: the transform is its own fixed point."""
    assert with_error_envelope(dict(schema)) == schema


def test_a_path_item_that_is_not_an_operation_is_untouched() -> None:
    """`parameters` and `summary` are legal path-item keys and have no responses."""
    document = {"paths": {"/x": {"summary": "shared", "parameters": [{"name": "id"}]}}}
    assert with_error_envelope(document)["paths"]["/x"] == {
        "summary": "shared",
        "parameters": [{"name": "id"}],
    }


def test_the_wildcard_status_form_counts_as_a_failure() -> None:
    """`4XX` is legal OpenAPI. FastAPI does not emit it; the rewrite still handles it."""
    document = {"paths": {"/x": {"get": {"responses": {"4XX": {"description": "no"}}}}}}
    responses = with_error_envelope(document)["paths"]["/x"]["get"]["responses"]
    assert responses["4XX"]["content"][JSON]["schema"]["$ref"] == ENVELOPE_REF


def test_an_operation_with_no_responses_still_gets_the_default() -> None:
    document = {"paths": {"/x": {"get": {}}}}
    responses = with_error_envelope(document)["paths"]["/x"]["get"]["responses"]
    assert set(responses) == {"default"}


def test_the_component_refs_point_into_components_not_defs() -> None:
    """Pydantic's default `#/$defs/...` is meaningless in an OpenAPI document."""
    envelope = envelope_schemas()["ErrorEnvelope"]
    assert envelope["properties"]["error"]["$ref"] == "#/components/schemas/ApiError"
    assert "$defs" not in envelope


def test_create_app_builds_the_subclass(app: FastAPI) -> None:
    """The point of the whole module, stated as a test rather than a comment."""
    assert isinstance(app, DartsApp)
    assert "HTTPValidationError" not in str(app.openapi())
