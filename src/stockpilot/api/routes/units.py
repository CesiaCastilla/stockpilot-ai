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


@router.get("/{vin}/history", responses=_ERRORS)
def get_unit_history(vin: str, service: UnitServiceDep) -> list[UnitEventRead]:
    """Status changes of the unit, oldest first. Not paginated: a unit has a handful."""
    return [UnitEventRead.model_validate(e) for e in service.history(vin)]


# --- Lifecycle ----------------------------------------------------------------
# One endpoint per business operation instead of PATCH {"status": ...}: each has
# its own rules and side effects (e.g. selling also creates the sale).


@router.post("/{unit_id}/arrive", responses=_ERRORS)
def arrive(unit_id: int, service: UnitServiceDep) -> UnitRead:
    """The unit in transit reaches its destination yard."""
    return UnitRead.model_validate(service.arrive(unit_id))


@router.post("/{unit_id}/reserve", responses=_ERRORS)
def reserve(unit_id: int, service: UnitServiceDep) -> UnitRead:
    return UnitRead.model_validate(service.reserve(unit_id))


@router.post("/{unit_id}/cancel-reservation", responses=_ERRORS)
def cancel_reservation(unit_id: int, service: UnitServiceDep) -> UnitRead:
    """The reservation is closed and the unit is available again."""
    return UnitRead.model_validate(service.cancel_reservation(unit_id))


@router.post("/{unit_id}/sell", responses=_ERRORS)
def sell(unit_id: int, service: UnitServiceDep) -> UnitRead:
    """Sell a unit in the yard, or turn its reservation into a sale."""
    return UnitRead.model_validate(service.sell(unit_id))


@router.post("/{unit_id}/deliver", responses=_ERRORS)
def deliver(unit_id: int, service: UnitServiceDep) -> UnitRead:
    """Hand the sold unit to its customer."""
    return UnitRead.model_validate(service.deliver(unit_id))
