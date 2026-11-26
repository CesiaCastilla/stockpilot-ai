"""Steps of tests/features/transfer_validator.feature, against a real PostgreSQL."""

from datetime import UTC, datetime

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockpilot.domain.transfers import InvalidTransferError, TransferOrderStatus
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, TransferOrder, Trim, Unit, VehicleModel
from stockpilot.schemas.transfers import TransferProposal
from stockpilot.services.transfers import TransferService

pytestmark = pytest.mark.integration

scenarios("transfer_validator.feature")

type Outcome = TransferOrder | InvalidTransferError


@pytest.fixture
def trim(db_session: Session) -> Trim:
    trim = Trim(vehicle_model=VehicleModel(name="Pickup X"), name="Base")
    db_session.add(trim)
    db_session.flush()
    return trim


def _dealer(session: Session, code: str) -> Dealer:
    return session.scalars(select(Dealer).where(Dealer.code == code)).one()


def _unit(session: Session, vin: str) -> Unit:
    return session.scalars(select(Unit).where(Unit.vin == vin)).one()


def _add_unit(session: Session, trim: Trim, vin: str, dealer: Dealer | None) -> None:
    session.add(
        Unit(
            vin=vin,
            trim=trim,
            color="White",
            status=UnitStatus.IN_YARD,
            dealer=dealer,
            received_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )
    session.flush()


def _propose(session: Session, vin: str, dealer_id: int) -> Outcome:
    proposal = TransferProposal(vin=vin, destination_dealer_id=dealer_id, reason="Shortage")
    try:
        return TransferService(session).propose(proposal)
    except InvalidTransferError as error:
        return error


# --- Given --------------------------------------------------------------------


@given(parsers.parse('a dealer "{code}" with room for {capacity:d} units'))
def dealer_with_capacity(db_session: Session, code: str, capacity: int) -> None:
    db_session.add(Dealer(code=code, name=code, province="X", city="X", yard_capacity=capacity))
    db_session.flush()


@given(parsers.parse('a unit "{vin}" in the yard of "{code}"'))
def unit_in_yard(db_session: Session, trim: Trim, vin: str, code: str) -> None:
    _add_unit(db_session, trim, vin, _dealer(db_session, code))


@given(parsers.parse('a unit "{vin}" at the distribution center'))
def unit_at_distribution_center(db_session: Session, trim: Trim, vin: str) -> None:
    _add_unit(db_session, trim, vin, None)


@given(parsers.parse('unit "{vin}" is {status}'))
def unit_with_status(db_session: Session, vin: str, status: str) -> None:
    _unit(db_session, vin).status = UnitStatus(status)
    db_session.flush()


@given(parsers.parse('{count:d} units in stock at "{code}"'))
def units_in_stock(db_session: Session, trim: Trim, count: int, code: str) -> None:
    for number in range(count):
        _add_unit(db_session, trim, f"8AFZZZ1234568{number:04d}", _dealer(db_session, code))


@given(parsers.parse('a {status} order to move "{vin}" to "{code}"'))
def existing_order(db_session: Session, status: str, vin: str, code: str) -> None:
    unit = _unit(db_session, vin)
    rejected = status == "rejected"
    db_session.add(
        TransferOrder(
            unit=unit,
            origin_dealer=unit.dealer,
            destination_dealer=_dealer(db_session, code),
            status=TransferOrderStatus(status),
            reason="Shortage",
            decided_at=datetime(2026, 8, 1, tzinfo=UTC) if rejected else None,
            rejection_reason="No" if rejected else None,
        )
    )
    db_session.flush()


# --- When ---------------------------------------------------------------------


# Before the quoted-code step, whose pattern needs the quotes to match.
@when(
    parsers.parse('the agent proposes moving "{vin}" to a dealer that does not exist'),
    target_fixture="outcome",
)
def propose_to_unknown_dealer(db_session: Session, vin: str) -> Outcome:
    return _propose(db_session, vin, 999_999)


@when(parsers.parse('the agent proposes moving "{vin}" to "{code}"'), target_fixture="outcome")
def propose(db_session: Session, vin: str, code: str) -> Outcome:
    return _propose(db_session, vin, _dealer(db_session, code).id)


# --- Then ---------------------------------------------------------------------


def _assert_pending(outcome: Outcome, origin: Dealer | None, destination: Dealer) -> None:
    assert isinstance(outcome, TransferOrder), outcome
    assert outcome.status is TransferOrderStatus.PENDING
    assert outcome.origin_dealer_id == (None if origin is None else origin.id)
    assert outcome.destination_dealer_id == destination.id
    assert outcome.reason == "Shortage"


@then(parsers.parse('a pending order from "{origin}" to "{destination}" is created'))
def pending_order(db_session: Session, outcome: Outcome, origin: str, destination: str) -> None:
    _assert_pending(outcome, _dealer(db_session, origin), _dealer(db_session, destination))


@then(parsers.parse('a pending order from the distribution center to "{destination}" is created'))
def pending_assignment(db_session: Session, outcome: Outcome, destination: str) -> None:
    _assert_pending(outcome, None, _dealer(db_session, destination))


@then(parsers.parse('unit "{vin}" has not moved from "{code}"'))
def unit_not_moved(db_session: Session, vin: str, code: str) -> None:
    unit = _unit(db_session, vin)
    db_session.refresh(unit)
    assert unit.status is UnitStatus.IN_YARD
    assert unit.dealer_id == _dealer(db_session, code).id


@then(parsers.parse('the proposal is rejected because of "{rules}"'))
def proposal_rejected(outcome: Outcome, rules: str) -> None:
    assert isinstance(outcome, InvalidTransferError), outcome
    assert ", ".join(v.rule for v in outcome.violations) == rules


@then("no order is created")
def no_order(db_session: Session) -> None:
    assert db_session.scalar(select(func.count()).select_from(TransferOrder)) == 0
