"""FastAPI dependencies. Tests replace them with app.dependency_overrides."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from stockpilot.core.config import Settings
from stockpilot.core.database import get_session
from stockpilot.domain.coverage import CoveragePolicy
from stockpilot.services.catalog import CatalogService
from stockpilot.services.coverage import CoverageService
from stockpilot.services.dealers import DealerService
from stockpilot.services.transfers import TransferService
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


def get_transfer_service(session: SessionDep) -> TransferService:
    return TransferService(session)


TransferServiceDep = Annotated[TransferService, Depends(get_transfer_service)]


def get_now() -> datetime:
    """The clock is a dependency, so tests can freeze it."""
    return datetime.now(UTC)


NowDep = Annotated[datetime, Depends(get_now)]


def get_coverage_policy(request: Request) -> CoveragePolicy:
    # The settings the app was created with, not the global ones.
    settings: Settings = request.app.state.settings
    return settings.coverage_policy()


def get_coverage_service(
    session: SessionDep, policy: Annotated[CoveragePolicy, Depends(get_coverage_policy)]
) -> CoverageService:
    return CoverageService(session, policy)


CoverageServiceDep = Annotated[CoverageService, Depends(get_coverage_service)]
