from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.models import TransferOrder


class TransferOrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, order: TransferOrder) -> TransferOrder:
        self.session.add(order)
        return order

    def has_pending(self, unit_id: int) -> bool:
        pending = select(TransferOrder.id).where(
            TransferOrder.unit_id == unit_id, TransferOrder.status == TransferOrderStatus.PENDING
        )
        return self.session.scalar(pending) is not None

    def count_pending_arrivals(self, dealer_id: int) -> int:
        query = select(func.count()).where(
            TransferOrder.destination_dealer_id == dealer_id,
            TransferOrder.status == TransferOrderStatus.PENDING,
        )
        return self.session.scalar(query) or 0
