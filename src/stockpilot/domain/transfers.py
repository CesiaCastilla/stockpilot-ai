"""Rules a transfer must pass. Pure functions: no database, no AI."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from stockpilot.domain.errors import DomainError
from stockpilot.domain.unit_status import UnitStatus


class TransferOrderStatus(StrEnum):
    # Waits for a person: nothing has moved yet.
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TransferRule(StrEnum):
    UNIT_EXISTS = "unit_exists"
    UNIT_AVAILABLE = "unit_available"
    NOT_ALREADY_PROPOSED = "not_already_proposed"
    DESTINATION_EXISTS = "destination_exists"
    DIFFERENT_DESTINATION = "different_destination"
    YARD_HAS_ROOM = "yard_has_room"
    UNIT_AT_ORIGIN = "unit_at_origin"


@dataclass(frozen=True)
class Violation:
    """A broken rule. The message is for people and for the model, which can
    use it to correct its proposal; code compares the rule."""

    rule: TransferRule
    message: str


class InvalidTransferError(DomainError):
    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(v.message for v in self.violations))


@dataclass(frozen=True)
class TransferUnit:
    """What the rules need to know about the unit, as the database has it."""

    vin: str
    status: UnitStatus
    dealer_id: int | None
    has_pending_order: bool


@dataclass(frozen=True)
class TransferDestination:
    dealer_id: int
    code: str
    yard_capacity: int
    stock_units: int
    # Units of pending orders: not there yet, but their places are promised.
    pending_arrivals: int


def check_unit(vin: str, unit: TransferUnit | None) -> list[Violation]:
    if unit is None:
        return [Violation(TransferRule.UNIT_EXISTS, f"Unit {vin} does not exist")]
    if unit.status is not UnitStatus.IN_YARD:
        return [
            Violation(
                TransferRule.UNIT_AVAILABLE,
                f"Unit {unit.vin} is {unit.status.value}: only units in a yard can be transferred",
            )
        ]
    if unit.has_pending_order:
        return [
            Violation(
                TransferRule.NOT_ALREADY_PROPOSED,
                f"Unit {unit.vin} already has a pending transfer order",
            )
        ]
    return []


def check_destination(dealer_id: int, destination: TransferDestination | None) -> list[Violation]:
    if destination is None:
        return [Violation(TransferRule.DESTINATION_EXISTS, f"Dealer {dealer_id} does not exist")]
    if destination.stock_units + destination.pending_arrivals >= destination.yard_capacity:
        return [
            Violation(
                TransferRule.YARD_HAS_ROOM,
                f"Dealer {destination.code} has no room: capacity {destination.yard_capacity}, "
                f"{destination.stock_units} units in stock and "
                f"{destination.pending_arrivals} in pending orders",
            )
        ]
    return []


def check_transfer(
    *,
    vin: str,
    unit: TransferUnit | None,
    destination_id: int,
    destination: TransferDestination | None,
) -> list[Violation]:
    """Every broken rule, not only the first one: the model can fix them all at once.

    vin and destination_id are what was proposed; unit and destination, what
    was found for them.
    """
    violations = check_unit(vin, unit) + check_destination(destination_id, destination)
    if unit is not None and unit.dealer_id == destination_id:
        violations.append(
            Violation(
                TransferRule.DIFFERENT_DESTINATION,
                f"Unit {unit.vin} is already assigned to dealer {destination_id}",
            )
        )
    return violations


def check_approval(
    *, unit: TransferUnit, origin_id: int | None, destination: TransferDestination
) -> list[Violation]:
    """The rules again, when a person approves: the stock may have changed since the proposal.

    Pending orders do not count now: one of them is the order being approved,
    and the others do not take a place until they are approved too.
    """
    violations = check_transfer(
        vin=unit.vin,
        unit=replace(unit, has_pending_order=False),
        destination_id=destination.dealer_id,
        destination=replace(destination, pending_arrivals=0),
    )
    if unit.dealer_id != origin_id:
        violations.append(
            Violation(
                TransferRule.UNIT_AT_ORIGIN,
                f"Unit {unit.vin} is not where it was when the order was proposed",
            )
        )
    return violations
