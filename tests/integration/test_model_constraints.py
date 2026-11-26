"""The database itself enforces the core invariants, even if code bypasses the ORM."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Reservation, Sale, TransferOrder, Trim, Unit, VehicleModel

pytestmark = pytest.mark.integration

VALID_VIN = "8AFZZZ12345678901"
RESERVED_AT = datetime(2026, 7, 1, tzinfo=UTC)
DECIDED_AT = datetime(2026, 8, 1, tzinfo=UTC)


def _trim(session: Session, model_name: str = "Pickup X", trim_name: str = "XLT 4x4") -> Trim:
    trim = Trim(vehicle_model=VehicleModel(name=model_name), name=trim_name)
    session.add(trim)
    session.flush()
    return trim


def _dealer(session: Session, code: str = "D001", yard_capacity: int = 50) -> Dealer:
    dealer = Dealer(
        code=code,
        name="Dealer Centro",
        province="Córdoba",
        city="Córdoba",
        yard_capacity=yard_capacity,
    )
    session.add(dealer)
    session.flush()
    return dealer


@pytest.fixture
def trim(db_session: Session) -> Trim:
    return _trim(db_session)


def _unit(
    session: Session, trim: Trim, *, vin: str = VALID_VIN, dealer: Dealer | None = None
) -> Unit:
    unit = Unit(
        vin=vin,
        trim=trim,
        color="Black",
        status=UnitStatus.IN_YARD,
        dealer=dealer,
        received_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.add(unit)
    session.flush()
    return unit


def test_valid_unit_is_stored_with_timestamps(db_session: Session, trim: Trim) -> None:
    unit = _unit(db_session, trim, dealer=_dealer(db_session))
    db_session.refresh(unit)

    assert unit.id is not None
    assert unit.status is UnitStatus.IN_YARD
    assert unit.created_at.tzinfo is not None


def test_unit_without_dealer_is_allowed_for_distribution_center_stock(
    db_session: Session, trim: Trim
) -> None:
    unit = _unit(db_session, trim, dealer=None)

    assert unit.dealer_id is None


def test_vin_must_be_unique(db_session: Session, trim: Trim) -> None:
    _unit(db_session, trim)

    with pytest.raises(IntegrityError, match="uq_units_vin"):
        _unit(db_session, trim)


def test_vin_shorter_than_17_characters_is_rejected(db_session: Session, trim: Trim) -> None:
    with pytest.raises(IntegrityError, match="ck_units_vin_length"):
        _unit(db_session, trim, vin="SHORTVIN")


def test_vin_longer_than_17_characters_is_rejected(db_session: Session, trim: Trim) -> None:
    # Caught by the VARCHAR(17) column itself, before the CHECK constraint.
    with pytest.raises(DataError, match="value too long"):
        _unit(db_session, trim, vin="8AFZZZ123456789012")


def test_unknown_status_is_rejected_by_the_database(db_session: Session, trim: Trim) -> None:
    with pytest.raises(IntegrityError, match="ck_units_unit_status"):
        db_session.execute(
            text(
                "INSERT INTO units (vin, trim_id, color, status, received_at) "
                "VALUES (:vin, :trim_id, 'Black', 'parked', now())"
            ),
            {"vin": VALID_VIN, "trim_id": trim.id},
        )


def test_yard_capacity_must_be_positive(db_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_dealers_yard_capacity_positive"):
        _dealer(db_session, yard_capacity=0)


def test_dealer_code_must_be_unique(db_session: Session) -> None:
    _dealer(db_session, code="D001")

    with pytest.raises(IntegrityError, match="uq_dealers_code"):
        _dealer(db_session, code="D001")


def test_trim_name_is_unique_within_a_model_but_not_across_models(db_session: Session) -> None:
    trim = _trim(db_session, model_name="Pickup X", trim_name="Base")
    db_session.add(Trim(vehicle_model=VehicleModel(name="SUV Y"), name="Base"))
    db_session.flush()  # same name in another model: allowed

    db_session.add(Trim(vehicle_model_id=trim.vehicle_model_id, name="Base"))
    with pytest.raises(IntegrityError, match="uq_trims_vehicle_model_id_name"):
        db_session.flush()


def test_unit_must_reference_an_existing_trim(db_session: Session) -> None:
    db_session.add(
        Unit(
            vin=VALID_VIN,
            trim_id=999_999,
            color="Black",
            status=UnitStatus.IN_YARD,
            received_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )

    with pytest.raises(IntegrityError, match="fk_units_trim_id_trims"):
        db_session.flush()


# --- Reservations and sales ---------------------------------------------------


def _reservation(
    session: Session, unit: Unit, dealer: Dealer, *, cancelled_at: datetime | None = None
) -> Reservation:
    reservation = Reservation(
        unit=unit, dealer=dealer, reserved_at=RESERVED_AT, cancelled_at=cancelled_at
    )
    session.add(reservation)
    session.flush()
    return reservation


def test_unit_can_have_only_one_active_reservation(db_session: Session, trim: Trim) -> None:
    dealer = _dealer(db_session)
    unit = _unit(db_session, trim, dealer=dealer)
    _reservation(db_session, unit, dealer)

    with pytest.raises(IntegrityError, match="uq_reservations_active_unit_id"):
        _reservation(db_session, unit, dealer)


def test_cancelled_reservations_do_not_block_a_new_one(db_session: Session, trim: Trim) -> None:
    dealer = _dealer(db_session)
    unit = _unit(db_session, trim, dealer=dealer)
    cancelled = RESERVED_AT + timedelta(days=2)
    _reservation(db_session, unit, dealer, cancelled_at=cancelled)
    _reservation(db_session, unit, dealer, cancelled_at=cancelled)

    active = _reservation(db_session, unit, dealer)

    assert active.id is not None


def test_reservation_cannot_be_cancelled_before_it_was_made(
    db_session: Session, trim: Trim
) -> None:
    dealer = _dealer(db_session)
    unit = _unit(db_session, trim, dealer=dealer)

    with pytest.raises(IntegrityError, match="ck_reservations_cancelled_after_reserved"):
        _reservation(db_session, unit, dealer, cancelled_at=RESERVED_AT - timedelta(days=1))


def test_unit_can_be_sold_only_once(db_session: Session, trim: Trim) -> None:
    dealer = _dealer(db_session)
    unit = _unit(db_session, trim, dealer=dealer)
    db_session.add(Sale(unit=unit, dealer=dealer, sold_at=RESERVED_AT))
    db_session.flush()

    db_session.add(Sale(unit=unit, dealer=dealer, sold_at=RESERVED_AT))
    with pytest.raises(IntegrityError, match="uq_sales_unit_id"):
        db_session.flush()


@pytest.mark.parametrize("price", [0, -1])
def test_trim_price_must_be_positive(db_session: Session, trim: Trim, price: int) -> None:
    trim.list_price_usd = Decimal(price)

    with pytest.raises(IntegrityError, match="ck_trims_list_price_usd_positive"):
        db_session.flush()


# --- Transfer orders ------------------------------------------------------------


def _order(
    session: Session,
    unit: Unit,
    destination: Dealer,
    *,
    status: TransferOrderStatus = TransferOrderStatus.PENDING,
    decided_at: datetime | None = None,
    rejection_reason: str | None = None,
) -> TransferOrder:
    order = TransferOrder(
        unit=unit,
        origin_dealer=unit.dealer,
        destination_dealer=destination,
        status=status,
        reason="Shortage",
        decided_at=decided_at,
        rejection_reason=rejection_reason,
    )
    session.add(order)
    session.flush()
    return order


def test_unit_can_have_only_one_pending_order(db_session: Session, trim: Trim) -> None:
    unit = _unit(db_session, trim)
    _order(db_session, unit, _dealer(db_session, code="D001"))

    with pytest.raises(IntegrityError, match="uq_transfer_orders_pending_unit_id"):
        _order(db_session, unit, _dealer(db_session, code="D002"))


def test_closed_orders_do_not_block_a_new_one(db_session: Session, trim: Trim) -> None:
    unit, dealer = _unit(db_session, trim), _dealer(db_session)
    _order(db_session, unit, dealer, status=TransferOrderStatus.APPROVED, decided_at=DECIDED_AT)
    _order(
        db_session,
        unit,
        dealer,
        status=TransferOrderStatus.REJECTED,
        decided_at=DECIDED_AT,
        rejection_reason="No",
    )

    assert _order(db_session, unit, dealer).id is not None


def test_order_cannot_go_back_to_its_origin(db_session: Session, trim: Trim) -> None:
    dealer = _dealer(db_session)
    unit = _unit(db_session, trim, dealer=dealer)

    with pytest.raises(IntegrityError, match="ck_transfer_orders_different_dealers"):
        _order(db_session, unit, dealer)


def test_unknown_order_status_is_rejected_by_the_database(db_session: Session, trim: Trim) -> None:
    order = _order(db_session, _unit(db_session, trim), _dealer(db_session))

    with pytest.raises(IntegrityError, match="ck_transfer_orders_status"):
        db_session.execute(
            text("UPDATE transfer_orders SET status = 'maybe', decided_at = now() WHERE id = :id"),
            {"id": order.id},
        )


@pytest.mark.parametrize(
    ("status", "decided_at", "constraint"),
    [
        (TransferOrderStatus.PENDING, DECIDED_AT, "closed_has_date"),
        (TransferOrderStatus.APPROVED, None, "closed_has_date"),
        (TransferOrderStatus.REJECTED, DECIDED_AT, "rejected_has_reason"),
    ],
)
def test_order_decision_must_be_complete(
    db_session: Session,
    trim: Trim,
    status: TransferOrderStatus,
    decided_at: datetime | None,
    constraint: str,
) -> None:
    with pytest.raises(IntegrityError, match=f"ck_transfer_orders_{constraint}"):
        _order(
            db_session,
            _unit(db_session, trim),
            _dealer(db_session),
            status=status,
            decided_at=decided_at,
        )


def test_only_rejected_orders_have_a_rejection_reason(db_session: Session, trim: Trim) -> None:
    with pytest.raises(IntegrityError, match="ck_transfer_orders_rejected_has_reason"):
        _order(
            db_session,
            _unit(db_session, trim),
            _dealer(db_session),
            status=TransferOrderStatus.APPROVED,
            decided_at=DECIDED_AT,
            rejection_reason="No",
        )
