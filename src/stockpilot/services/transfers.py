from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from stockpilot.domain.errors import ConflictError, NotFoundError
from stockpilot.domain.transfers import (
    InvalidTransferError,
    TransferDestination,
    TransferOrderStatus,
    TransferUnit,
    check_approval,
    check_transfer,
)
from stockpilot.models import Dealer, TransferOrder, Unit
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.repositories.transfer_orders import TransferOrderRepository
from stockpilot.repositories.units import UnitRepository
from stockpilot.schemas.transfers import TransferProposal
from stockpilot.services.unit_lifecycle import UnitLifecycle


class TransferService:
    """The only way to create and decide transfer orders, so none skips the validator.

    Each public method is one transaction. Locks are always taken in the same
    sequence (order, unit, dealer), so two operations never wait for each other.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.orders = TransferOrderRepository(session)
        self.units = UnitRepository(session)
        self.dealers = DealerRepository(session)
        self.lifecycle = UnitLifecycle(session)

    def get(self, order_id: int) -> TransferOrder:
        order = self.orders.get(order_id)
        if order is None:
            raise NotFoundError("Transfer order", order_id)
        return order

    def search(
        self, *, status: TransferOrderStatus | None, limit: int = 50, offset: int = 0
    ) -> tuple[Sequence[TransferOrder], int]:
        orders = self.orders.search(status=status, limit=limit, offset=offset)
        return orders, self.orders.count(status=status)

    def propose(
        self, proposal: TransferProposal, *, agent_run_id: int | None = None
    ) -> TransferOrder:
        """Create a pending order, or raise InvalidTransferError with every broken rule."""
        # Locked: what the validator checks is still true when the order is saved.
        unit = self._locked_unit(proposal.vin)
        destination = self.dealers.get_for_update(proposal.destination_dealer_id)

        violations = check_transfer(
            vin=proposal.vin,
            unit=self._unit_facts(unit),
            destination_id=proposal.destination_dealer_id,
            destination=self._destination_facts(destination),
        )
        if violations:
            raise InvalidTransferError(violations)
        # Both were found, or there would be violations. This is for mypy.
        assert unit is not None and destination is not None

        order = self.orders.add(
            TransferOrder(
                unit=unit,
                origin_dealer=unit.dealer,
                destination_dealer=destination,
                status=TransferOrderStatus.PENDING,
                reason=proposal.reason,
                agent_run_id=agent_run_id,
            )
        )
        self.session.commit()
        return order

    def approve(self, order_id: int) -> TransferOrder:
        """Ship the unit and close the order, both or none.

        If the order can no longer be executed it stays pending: closing it is
        also a decision, and a person takes it by rejecting it.
        """
        order = self._locked_pending(order_id)
        unit = self.units.get_for_update(order.unit_id)
        destination = self.dealers.get_for_update(order.destination_dealer_id)
        unit_facts = self._unit_facts(unit)
        destination_facts = self._destination_facts(destination)
        # Foreign keys guarantee that both exist. This is for mypy.
        assert unit is not None and unit_facts is not None and destination_facts is not None

        violations = check_approval(
            unit=unit_facts, origin_id=order.origin_dealer_id, destination=destination_facts
        )
        if violations:
            raise InvalidTransferError(violations)

        order.decided_at = self.lifecycle.ship(unit, order.destination_dealer_id)
        order.status = TransferOrderStatus.APPROVED
        self.session.commit()
        return order

    def reject(self, order_id: int, reason: str) -> TransferOrder:
        order = self._locked_pending(order_id)
        order.decided_at = datetime.now(UTC)
        order.status = TransferOrderStatus.REJECTED
        order.rejection_reason = reason
        self.session.commit()
        return order

    def _locked_pending(self, order_id: int) -> TransferOrder:
        order = self.orders.get_for_update(order_id)
        if order is None:
            raise NotFoundError("Transfer order", order_id)
        if order.status is not TransferOrderStatus.PENDING:
            raise ConflictError(f"Transfer order {order.id} is already {order.status.value}")
        return order

    def _locked_unit(self, vin: str) -> Unit | None:
        unit = self.units.get_by_vin(vin)
        return None if unit is None else self.units.get_for_update(unit.id)

    def _unit_facts(self, unit: Unit | None) -> TransferUnit | None:
        if unit is None:
            return None
        return TransferUnit(
            vin=unit.vin,
            status=unit.status,
            dealer_id=unit.dealer_id,
            has_pending_order=self.orders.has_pending(unit.id),
        )

    def _destination_facts(self, dealer: Dealer | None) -> TransferDestination | None:
        if dealer is None:
            return None
        return TransferDestination(
            dealer_id=dealer.id,
            code=dealer.code,
            yard_capacity=dealer.yard_capacity,
            stock_units=self.dealers.count_stock_units(dealer.id),
            pending_arrivals=self.orders.count_pending_arrivals(dealer.id),
        )
