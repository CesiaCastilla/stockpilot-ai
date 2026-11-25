"""Behavior shared by every external API client. respx blocks the real network."""

import httpx
import pytest
import respx
from pydantic import BaseModel, TypeAdapter
from structlog.testing import capture_logs

from stockpilot.core.config import Settings
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.integrations.http import JsonApi, build_http_client, build_json_api

BASE_URL = "https://api.example.test"


class Thing(BaseModel):
    name: str


THING = TypeAdapter(Thing)
OK = httpx.Response(200, json={"name": "x", "field_added_later": 1})


class Sleeps(list[float]):
    """Records the waits instead of waiting."""

    def __call__(self, seconds: float) -> None:
        self.append(seconds)


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def api(sleeps: Sleeps) -> JsonApi:
    return JsonApi(
        httpx.Client(), service="Example", base_url=f"{BASE_URL}/", max_attempts=3, sleep=sleeps
    )


def test_returns_the_validated_response(
    api: JsonApi, sleeps: Sleeps, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{BASE_URL}/things", params={"q": "Río Cuarto", "max": 1})
    route.return_value = OK

    assert api.get("/things", THING, {"q": "Río Cuarto", "max": 1}) == Thing(name="x")
    assert route.call_count == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.ReadTimeout("slow"), id="read-timeout"),
        pytest.param(httpx.ConnectTimeout("slow"), id="connect-timeout"),
        pytest.param(httpx.ConnectError("refused"), id="connection-refused"),
        pytest.param(httpx.RemoteProtocolError("cut"), id="connection-cut"),
        *[pytest.param(httpx.Response(s), id=str(s)) for s in (429, 500, 502, 503, 504)],
    ],
)
def test_transient_failures_are_retried(
    api: JsonApi,
    sleeps: Sleeps,
    respx_mock: respx.MockRouter,
    failure: httpx.Response | Exception,
) -> None:
    route = respx_mock.get(f"{BASE_URL}/things")
    route.side_effect = [failure, failure, OK]

    with capture_logs() as logs:
        assert api.get("/things", THING) == Thing(name="x")

    assert route.call_count == 3
    assert [(e["event"], e["log_level"], e["attempt"]) for e in logs] == [
        ("external_api_retry", "warning", 1),
        ("external_api_retry", "warning", 2),
    ]


def test_waits_longer_between_each_retry(
    api: JsonApi, sleeps: Sleeps, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{BASE_URL}/things").side_effect = [
        httpx.Response(503),
        httpx.Response(503),
        OK,
    ]

    api.get("/things", THING)

    # 0.5 and 1 seconds, plus up to 0.5 of jitter.
    first, second = sleeps
    assert 0.5 <= first < 1.0
    assert 1.0 <= second < 1.5


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        pytest.param(httpx.ReadTimeout("slow"), "timeout", id="timeout"),
        pytest.param(httpx.ConnectError("refused"), "connection failed", id="connection"),
        pytest.param(httpx.Response(503), "HTTP 503", id="503"),
    ],
)
def test_gives_up_after_the_last_attempt(
    api: JsonApi,
    sleeps: Sleeps,
    respx_mock: respx.MockRouter,
    failure: httpx.Response | Exception,
    reason: str,
) -> None:
    route = respx_mock.get(f"{BASE_URL}/things")
    route.side_effect = [failure] * 3

    with capture_logs() as logs, pytest.raises(ExternalServiceError) as caught:
        api.get("/things", THING)

    assert str(caught.value) == f"Example is not available: {reason}"
    assert route.call_count == 3
    assert len(sleeps) == 2  # no wait after the last attempt
    assert logs[-1]["event"] == "external_api_failed"
    assert logs[-1]["service"] == "Example"


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 501])
def test_other_http_errors_are_not_retried(
    api: JsonApi, sleeps: Sleeps, respx_mock: respx.MockRouter, status: int
) -> None:
    route = respx_mock.get(f"{BASE_URL}/things")
    route.return_value = httpx.Response(status)

    with pytest.raises(ExternalServiceError, match=f"HTTP {status}"):
        api.get("/things", THING)

    assert route.call_count == 1


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"<html>maintenance</html>", id="not-json"),
        pytest.param(b"", id="empty"),
        pytest.param(b'{"title": "x"}', id="missing-field"),
        pytest.param(b'{"name": 5}', id="wrong-type"),
        pytest.param(b"[]", id="wrong-shape"),
    ],
)
def test_unexpected_responses_are_not_retried(
    api: JsonApi, respx_mock: respx.MockRouter, content: bytes
) -> None:
    route = respx_mock.get(f"{BASE_URL}/things")
    route.return_value = httpx.Response(200, content=content)

    with pytest.raises(ExternalServiceError, match="Example is not available: unexpected response"):
        api.get("/things", THING)

    assert route.call_count == 1


def test_http_client_has_the_configured_timeouts() -> None:
    settings = Settings(http_timeout_seconds=1.5, http_connect_timeout_seconds=0.5)

    with build_http_client(settings) as http:
        assert http.timeout == httpx.Timeout(1.5, connect=0.5)


# --- Failing fast -------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cooling_api(sleeps: Sleeps, clock: Clock) -> JsonApi:
    return JsonApi(
        httpx.Client(),
        service="Example",
        base_url=BASE_URL,
        max_attempts=2,
        failure_cooldown=60,
        sleep=sleeps,
        clock=clock,
    )


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.ConnectError("refused"), id="connection"),
        pytest.param(httpx.ReadTimeout("slow"), id="timeout"),
        pytest.param(httpx.Response(503), id="503"),
        pytest.param(httpx.Response(429), id="rate-limit"),
    ],
)
def test_api_that_is_down_is_not_asked_again_for_a_while(
    cooling_api: JsonApi,
    clock: Clock,
    respx_mock: respx.MockRouter,
    failure: httpx.Response | Exception,
) -> None:
    route = respx_mock.get(f"{BASE_URL}/things")
    route.side_effect = [failure, failure, OK]
    with pytest.raises(ExternalServiceError):
        cooling_api.get("/things", THING)

    clock.now += 59.9
    with pytest.raises(ExternalServiceError, match="failed recently, not asked again yet"):
        cooling_api.get("/things", THING)
    assert route.call_count == 2  # only the two attempts of the first call

    clock.now += 0.1  # exactly 60 seconds: asked again
    assert cooling_api.get("/things", THING) == Thing(name="x")


def test_a_retry_that_works_does_not_start_the_wait(
    cooling_api: JsonApi, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{BASE_URL}/things").side_effect = [httpx.Response(503), OK, OK]

    for _ in range(2):
        assert cooling_api.get("/things", THING) == Thing(name="x")


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(404), id="404"),
        pytest.param(httpx.Response(200, content=b"[]"), id="unexpected-response"),
    ],
)
def test_api_that_answers_is_not_down(
    cooling_api: JsonApi, respx_mock: respx.MockRouter, answer: httpx.Response
) -> None:
    respx_mock.get(f"{BASE_URL}/things").side_effect = [answer, OK]
    with pytest.raises(ExternalServiceError):
        cooling_api.get("/things", THING)

    assert cooling_api.get("/things", THING) == Thing(name="x")


def test_one_api_down_does_not_affect_another(
    sleeps: Sleeps, clock: Clock, respx_mock: respx.MockRouter
) -> None:
    settings = Settings(http_max_attempts=1)
    http = httpx.Client()
    first = build_json_api(settings, http, service="First", base_url="https://first.test")
    second = build_json_api(settings, http, service="Second", base_url="https://second.test")
    failing = respx_mock.get("https://first.test/things").respond(503)
    respx_mock.get("https://second.test/things").return_value = OK

    for _ in range(2):
        with pytest.raises(ExternalServiceError):
            first.get("/things", THING)

    assert failing.call_count == 1  # the cooldown comes from the settings
    assert second.get("/things", THING) == Thing(name="x")
