"""What every external API client shares: timeout, retries, validation and errors."""

import time
from collections.abc import Callable, Mapping

import httpx
import structlog
from pydantic import TypeAdapter, ValidationError
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from stockpilot.core.config import Settings
from stockpilot.domain.errors import ExternalServiceError

logger = structlog.get_logger(__name__)

_RETRYABLE_STATUSES = frozenset(
    {
        httpx.codes.TOO_MANY_REQUESTS,
        httpx.codes.INTERNAL_SERVER_ERROR,
        httpx.codes.BAD_GATEWAY,
        httpx.codes.SERVICE_UNAVAILABLE,
        httpx.codes.GATEWAY_TIMEOUT,
    }
)


def build_http_client(settings: Settings) -> httpx.Client:
    """One client for all the integrations: it is thread-safe and reuses connections."""
    # Shorter to connect: it applies to each address of the host, so with a
    # broken IPv6 route this is how long it takes to fall back to IPv4.
    timeout = httpx.Timeout(
        settings.http_timeout_seconds, connect=settings.http_connect_timeout_seconds
    )
    return httpx.Client(timeout=timeout)


class JsonApi:
    """Read requests to one external JSON API.

    Every failure leaves as ExternalServiceError, so callers never depend on
    httpx, tenacity or the shape of the response.
    """

    def __init__(
        self,
        http: httpx.Client,
        *,
        service: str,
        base_url: str,
        max_attempts: int,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._service = service
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._sleep = sleep

    def get[T](
        self,
        path: str,
        response_type: TypeAdapter[T],
        params: Mapping[str, str | int] | None = None,
    ) -> T:
        return self._request(
            path, response_type, lambda: self._http.get(self._base_url + path, params=params)
        )

    def post[T](self, path: str, response_type: TypeAdapter[T], body: object) -> T:
        """Only for queries sent as POST: a retry could repeat a write."""
        return self._request(
            path, response_type, lambda: self._http.post(self._base_url + path, json=body)
        )

    def _request[T](
        self, path: str, response_type: TypeAdapter[T], send: Callable[[], httpx.Response]
    ) -> T:
        try:
            response = self._send_with_retries(send)
            # Straight from the bytes: going through float would lose decimals.
            return response_type.validate_json(response.content)
        except httpx.HTTPError as error:
            failure = ExternalServiceError(self._service, _reason(error))
            cause: Exception = error
        except ValidationError as error:
            # Not retried: the API answered, but its contract changed.
            failure = ExternalServiceError(self._service, "unexpected response")
            cause = error
        logger.error("external_api_failed", service=self._service, path=path, error=str(cause))
        raise failure from cause

    def _send_with_retries(self, send: Callable[[], httpx.Response]) -> httpx.Response:
        retrying = Retrying(
            retry=retry_if_exception(_is_transient),
            stop=stop_after_attempt(self._max_attempts),
            # Exponential, so a struggling API gets room to recover; with
            # jitter, so clients that failed together don't retry together.
            wait=wait_exponential_jitter(multiplier=0.5, max=4, jitter=0.5),
            sleep=self._sleep,
            before_sleep=self._log_retry,
            reraise=True,
        )
        return retrying(lambda: send().raise_for_status())

    def _log_retry(self, state: RetryCallState) -> None:
        error = state.outcome.exception() if state.outcome else None
        logger.warning(
            "external_api_retry",
            service=self._service,
            attempt=state.attempt_number,
            error=str(error),
        )


def _is_transient(error: BaseException) -> bool:
    """Failures that may not happen again. The rest of the 4xx would fail every time."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in _RETRYABLE_STATUSES
    return isinstance(error, httpx.TransportError)


def _reason(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    return "connection failed"
