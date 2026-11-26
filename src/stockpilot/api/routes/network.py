"""The whole dealer network at once: what the agent and the daily job start from."""

from typing import Annotated

from fastapi import APIRouter, Query

from stockpilot.api.dependencies import CoverageServiceDep, NowDep
from stockpilot.api.params import Limit, Offset
from stockpilot.schemas.coverage import AgedUnitPage, NetworkFilters, ShortageReport

router = APIRouter(prefix="/network", tags=["network"])

# Ten years: no new unit waits that long, and a larger number of days would
# overflow the date it is subtracted from.
MinDays = Annotated[
    int | None,
    Query(ge=0, le=3650, description="Default: the aging threshold. 0: every movable unit."),
]


@router.get("/shortages")
def list_shortages(
    service: CoverageServiceDep, now: NowDep, filters: Annotated[NetworkFilters, Query()]
) -> ShortageReport:
    """Dealers and trims with less coverage than the threshold, lowest first."""
    return service.shortages(filters, now=now)


@router.get("/aged-units")
def list_aged_units(
    service: CoverageServiceDep,
    now: NowDep,
    filters: Annotated[NetworkFilters, Query()],
    min_days: MinDays = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AgedUnitPage:
    """Units in a yard for more than min_days that could be moved, oldest first."""
    return service.aged_units(filters, now=now, min_days=min_days, limit=limit, offset=offset)
