import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog

from stockpilot.core.config import Settings
from stockpilot.core.logging import configure_logging
from stockpilot.main import create_app


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    yield
    configure_logging(Settings())


def _configure(**settings: Any) -> None:
    configure_logging(Settings(**settings))


def _events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_event_is_one_json_line_with_cloud_logging_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure(log_format="json")
    before = datetime.now(UTC)

    structlog.get_logger("stockpilot.test").warning("dealer_not_located", city="Río Cuarto")

    (event,) = _events(capsys)
    assert before <= datetime.fromisoformat(event.pop("time")) <= datetime.now(UTC)
    assert event == {
        "severity": "WARNING",
        "message": "dealer_not_located",
        "city": "Río Cuarto",
        "logger": "stockpilot.test",
    }


def test_text_is_written_without_escapes(capsys: pytest.CaptureFixture[str]) -> None:
    _configure(log_format="json")

    structlog.get_logger().info("x", city="Río Cuarto")

    assert "Río Cuarto" in capsys.readouterr().out


def test_library_logs_have_the_same_format_and_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure(log_format="json")

    with structlog.contextvars.bound_contextvars(correlation_id="abc"):
        logging.getLogger("some.library").error("went %s", "wrong")

    (event,) = _events(capsys)
    assert event["message"] == "went wrong"
    assert event["severity"] == "ERROR"
    assert event["logger"] == "some.library"
    assert event["correlation_id"] == "abc"


def test_exception_is_part_of_the_same_line(capsys: pytest.CaptureFixture[str]) -> None:
    _configure(log_format="json")

    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger().exception("failed")

    (event,) = _events(capsys)
    assert event["severity"] == "ERROR"
    assert "ValueError: boom" in event["exception"]


def test_log_level(capsys: pytest.CaptureFixture[str]) -> None:
    _configure(log_format="json", log_level="WARNING")

    for logger in (structlog.get_logger("ours"), logging.getLogger("library")):
        logger.info("hidden")
        logger.warning("shown")

    assert [e["message"] for e in _events(capsys)] == ["shown", "shown"]


def test_configuring_twice_does_not_duplicate_lines(capsys: pytest.CaptureFixture[str]) -> None:
    _configure(log_format="json")
    _configure(log_format="json")

    structlog.get_logger().info("once")

    assert len(_events(capsys)) == 1


@pytest.mark.parametrize("name", ["uvicorn", "uvicorn.error"])
def test_uvicorn_logs_go_through_ours(capsys: pytest.CaptureFixture[str], name: str) -> None:
    # What uvicorn does before loading the app.
    logger = logging.getLogger(name)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False

    _configure(log_format="json")
    logger.warning("started")

    captured = capsys.readouterr()
    assert [json.loads(line)["message"] for line in captured.out.splitlines()] == ["started"]
    assert captured.err == ""  # uvicorn's own handler is gone


def test_repeated_or_noisy_logs_are_silenced(capsys: pytest.CaptureFixture[str]) -> None:
    logging.getLogger("uvicorn.access").addHandler(logging.StreamHandler())
    _configure(log_format="json")

    logging.getLogger("uvicorn.access").warning("GET / 200")
    logging.getLogger("httpx").info("HTTP Request: GET https://example.test")

    captured = capsys.readouterr()
    assert captured.out + captured.err == ""


def test_the_application_configures_it(capsys: pytest.CaptureFixture[str]) -> None:
    create_app(Settings(log_format="json", log_level="ERROR"))

    structlog.get_logger().warning("hidden")
    structlog.get_logger().error("shown")

    assert [e["message"] for e in _events(capsys)] == ["shown"]


def test_console_format_is_not_json(capsys: pytest.CaptureFixture[str]) -> None:
    _configure(log_format="console")

    structlog.get_logger().info("hello", city="Tandil")

    out = capsys.readouterr().out
    assert "hello" in out
    assert "city=Tandil" in out
    assert not out.startswith("{")
