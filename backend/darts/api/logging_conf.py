"""One line per event, and a request id threaded through all of them.

`docker logs` is the only log viewer this box will ever have, so every record
is a single line of `key=value` pairs: greppable by eye, parseable by machine,
and impossible to interleave into nonsense when two requests log at once. A
traceback is folded into one quoted `exc=` value for the same reason.

The request id is held in a ContextVar rather than passed around, so a log call
deep inside a service still carries the request that caused it.
"""

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"

#: What the formatter prints for a record emitted outside any request.
NO_REQUEST_ID = "-"

_MAX_REQUEST_ID = 64
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")

#: Values made only of these need no quoting, which keeps a line readable.
_BARE = re.compile(r"\A[A-Za-z0-9._:/@=-]+\Z")

#: Attributes every LogRecord has; anything else came from `extra=`.
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
    "taskName",
}

#: Set on handlers this module installs, so reconfiguring replaces its own
#: handler without evicting one pytest's caplog (or anything else) added.
_INSTALLED = "_darts_handler"

request_id: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)

logger = logging.getLogger("darts.api.request")


def _render(value: object) -> str:
    """A single-line, unambiguous rendering of one field value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    return text if _BARE.fullmatch(text) else json.dumps(text)


class StructuredFormatter(logging.Formatter):
    """`ts=... level=... logger=... request_id=... msg=... <extras>`."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        fields: dict[str, object] = {
            "ts": f"{stamp}.{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id.get(),
            "msg": record.getMessage(),
        }
        fields.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD})
        if record.exc_info:
            fields["exc"] = self.formatException(record.exc_info)
        return " ".join(f"{key}={_render(value)}" for key, value in fields.items())


def configure_logging(level: str = "INFO") -> None:
    """Send every logger through one structured handler on stdout.

    Uvicorn installs handlers of its own at startup; they are cleared and left
    propagating so its lines get the same treatment as ours. Its access log is
    disabled outright because `RequestContextMiddleware` already logs one line
    per request, with the request id and duration that uvicorn's does not have.
    """
    root = logging.getLogger()
    for existing in [h for h in root.handlers if getattr(h, _INSTALLED, False)]:
        root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    setattr(handler, _INSTALLED, True)
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


def _clean_id(candidate: str | None) -> str:
    """Reuse a caller's request id, but never echo one back unfiltered.

    The value lands in a log line and in a response header, so it is stripped
    to characters that cannot break either and truncated; anything left empty
    gets a fresh id instead.
    """
    if candidate is None:
        return uuid.uuid4().hex
    return _UNSAFE_ID.sub("", candidate)[:_MAX_REQUEST_ID] or uuid.uuid4().hex


class RequestContextMiddleware:
    """Tag each request with an id, echo it back, and log how it went.

    Written against the ASGI interface rather than as a `BaseHTTPMiddleware`
    so the ContextVar is set on the task that actually runs the endpoint, and
    so the status code is read from the response start message even when the
    endpoint raised and an exception handler produced the response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        identifier = _clean_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        token = request_id.set(identifier)
        started = time.perf_counter()
        # Nothing sent at all means the app raised; the error middleware turns
        # that into a 500, so that is what this line should report.
        status = 500

        async def tagged(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = identifier
            await send(message)

        try:
            await self.app(scope, receive, tagged)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round(elapsed, 1),
                },
            )
            request_id.reset(token)
