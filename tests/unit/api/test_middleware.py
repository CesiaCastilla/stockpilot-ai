"""Correlation id and request log, over a small app with the real middleware."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.contextvars import get_contextvars, merge_contextvars
from structlog.testing import capture_logs

from stockpilot.api.middleware import HEADER, RequestLogMiddleware
from stockpilot.core.config import Settings
from stockpilot.main import create_app

logger = structlog.get_logger(__name__)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)

    @app.get("/sync/{item}")
    def sync_endpoint(item: str) -> dict[str, str]:
        logger.info("inside")  # runs in a worker thread, like our endpoints
        return {"item": item}

    @app.get("/async")
    async def async_endpoint() -> None:
        logger.info("inside")

    @app.get("/broken")
    def broken() -> None:
        raise RuntimeError("secret detail")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _get(client: TestClient, path: str, **kwargs: Any) -> tuple[Any, list[Any]]:
    with capture_logs(processors=[merge_contextvars]) as logs:
        response = client.get(path, **kwargs)
    return response, list(logs)


def test_request_is_logged(client: TestClient) -> None:
    response, logs = _get(client, "/sync/x?token=abc")

    event = logs[-1]
    assert event["duration_ms"] >= 0
    assert event | {"duration_ms": 0} == {
        "event": "request",
        "log_level": "info",
        "method": "GET",
        "path": "/sync/x",  # without the query string, which can carry data
        "status": 200,
        "duration_ms": 0,
        "correlation_id": response.headers[HEADER],
    }


@pytest.mark.parametrize("path", ["/sync/x", "/async"])
def test_events_inside_the_request_have_its_id(client: TestClient, path: str) -> None:
    response, logs = _get(client, path)

    assert [e["event"] for e in logs] == ["inside", "request"]
    assert {e["correlation_id"] for e in logs} == {response.headers[HEADER]}


def test_each_request_has_its_own_id(client: TestClient) -> None:
    first, second = client.get("/async"), client.get("/async")

    assert first.headers[HEADER] != second.headers[HEADER]
    assert len(first.headers[HEADER]) == 32


def test_id_does_not_outlive_the_request() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        assert "correlation_id" in get_contextvars()
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message: Any) -> None: ...

    async def call() -> dict[str, Any]:
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        await RequestLogMiddleware(app)(scope, None, send)  # type: ignore[arg-type]
        return get_contextvars()

    assert asyncio.run(call()) == {}


def test_caller_can_send_its_own_id(client: TestClient) -> None:
    response, logs = _get(client, "/async", headers={HEADER: "n8n-run_42"})

    assert response.headers[HEADER] == "n8n-run_42"
    assert {e["correlation_id"] for e in logs} == {"n8n-run_42"}


@pytest.mark.parametrize(
    "received",
    [
        pytest.param("", id="empty"),
        pytest.param("a" * 65, id="too-long"),
        pytest.param('x","severity":"ERROR', id="json-injection"),
        pytest.param("two words", id="space"),
    ],
)
def test_unsafe_id_is_replaced(client: TestClient, received: str) -> None:
    response = client.get("/async", headers={HEADER: received})

    assert len(response.headers[HEADER]) == 32
    assert response.headers[HEADER] != received


def test_longest_id_is_accepted(client: TestClient) -> None:
    assert client.get("/async", headers={HEADER: "a" * 64}).headers[HEADER] == "a" * 64


def test_client_errors_are_logged_with_their_status(client: TestClient) -> None:
    response, logs = _get(client, "/unknown")

    assert logs[-1]["status"] == 404
    assert HEADER in response.headers


def test_unexpected_error_is_logged_and_answered_with_the_id(client: TestClient) -> None:
    response, logs = _get(client, "/broken")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}  # nothing internal leaks
    (event,) = logs
    assert event["event"] == "request_failed"
    assert event["log_level"] == "error"
    assert event["exc_info"]
    assert event["correlation_id"] == response.headers[HEADER]


def test_unexpected_error_still_reaches_the_server(client: TestClient) -> None:
    with TestClient(client.app) as strict, pytest.raises(RuntimeError):
        strict.get("/broken")


def test_response_already_started_is_not_answered_twice() -> None:
    sent: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("cut in the middle")

    async def send(message: Any) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    with pytest.raises(RuntimeError):
        asyncio.run(RequestLogMiddleware(app)(scope, None, send))  # type: ignore[arg-type]

    assert [m["type"] for m in sent] == ["http.response.start"]


def test_the_application_uses_it() -> None:
    with TestClient(create_app(Settings())) as app_client:
        assert HEADER in app_client.get("/docs").headers
