"""Translate domain errors to HTTP responses, in one place.

Services raise domain errors without knowing about HTTP; routes don't catch
them. This keeps both layers free of status-code logic.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from stockpilot.domain.errors import (
    AlreadyExistsError,
    ConflictError,
    DomainError,
    ExternalServiceError,
    InvalidStatusTransitionError,
    InvalidVinError,
    NotFoundError,
)

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    AlreadyExistsError: status.HTTP_409_CONFLICT,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidStatusTransitionError: status.HTTP_409_CONFLICT,
    InvalidVinError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 502, not 500: the failure is in a service behind this API.
    ExternalServiceError: status.HTTP_502_BAD_GATEWAY,
}


def status_for(error: DomainError) -> int:
    for error_type, code in STATUS_BY_ERROR.items():
        if isinstance(error, error_type):
            return code
    # A domain error without a mapping is a bug in this table, not a client error.
    return status.HTTP_500_INTERNAL_SERVER_ERROR


async def _handle_domain_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, DomainError)
    # Same shape as FastAPI's own HTTPException responses.
    return JSONResponse(status_code=status_for(error), content={"detail": str(error)})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
