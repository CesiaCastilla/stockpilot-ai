from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from stockpilot.api.dependencies import CoverageServiceDep, DealerServiceDep, NowDep
from stockpilot.schemas.common import ErrorResponse, Page
from stockpilot.schemas.coverage import DealerCoverage
from stockpilot.schemas.dealers import DealerCreate, DealerRead, DealerUpdate

router = APIRouter(prefix="/dealers", tags=["dealers"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Dealer not found"},
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Conflicts with existing data",
    },
}


@router.get("")
def list_dealers(
    service: DealerServiceDep,
    province: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DealerRead]:
    items, total = service.search(province=province, limit=limit, offset=offset)
    return Page(
        items=[DealerRead.model_validate(d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses=_ERRORS)
def create_dealer(payload: DealerCreate, service: DealerServiceDep) -> DealerRead:
    return DealerRead.model_validate(service.create(payload))


@router.get("/{dealer_id}", responses=_ERRORS)
def get_dealer(dealer_id: int, service: DealerServiceDep) -> DealerRead:
    return DealerRead.model_validate(service.get(dealer_id))


@router.patch("/{dealer_id}", responses=_ERRORS)
def update_dealer(dealer_id: int, payload: DealerUpdate, service: DealerServiceDep) -> DealerRead:
    return DealerRead.model_validate(service.update(dealer_id, payload))


@router.delete("/{dealer_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS)
def delete_dealer(dealer_id: int, service: DealerServiceDep) -> Response:
    service.delete(dealer_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{dealer_id}/coverage", responses=_ERRORS)
def get_dealer_coverage(dealer_id: int, service: CoverageServiceDep, now: NowDep) -> DealerCoverage:
    """Coverage and aging by trim, most urgent first."""
    return service.for_dealer(dealer_id, now=now)
