from sqlalchemy.orm import Session

from stockpilot.domain.transfers import (
    InvalidTransferError,
    TransferDestination,
    TransferOrderStatus,
    TransferUnit,
    check_transfer,
)
from stockpilot.models import Dealer, TransferOrder, Unit
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.repositories.transfer_orders import TransferOrderRepository
from stockpilot.repositories.units import UnitRepository
from stockpilot.schemas.transfers import TransferProposal


class TransferService:
    """The only way to create a transfer order, so no proposal skips the validator.

    Each public method is one transaction. Locks are always taken in the same
    order, unit and then dealer, so two operations can never wait for each other.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.orders = TransferOrderRepository(session)
        self.units = UnitRepository(session)
        self.dealers = DealerRepository(session)

    def propose(self, proposal: TransferProposal) -> TransferOrder:
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
            )
        )
        self.session.commit()
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
