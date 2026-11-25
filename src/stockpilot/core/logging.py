"""One log format for everything the process writes: our events and the libraries' logs."""

import logging
import sys

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from stockpilot.core.config import Settings

# httpx logs every request with its full URL; our clients already log what matters.
_NOISY_LOGGERS = ("httpx", "httpcore")
_HANDLER_NAME = "stockpilot"


def configure_logging(settings: Settings) -> None:
    """Safe to call more than once: the last call wins."""
    # Applied to our events and to the records of standard library loggers.
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="time"),
    ]
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        # Not cached, so tests can capture the events of module-level loggers.
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *_renderer(settings),
            ],
        )
    )
    root = logging.getLogger()
    for old in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(old)
    handler.set_name(_HANDLER_NAME)
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # Uvicorn installs its own handlers: its records go through ours instead.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # One line per request is enough, and ours has the correlation id.
    logging.getLogger("uvicorn.access").disabled = True


def _renderer(settings: Settings) -> list[Processor]:
    if settings.log_format == "console":
        return [structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())]
    return [
        structlog.processors.format_exc_info,
        _google_cloud_fields,
        structlog.processors.JSONRenderer(ensure_ascii=False, default=str),
    ]


def _google_cloud_fields(_: WrappedLogger, __: str, event: EventDict) -> EventDict:
    """The names Cloud Logging reads: 'severity' and 'message'."""
    event["severity"] = event.pop("level").upper()
    event["message"] = event.pop("event")
    return event
