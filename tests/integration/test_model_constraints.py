"""The database itself enforces the core invariants, even if code bypasses the ORM."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Trim, Unit, VehicleModel

pytestmark = pytest.mark.integration

VALID_VIN = "8AFZZZ12345678901"


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
