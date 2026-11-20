from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from stockpilot.core.database import get_session
from stockpilot.repositories.health import is_database_available
from stockpilot.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


def check_database(session: Annotated[Session, Depends(get_session)]) -> bool:
    """Dependency wrapper so tests can replace the database check."""
    return is_database_available(session)


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
def health(
    response: Response,
    database_ok: Annotated[bool, Depends(check_database)],
) -> HealthResponse:
    if database_ok:
        return HealthResponse(status="ok", database="ok")
    # 503 lets load balancers and Cloud Run stop routing traffic to this instance.
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="degraded", database="unavailable")
