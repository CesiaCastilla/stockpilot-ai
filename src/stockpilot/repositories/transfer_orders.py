from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.models import TransferOrder


class TransferOrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, order_id: int) -> TransferOrder | None:
        return self.session.get(TransferOrder, order_id)

    def get_for_update(self, order_id: int) -> TransferOrder | None:
        """Get the order and lock its row: it is decided only once."""
        return self.session.get(
            TransferOrder, order_id, with_for_update={"key_share": True}, populate_existing=True
        )

    def search(
        self, *, status: TransferOrderStatus | None, limit: int, offset: int
    ) -> Sequence[TransferOrder]:
        # Oldest first: the ones that have waited longest for a decision.
        query = select(TransferOrder).order_by(TransferOrder.created_at, TransferOrder.id)
        if status is not None:
            query = query.where(TransferOrder.status == status)
        # Read by the list response: without this, three more queries per order.
        query = query.options(
            joinedload(TransferOrder.unit),
            joinedload(TransferOrder.origin_dealer),
            joinedload(TransferOrder.destination_dealer),
        )
        return self.session.scalars(query.limit(limit).offset(offset)).all()

    def count(self, *, status: TransferOrderStatus | None) -> int:
        query = select(func.count()).select_from(TransferOrder)
        if status is not None:
            query = query.where(TransferOrder.status == status)
        return self.session.scalar(query) or 0

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
