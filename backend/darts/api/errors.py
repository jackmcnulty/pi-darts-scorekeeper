"""The one error shape every `/api` route returns.

```json
{"error": {"code": "not_found", "message": "...", "detail": null}}
```

`code` is a closed vocabulary a client can branch on, `message` is a sentence
for a human, and `detail` carries whatever machine-readable extra that code
implies -- pydantic's per-field errors for `validation_error`, the reason a
domain rule refused for `conflict`, the health report for `service_unavailable`.

The mapping from the two error hierarchies lands here rather than in #17/#18 so
that an endpoint only ever has to raise: `NotFoundError` is a 404 wherever it
comes from, and no route handler repeats that decision.

`ErrorEnvelope` below is that shape as a model. Nothing serves it directly --
the handlers all return `JSONResponse` -- but `envelope()` builds through it, so
there is still exactly one spelling, and #21's `darts.api.openapi` publishes it
as the error schema every route points at. Before that the generated TypeScript
client described FastAPI's `HTTPValidationError`, which no route has returned
since #16.
"""

import logging
import re
import sqlite3
from collections.abc import Callable
from enum import StrEnum
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from darts.repo.errors import DuplicateNameError, InvalidMatchError, NotFoundError, RepoError
from darts.services.errors import ServiceError

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Everything `error.code` is allowed to be."""

    VALIDATION_ERROR = "validation_error"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL = "internal"


#: Statuses worth naming precisely. Anything else falls back to the class of
#: the status: a 4xx blamed on the request, a 5xx blamed on us.
_BY_STATUS = {
    400: ErrorCode.INVALID_REQUEST,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}

_TRAILING_ERROR = re.compile(r"Error$")
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


class ApiError(BaseModel):
    """What went wrong: a code to branch on, a sentence, and the specifics."""

    code: ErrorCode = Field(description="A closed vocabulary; branch on this, not on the message.")
    message: str = Field(description="One sentence, safe to show a human.")
    detail: Any = Field(
        default=None,
        description=(
            "Whatever the code implies. Pydantic's per-field errors for "
            "`validation_error`; `{'reason': ...}` for a refused domain rule, which "
            "is how several distinct 409s are told apart without matching on prose; "
            "the health report for `service_unavailable`; absent for the rest."
        ),
    )


class ErrorEnvelope(BaseModel):
    """The body of every failed `/api` response, whatever the status."""

    error: ApiError


def code_for_status(status: int) -> ErrorCode:
    if status in _BY_STATUS:
        return _BY_STATUS[status]
    return ErrorCode.INVALID_REQUEST if status < 500 else ErrorCode.INTERNAL


def envelope(
    status: int, code: ErrorCode, message: str, detail: Any = None, headers: Any = None
) -> JSONResponse:
    """Build the response. The only place this shape is spelled out.

    The dump is deliberately python-mode, not JSON-mode: `detail` is typed
    `Any` and already JSON-ready when it arrives -- `jsonable_encoder` for
    pydantic's per-field errors, plain dicts everywhere else -- so it passes
    through untouched rather than meeting a second encoder with its own
    opinions. `ErrorCode` is a `StrEnum`, so `json.dumps` writes its value.
    """
    body = ErrorEnvelope(error=ApiError(code=code, message=message, detail=detail))
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


def _reason(exc: Exception) -> str:
    """`LegCompleteError` -> `leg_complete`: a discriminator for one code.

    Several distinct refusals share the 409 `conflict` code, and a client that
    wants to tell "that leg is already won" from "there is nothing to undo"
    should not have to match on the message text.
    """
    name = _TRAILING_ERROR.sub("", type(exc).__name__)
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def _domain(exc: Exception, status: int, code: ErrorCode) -> JSONResponse:
    return envelope(status, code, str(exc) or type(exc).__doc__ or "", {"reason": _reason(exc)})


def http_exception(request: Request, exc: HTTPException) -> Response:
    """Starlette's own 404s and 405s, and any `raise HTTPException` we make.

    A string `detail` is the message -- that is how FastAPI is written and how
    everyone raises it. A structured one is passed through as `detail`, with
    the standard reason phrase supplying the message.
    """
    phrase = HTTPStatus(exc.status_code).phrase
    structured = not isinstance(exc.detail, str)
    return envelope(
        exc.status_code,
        code_for_status(exc.status_code),
        phrase if structured else exc.detail,
        exc.detail if structured else None,
        headers=exc.headers,
    )


def validation_error(request: Request, exc: RequestValidationError) -> Response:
    """422 with pydantic's per-field errors intact under `detail`.

    #17 asserts on `loc` to prove a malformed payload was refused for the
    right field, so the errors are encoded rather than flattened to prose.
    """
    return envelope(
        422,
        ErrorCode.VALIDATION_ERROR,
        "Request validation failed",
        jsonable_encoder(exc.errors()),
    )


def not_found(request: Request, exc: NotFoundError) -> Response:
    return _domain(exc, 404, ErrorCode.NOT_FOUND)


def duplicate_name(request: Request, exc: DuplicateNameError) -> Response:
    return _domain(exc, 409, ErrorCode.CONFLICT)


def invalid_match(request: Request, exc: InvalidMatchError) -> Response:
    return _domain(exc, 422, ErrorCode.VALIDATION_ERROR)


def repo_error(request: Request, exc: RepoError) -> Response:
    """Any other addressing failure: the request named something unusable."""
    return _domain(exc, 400, ErrorCode.INVALID_REQUEST)


def service_error(request: Request, exc: ServiceError) -> Response:
    """Every service error is one legal-but-refused situation, so every one is a 409."""
    return _domain(exc, 409, ErrorCode.CONFLICT)


def database_error(request: Request, exc: sqlite3.Error) -> Response:
    """The database is there but would not serve this request.

    A locked or read-only database is a condition of the box, not a fault in
    the request, and it is the same thing `/api/healthz` reports as degraded.
    """
    logger.error("database unavailable", exc_info=exc)
    return envelope(
        503,
        ErrorCode.SERVICE_UNAVAILABLE,
        "The database is unavailable",
        {"reason": type(exc).__name__},
    )


def unhandled(request: Request, exc: Exception) -> Response:
    """A bug. The traceback goes to the log; the client gets none of it."""
    logger.exception("unhandled error", exc_info=exc)
    return envelope(500, ErrorCode.INTERNAL, "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    """Register every handler. Starlette dispatches on the most derived class."""
    handlers: list[tuple[type[Exception], Callable[[Request, Any], Response]]] = [
        (HTTPException, http_exception),
        (RequestValidationError, validation_error),
        (NotFoundError, not_found),
        (DuplicateNameError, duplicate_name),
        (InvalidMatchError, invalid_match),
        (RepoError, repo_error),
        (ServiceError, service_error),
        (sqlite3.Error, database_error),
        (Exception, unhandled),
    ]
    for exception, handler in handlers:
        app.add_exception_handler(exception, handler)
