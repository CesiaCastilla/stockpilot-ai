"""Transfer orders. There is no POST to create one: only the agent proposes."""

from typing import Any

from fastapi import APIRouter, status

from stockpilot.api.dependencies import TransferServiceDep
from stockpilot.api.params import Limit, Offset
from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.schemas.common import DatabaseId, ErrorResponse, Page
from stockpilot.schemas.transfers import TransferOrderRead, TransferRejection

router = APIRouter(prefix="/transfer-orders", tags=["transfer orders"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Order not found"},
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    **_NOT_FOUND,
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Already decided, or it can no longer be executed",
    },
}


@router.get("")
def list_transfer_orders(
    service: TransferServiceDep,
    status: TransferOrderStatus | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[TransferOrderRead]:
    """Oldest first."""
    items, total = service.search(status=status, limit=limit, offset=offset)
    return Page(
        items=[TransferOrderRead.model_validate(o) for o in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{order_id}", responses=_NOT_FOUND)
def get_transfer_order(order_id: DatabaseId, service: TransferServiceDep) -> TransferOrderRead:
    return TransferOrderRead.model_validate(service.get(order_id))


@router.post("/{order_id}/approve", responses=_ERRORS)
def approve_transfer_order(order_id: DatabaseId, service: TransferServiceDep) -> TransferOrderRead:
    """The rules are checked again and the unit leaves for its destination."""
    return TransferOrderRead.model_validate(service.approve(order_id))


@router.post("/{order_id}/reject", responses=_ERRORS)
def reject_transfer_order(
    order_id: DatabaseId, payload: TransferRejection, service: TransferServiceDep
) -> TransferOrderRead:
    """The unit does not move and can be proposed again."""
    return TransferOrderRead.model_validate(service.reject(order_id, payload.reason))
