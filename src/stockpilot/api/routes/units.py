from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from stockpilot.api.dependencies import UnitServiceDep
from stockpilot.schemas.common import ErrorResponse, Page
from stockpilot.schemas.units import UnitCreate, UnitEventRead, UnitFilters, UnitRead

router = APIRouter(prefix="/units", tags=["units"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Not found"},
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Conflicts with existing data",
    },
}


@router.get("")
def list_units(
    service: UnitServiceDep,
    filters: Annotated[UnitFilters, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[UnitRead]:
    """Units sorted by received_at, oldest first."""
    items, total = service.search(filters, limit=limit, offset=offset)
    return Page(
        items=[UnitRead.model_validate(u) for u in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses=_ERRORS)
def register_unit(payload: UnitCreate, service: UnitServiceDep) -> UnitRead:
    return UnitRead.model_validate(service.register(payload))


@router.get("/by-vin/{vin}", responses=_ERRORS)
def get_unit_by_vin(vin: str, service: UnitServiceDep) -> UnitRead:
    return UnitRead.model_validate(service.get_by_vin(vin))


@router.get("/{unit_id}", responses=_ERRORS)
def get_unit(unit_id: int, service: UnitServiceDep) -> UnitRead:
    return UnitRead.model_validate(service.get(unit_id))


@router.get("/{unit_id}/events", responses=_ERRORS)
def get_unit_history(unit_id: int, service: UnitServiceDep) -> list[UnitEventRead]:
    """Status changes of the unit, oldest first. Not paginated: a unit has a handful."""
    return [UnitEventRead.model_validate(e) for e in service.history(unit_id)]
