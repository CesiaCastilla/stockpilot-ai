"""Details of the service. The rules are in tests/features/transfer_validator.feature."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from stockpilot.domain.transfers import InvalidTransferError, TransferRule
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Trim, Unit, UnitEvent, VehicleModel
from stockpilot.schemas.transfers import TransferProposal
from stockpilot.services.transfers import TransferService

pytestmark = pytest.mark.integration

VIN = "8AFZZZ12345670001"


@pytest.fixture
def dealer(db_session: Session) -> Dealer:
    dealer = Dealer(code="D001", name="X", province="X", city="X", yard_capacity=5)
    trim = Trim(vehicle_model=VehicleModel(name="Pickup X"), name="Base")
    unit = Unit(
        vin=VIN,
        trim=trim,
        color="White",
        status=UnitStatus.IN_YARD,
        received_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    db_session.add_all([dealer, unit])
    db_session.commit()
    return dealer


def _proposal(dealer_id: int) -> TransferProposal:
    return TransferProposal(vin=VIN, destination_dealer_id=dealer_id, reason="Shortage")


def test_the_order_is_committed(db_session: Session, dealer: Dealer) -> None:
    order = TransferService(db_session).propose(_proposal(dealer.id))

    db_session.rollback()  # only what was committed survives
    db_session.refresh(order)
    assert order.unit.vin == VIN


def test_proposing_leaves_no_event_in_the_unit_history(db_session: Session, dealer: Dealer) -> None:
    TransferService(db_session).propose(_proposal(dealer.id))

    assert db_session.query(UnitEvent).count() == 0


def test_the_largest_id_is_just_an_unknown_dealer(db_session: Session, dealer: Dealer) -> None:
    # The schema stops larger ones: PostgreSQL would fail instead of not finding them.
    with pytest.raises(InvalidTransferError) as caught:
        TransferService(db_session).propose(_proposal(2**31 - 1))

    assert [v.rule for v in caught.value.violations] == [TransferRule.DESTINATION_EXISTS]
