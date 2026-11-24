"""FastAPI dependencies. Tests replace them with app.dependency_overrides."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from stockpilot.core.database import get_session
from stockpilot.services.catalog import CatalogService
from stockpilot.services.dealers import DealerService
from stockpilot.services.units import UnitService

SessionDep = Annotated[Session, Depends(get_session)]


def get_dealer_service(session: SessionDep) -> DealerService:
    return DealerService(session)


DealerServiceDep = Annotated[DealerService, Depends(get_dealer_service)]


def get_catalog_service(session: SessionDep) -> CatalogService:
    return CatalogService(session)


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]


def get_unit_service(session: SessionDep) -> UnitService:
    return UnitService(session)


UnitServiceDep = Annotated[UnitService, Depends(get_unit_service)]
