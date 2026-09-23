"""Making the published schema tell the truth about failures.

FastAPI documents the error case of every route that takes a parameter as its
own `HTTPValidationError`. No `/api` route has returned that since #16 replaced
the lot with a single envelope -- and because the error handlers build their
responses with a raw `JSONResponse`, the schema generator never sees the real
shape. The schema was confidently wrong, and `frontend/src/api/schema.d.ts`,
generated from it, inherited the lie.

#21's frontend rule is that no request or response type is hand-written, which
that lie makes impossible to keep for errors. The fix could have been
`responses=` on all twenty-one routes across five merged tickets; instead the
substitution happens once, here. Every documented failure points at
`ErrorEnvelope`, and so does a `default` response, because a route that
enumerates only its 422 can still answer 500 and the client has to be ready for
it.

This is a documentation change only. Not a byte of what the server sends moves;
`darts.api.errors` was already sending this.
"""

from typing import Any

from fastapi import FastAPI

from darts.api.errors import ErrorEnvelope

#: Where `ErrorEnvelope` and its dependencies land, and how they refer to each
#: other. Pydantic's default `#/$defs/...` template is meaningless in an
#: OpenAPI document, where every schema lives under `components`.
COMPONENTS = "#/components/schemas"
REF_TEMPLATE = f"{COMPONENTS}/{{model}}"
ENVELOPE_REF = f"{COMPONENTS}/{ErrorEnvelope.__name__}"

JSON = "application/json"

#: FastAPI's account of an error, generated per-route and never sent. Dropped
#: once nothing references it, so that a regenerated client cannot offer a type
#: no response will ever satisfy.
SUPERSEDED = ("HTTPValidationError", "ValidationError")

#: The keys of a path item that are operations. The rest -- `summary`,
#: `parameters`, `servers` -- are not, and iterating the item blindly would
#: try to give `parameters` a set of responses.
METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

DEFAULT_DESCRIPTION = "Any other failure, in the standard error envelope."


def error_content() -> dict[str, Any]:
    return {"content": {JSON: {"schema": {"$ref": ENVELOPE_REF}}}}


def envelope_schemas() -> dict[str, Any]:
    """`ErrorEnvelope` and everything it names, flattened for `components`."""
    schema = ErrorEnvelope.model_json_schema(ref_template=REF_TEMPLATE)
    defs: dict[str, Any] = schema.pop("$defs", {})
    return {**defs, ErrorEnvelope.__name__: schema}


def _is_failure(status: str) -> bool:
    """Is this response key one the envelope describes?

    `default` is, by definition. Otherwise anything from 400 up -- including
    the `4XX` wildcard form, which is legal OpenAPI even though FastAPI does
    not currently emit it.
    """
    if status == "default":
        return True
    head = status[0]
    return head.isdigit() and int(head) >= 4


def _rewrite(operation: dict[str, Any]) -> None:
    responses: dict[str, Any] = operation.setdefault("responses", {})
    for status, response in responses.items():
        if _is_failure(status):
            # The description is the route author's own words -- "Degraded,
            # unwritable, or not yet started" -- and worth more than anything
            # generated here, so only the content is replaced.
            response.update(error_content())
    responses["default"] = {"description": DEFAULT_DESCRIPTION, **error_content()}


def with_error_envelope(schema: dict[str, Any]) -> dict[str, Any]:
    """Point every failure in `schema` at `ErrorEnvelope`, in place."""
    schemas: dict[str, Any] = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(envelope_schemas())

    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method in METHODS:
                _rewrite(operation)

    for superseded in SUPERSEDED:
        schemas.pop(superseded, None)
    return schema


class DartsApp(FastAPI):
    """A `FastAPI` whose schema describes the errors this app actually sends.

    Subclassed rather than monkey-patched `app.openapi`: assigning over a bound
    method is the documented recipe but not something strict mypy will accept
    without an ignore, and there are none in this codebase.
    """

    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            self.openapi_schema = with_error_envelope(super().openapi())
        return self.openapi_schema
