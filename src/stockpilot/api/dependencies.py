"""FastAPI dependencies. Tests replace them with app.dependency_overrides."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from stockpilot.core.database import get_session
from stockpilot.services.dealers import DealerService

SessionDep = Annotated[Session, Depends(get_session)]


def get_dealer_service(session: SessionDep) -> DealerService:
    return DealerService(session)


DealerServiceDep = Annotated[DealerService, Depends(get_dealer_service)]
