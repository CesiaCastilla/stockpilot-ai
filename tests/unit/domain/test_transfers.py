"""Messages and boundaries. The business cases are in transfer_validator.feature."""

from dataclasses import replace

import pytest

from stockpilot.domain.transfers import (
    InvalidTransferError,
    TransferDestination,
    TransferRule,
    TransferUnit,
    Violation,
    check_approval,
    check_transfer,
)
from stockpilot.domain.unit_status import UnitStatus

VIN = "8AFZZZ12345670001"
UNIT = TransferUnit(vin=VIN, status=UnitStatus.IN_YARD, dealer_id=1, has_pending_order=False)
DESTINATION = TransferDestination(
    dealer_id=2, code="D002", yard_capacity=10, stock_units=0, pending_arrivals=0
)


def _check(
    unit: TransferUnit | None = UNIT, destination: TransferDestination | None = DESTINATION
) -> list[Violation]:
    return check_transfer(vin=VIN, unit=unit, destination_id=2, destination=destination)


def test_valid_transfer_breaks_no_rule() -> None:
    assert _check() == []


@pytest.mark.parametrize(
    ("stock", "pending", "fits"),
    [(9, 0, True), (10, 0, False), (0, 9, True), (0, 10, False), (5, 4, True), (5, 5, False)],
)
def test_stock_and_pending_orders_share_the_capacity(stock: int, pending: int, fits: bool) -> None:
    destination = replace(DESTINATION, stock_units=stock, pending_arrivals=pending)

    rules = [v.rule for v in _check(destination=destination)]

    assert rules == ([] if fits else [TransferRule.YARD_HAS_ROOM])


def test_yard_above_its_capacity_has_no_room() -> None:
    # The capacity can be lowered below the current stock.
    (violation,) = _check(destination=replace(DESTINATION, stock_units=12))

    assert violation.rule is TransferRule.YARD_HAS_ROOM


def test_a_unit_that_is_not_available_is_not_also_reported_as_proposed() -> None:
    # Its pending order is stale: the status is what the model has to know.
    unit = replace(UNIT, status=UnitStatus.SOLD, has_pending_order=True)

    assert [v.rule for v in _check(unit=unit)] == [TransferRule.UNIT_AVAILABLE]


def test_distribution_center_is_not_the_same_place_as_the_destination() -> None:
    assert _check(unit=replace(UNIT, dealer_id=None)) == []


@pytest.mark.parametrize(
    ("unit", "destination", "message"),
    [
        (None, DESTINATION, f"Unit {VIN} does not exist"),
        (
            replace(UNIT, status=UnitStatus.RESERVED),
            DESTINATION,
            f"Unit {VIN} is reserved: only units in a yard can be transferred",
        ),
        (
            replace(UNIT, has_pending_order=True),
            DESTINATION,
            f"Unit {VIN} already has a pending transfer order",
        ),
        (UNIT, None, "Dealer 2 does not exist"),
        (
            UNIT,
            replace(DESTINATION, stock_units=8, pending_arrivals=2),
            "Dealer D002 has no room: capacity 10, 8 units in stock and 2 in pending orders",
        ),
        (
            replace(UNIT, dealer_id=2),
            DESTINATION,
            f"Unit {VIN} is already assigned to dealer 2",
        ),
    ],
)
def test_message_says_what_is_wrong(
    unit: TransferUnit | None, destination: TransferDestination | None, message: str
) -> None:
    assert [v.message for v in _check(unit, destination)] == [message]


def test_error_keeps_every_violation() -> None:
    violations = _check(unit=None, destination=None)

    error = InvalidTransferError(violations)

    assert [v.rule for v in error.violations] == [
        TransferRule.UNIT_EXISTS,
        TransferRule.DESTINATION_EXISTS,
    ]
    assert str(error) == f"Unit {VIN} does not exist; Dealer 2 does not exist"


# --- Approval -------------------------------------------------------------------


def _check_approval(
    unit: TransferUnit = UNIT,
    destination: TransferDestination = DESTINATION,
    origin_id: int | None = 1,
) -> list[TransferRule]:
    violations = check_approval(unit=unit, origin_id=origin_id, destination=destination)
    return [v.rule for v in violations]


def test_the_order_being_approved_does_not_block_itself() -> None:
    unit = replace(UNIT, has_pending_order=True)
    destination = replace(DESTINATION, stock_units=9, pending_arrivals=1)

    assert _check_approval(unit, destination) == []


@pytest.mark.parametrize(("stock", "fits"), [(9, True), (10, False)])
def test_approval_needs_room_in_the_yard(stock: int, fits: bool) -> None:
    rules = _check_approval(destination=replace(DESTINATION, stock_units=stock))

    assert rules == ([] if fits else [TransferRule.YARD_HAS_ROOM])


@pytest.mark.parametrize("status", [s for s in UnitStatus if s is not UnitStatus.IN_YARD])
def test_approval_needs_the_unit_in_a_yard(status: UnitStatus) -> None:
    assert _check_approval(replace(UNIT, status=status)) == [TransferRule.UNIT_AVAILABLE]


@pytest.mark.parametrize(("dealer_id", "origin_id"), [(3, 1), (None, 1), (3, None)])
def test_approval_needs_the_unit_where_it_was(dealer_id: int | None, origin_id: int | None) -> None:
    unit = replace(UNIT, dealer_id=dealer_id)

    assert _check_approval(unit, origin_id=origin_id) == [TransferRule.UNIT_AT_ORIGIN]


def test_unit_still_at_the_distribution_center_can_be_approved() -> None:
    assert _check_approval(replace(UNIT, dealer_id=None), origin_id=None) == []
