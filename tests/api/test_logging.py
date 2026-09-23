"""Structured logging and the request id that threads through it."""

import io
import logging
import re
import sys
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from darts.api.logging_conf import (
    NO_REQUEST_ID,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    StructuredFormatter,
    configure_logging,
    request_id,
)


def render(record: logging.LogRecord) -> str:
    return StructuredFormatter().format(record)


def make_record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("darts.test", logging.INFO, __file__, 10, message, (), None)
    record.__dict__.update(extra)
    return record


#: A key, then either a JSON-quoted value or a bare run of non-spaces. Parsing
#: the line back is the point: a log nobody can machine-read is prose.
PAIR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def fields(line: str) -> dict[str, str]:
    """Parse one log line back into its pairs, for asserting on structure."""
    assert "\n" not in line, "a record must be one line"
    return dict(PAIR.findall(line))


@pytest.fixture
def emitted() -> Iterator[Callable[[], list[str]]]:
    """Read back the lines exactly as the structured handler wrote them.

    The request id is resolved at format time from a ContextVar, so a record
    re-rendered after its request has finished would report no request at all.
    Capturing what was actually written is the only honest way to assert on it.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield lambda: stream.getvalue().splitlines()
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


def only(lines: list[str], **match: str) -> dict[str, str]:
    """The one emitted line whose fields include `match`."""
    found = [f for f in map(fields, lines) if all(f.get(k) == v for k, v in match.items())]
    assert len(found) == 1, f"expected exactly one {match}, got {len(found)} in {lines}"
    return found[0]


def test_a_record_is_one_line_of_pairs() -> None:
    parsed = fields(render(make_record("boot check complete")))
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "darts.test"
    assert parsed["request_id"] == NO_REQUEST_ID
    assert parsed["msg"] == '"boot check complete"'
    assert parsed["ts"].endswith("Z")


def test_extras_become_fields() -> None:
    parsed = fields(render(make_record("shutdown checkpoint", truncated=True, wal_pages=12)))
    assert parsed["truncated"] == "true"
    assert parsed["wal_pages"] == "12"


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        (True, "true"),
        (False, "false"),
        (None, "null"),
        (3, "3"),
        (1.5, "1.5"),
        ("healthy", "healthy"),
        ("/var/lib/darts/darts.db", "/var/lib/darts/darts.db"),
        ("two words", '"two words"'),
        ('has "quotes"', '"has \\"quotes\\""'),
    ],
)
def test_values_are_quoted_only_when_they_need_to_be(value: object, rendered: str) -> None:
    assert fields(render(make_record("m", field=value)))["field"] == rendered


def test_a_multiline_message_stays_on_one_line() -> None:
    """`docker logs` gets one record per line even when the message has none."""
    line = render(make_record("first\nsecond\nthird"))
    assert "\n" not in line
    assert "\\n" in line


def test_a_traceback_is_folded_into_one_field() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record("unhandled error")
        record.exc_info = sys.exc_info()
    line = render(record)
    assert "\n" not in line
    assert "ValueError: boom" in fields(line)["exc"]


def ours(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if isinstance(h.formatter, StructuredFormatter)]


def test_configure_logging_is_idempotent_and_leaves_other_handlers_alone() -> None:
    """Every `create_app` calls it, and pytest's caplog must survive that."""
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        configure_logging("WARNING")
        configure_logging("DEBUG")
        assert len(ours(root)) == 1, "handlers stacked up"
        assert foreign in root.handlers, "someone else's handler was evicted"
        assert root.level == logging.DEBUG
    finally:
        root.removeHandler(foreign)
        configure_logging("INFO")


def test_uvicorns_own_access_log_is_disabled() -> None:
    """Our middleware logs the same request with an id and a duration."""
    configure_logging("INFO")
    assert logging.getLogger("uvicorn.access").disabled
    assert logging.getLogger("uvicorn.error").propagate


def test_a_request_id_is_generated_and_echoed(client: TestClient) -> None:
    response = client.get("/api/version")
    assert len(response.headers[REQUEST_ID_HEADER]) == 32


def test_a_callers_request_id_is_reused(client: TestClient) -> None:
    response = client.get("/api/version", headers={REQUEST_ID_HEADER: "phone-42"})
    assert response.headers[REQUEST_ID_HEADER] == "phone-42"


@pytest.mark.parametrize(
    ("sent", "echoed"),
    [
        ("trace\r\nX-Evil: yes", "traceX-Evilyes"),
        ("../../etc/passwd", "....etcpasswd"),
        ("!!!", None),
        ("", None),
        ("x" * 200, "x" * 64),
    ],
)
def test_a_hostile_request_id_is_never_echoed_back_raw(
    client: TestClient, sent: str, echoed: str | None
) -> None:
    """It lands in a log line and a response header; neither may be forged."""
    response = client.get("/api/version", headers={REQUEST_ID_HEADER: sent})
    returned = response.headers[REQUEST_ID_HEADER]
    if echoed is None:
        assert len(returned) == 32, "an unusable id should be replaced, not echoed"
    else:
        assert returned == echoed


def test_the_request_id_is_cleared_between_requests(client: TestClient) -> None:
    client.get("/api/version", headers={REQUEST_ID_HEADER: "phone-42"})
    assert request_id.get() == NO_REQUEST_ID


def test_each_request_is_logged_once_with_its_outcome(
    client: TestClient, emitted: Callable[[], list[str]]
) -> None:
    client.get("/api/nope", headers={REQUEST_ID_HEADER: "phone-42"})

    line = only(emitted(), logger="darts.api.request", request_id="phone-42")
    assert line["method"] == "GET"
    assert line["path"] == "/api/nope"
    assert line["status"] == "404"
    assert float(line["duration_ms"]) >= 0


def test_the_id_reaches_logs_emitted_inside_the_request(
    emitted: Callable[[], list[str]],
) -> None:
    """A service logging three layers down still says which request it was."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/api/deep")
    def deep() -> dict[str, bool]:
        logging.getLogger("darts.services.play").info("threw a dart")
        return {"ok": True}

    with TestClient(app) as client:
        client.get("/api/deep", headers={REQUEST_ID_HEADER: "phone-42"})

    line = only(emitted(), logger="darts.services.play")
    assert line["request_id"] == "phone-42"
    assert line["msg"] == '"threw a dart"'


def test_a_request_that_raises_is_still_logged_as_a_500(
    emitted: Callable[[], list[str]],
) -> None:
    """The line is written in a finally, so a crash cannot silence it."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("bug")

    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/api/boom")

    assert only(emitted(), logger="darts.api.request")["status"] == "500"
