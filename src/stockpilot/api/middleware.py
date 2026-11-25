import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger(__name__)

HEADER = "X-Correlation-ID"
# It is written to the logs as it comes: anything else could forge log content.
_VALID_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class RequestLogMiddleware:
    """Gives each request a correlation id and logs its result.

    Every event logged while the request runs carries the id, also those of
    the external API clients. A caller can send its own in the header, to
    follow one operation across systems.

    Plain ASGI and not BaseHTTPMiddleware, which runs the endpoint in another
    task and complicates context variables.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = _correlation_id(Headers(scope=scope).get(HEADER))
        status: int | None = None

        async def send_with_header(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[HEADER] = correlation_id
            await send(message)

        with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
            start = time.perf_counter()
            request = {"method": scope["method"], "path": scope["path"]}
            try:
                await self.app(scope, receive, send_with_header)
            except Exception:
                logger.exception("request_failed", **request, duration_ms=_elapsed_ms(start))
                if status is None:
                    # Answered here: the outer 500 handler would lose the header.
                    response = JSONResponse({"detail": "Internal server error"}, status_code=500)
                    await response(scope, receive, send_with_header)
                raise  # so the server and the tests still see it
            logger.info("request", **request, status=status, duration_ms=_elapsed_ms(start))


def _correlation_id(received: str | None) -> str:
    if received is not None and _VALID_ID.fullmatch(received):
        return received
    return uuid4().hex


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
