"""Arrival, reservation, sale and delivery of a unit through the API."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Reservation, Sale, Trim, Unit, VehicleModel

pytestmark = pytest.mark.integration

URL = "/api/v1/units"
ACTIONS = ["arrive", "reserve", "cancel-reservation", "sell", "deliver"]
RECEIVED_AT = datetime.now(UTC) - timedelta(days=40)


@pytest.fixture
def trim(db_session: Session) -> Trim:
    trim = Trim(vehicle_model=VehicleModel(name="Pickup X"), name="Base")
    db_session.add(trim)
    db_session.flush()
    return trim


@pytest.fixture
def dealer(db_session: Session) -> Dealer:
    dealer = Dealer(code="D001", name="X", province="X", city="X", yard_capacity=5)
    db_session.add(dealer)
    db_session.flush()
    return dealer


def _insert(session: Session, trim: Trim, status: UnitStatus, dealer: Dealer | None) -> Unit:
    unit = Unit(
        vin="8AFZZZ12345670001",
        trim=trim,
        color="White",
        status=status,
        dealer=dealer,
        received_at=RECEIVED_AT,
    )
    session.add(unit)
    if status is UnitStatus.RESERVED:
        session.add(Reservation(unit=unit, dealer=dealer, reserved_at=RECEIVED_AT))
    session.flush()
    return unit


def _do(client: TestClient, unit_id: int, action: str) -> dict[str, Any]:
    response = client.post(f"{URL}/{unit_id}/{action}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _events(client: TestClient, unit_id: int) -> list[tuple[str | None, str]]:
    body = client.get(f"{URL}/{unit_id}/events").json()
    return [(e["from_status"], e["to_status"]) for e in body]


# --- Every action against every status ----------------------------------------

# Written by hand, not derived from the code under test.
ALLOWED = {
    ("arrive", "in_transit"): "in_yard",
    ("reserve", "in_yard"): "reserved",
    ("cancel-reservation", "reserved"): "in_yard",
    ("sell", "in_yard"): "sold",
    ("sell", "reserved"): "sold",
    ("deliver", "sold"): "delivered",
}


@pytest.mark.parametrize("status", list(UnitStatus))
@pytest.mark.parametrize("action", ACTIONS)
def test_actions_are_only_allowed_from_their_statuses(
    api_client: TestClient,
    db_session: Session,
    trim: Trim,
    dealer: Dealer,
    action: str,
    status: UnitStatus,
) -> None:
    unit = _insert(db_session, trim, status, dealer)
    expected = ALLOWED.get((action, status.value))

    response = api_client.post(f"{URL}/{unit.id}/{action}")

    if expected is not None:
        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected
        assert _events(api_client, unit.id) == [(status.value, expected)]
    else:
        # Rejected: neither the unit nor its history change.
        assert response.status_code == 409
        assert api_client.get(f"{URL}/{unit.id}").json()["status"] == status.value
        assert _events(api_client, unit.id) == []


@pytest.mark.parametrize("action", ACTIONS)
def test_unknown_unit_returns_404(api_client: TestClient, action: str) -> None:
    assert api_client.post(f"{URL}/999999/{action}").status_code == 404


# --- Side effects -------------------------------------------------------------


def test_arrival_keeps_the_dealer_and_does_not_reset_aging(
    api_client: TestClient, db_session: Session, trim: Trim, dealer: Dealer
) -> None:
    unit = _insert(db_session, trim, UnitStatus.IN_TRANSIT, dealer)

    body = _do(api_client, unit.id, "arrive")

    assert body["dealer_id"] == dealer.id
    assert datetime.fromisoformat(body["received_at"]) == RECEIVED_AT


def test_reserve_opens_a_reservation_at_the_dealer(
    api_client: TestClient, db_session: Session, trim: Trim, dealer: Dealer
) -> None:
    unit = _insert(db_session, trim, UnitStatus.IN_YARD, dealer)

    _do(api_client, unit.id, "reserve")

    reservation = db_session.scalars(select(Reservation)).one()
    assert (reservation.unit_id, reservation.dealer_id) == (unit.id, dealer.id)
    assert reservation.cancelled_at is None


def test_cancelled_reservation_stays_in_the_history_and_frees_the_unit(
    api_client: TestClient, db_session: Session, trim: Trim, dealer: Dealer
) -> None:
    unit = _insert(db_session, trim, UnitStatus.IN_YARD, dealer)

    for action in ("reserve", "cancel-reservation", "reserve"):
        _do(api_client, unit.id, action)

    first, second = db_session.scalars(select(Reservation).order_by(Reservation.id))
    assert first.cancelled_at is not None
    assert second.cancelled_at is None


@pytest.mark.parametrize("status", [UnitStatus.IN_YARD, UnitStatus.RESERVED])
def test_sell_records_one_sale_at_the_dealer(
    api_client: TestClient, db_session: Session, trim: Trim, dealer: Dealer, status: UnitStatus
) -> None:
    unit = _insert(db_session, trim, status, dealer)

    _do(api_client, unit.id, "sell")

    sale = db_session.scalars(select(Sale)).one()
    assert (sale.unit_id, sale.dealer_id) == (unit.id, dealer.id)


def test_a_fulfilled_reservation_is_not_marked_as_cancelled(
    api_client: TestClient, db_session: Session, trim: Trim, dealer: Dealer
) -> None:
    unit = _insert(db_session, trim, UnitStatus.RESERVED, dealer)

    _do(api_client, unit.id, "sell")

    assert db_session.scalars(select(Reservation)).one().cancelled_at is None


@pytest.mark.parametrize("action", ["reserve", "sell"])
def test_unit_at_the_distribution_center_cannot_be_reserved_or_sold(
    api_client: TestClient, db_session: Session, trim: Trim, action: str
) -> None:
    unit = _insert(db_session, trim, UnitStatus.IN_YARD, dealer=None)

    response = api_client.post(f"{URL}/{unit.id}/{action}")

    assert response.status_code == 409
    assert "distribution center" in response.json()["detail"]
    # Checks run before changes: nothing is left half done in the session.
    assert api_client.get(f"{URL}/{unit.id}").json()["status"] == "in_yard"
    assert _events(api_client, unit.id) == []


def test_failed_sale_creates_no_sale(
    api_client: TestClient, db_session: Session, trim: Trim, dealer: Dealer
) -> None:
    unit = _insert(db_session, trim, UnitStatus.IN_TRANSIT, dealer)

    assert api_client.post(f"{URL}/{unit.id}/sell").status_code == 409

    assert db_session.scalar(select(func.count()).select_from(Sale)) == 0


# --- Whole life ---------------------------------------------------------------


def test_history_tells_the_whole_life_of_a_unit(
    api_client: TestClient, trim: Trim, dealer: Dealer
) -> None:
    unit = api_client.post(
        URL,
        json={
            "vin": "8AFZZZ12345670001",
            "trim_id": trim.id,
            "color": "White",
            "dealer_id": dealer.id,
        },
    ).json()

    for action in ("arrive", "reserve", "cancel-reservation", "reserve", "sell", "deliver"):
        _do(api_client, unit["id"], action)

    assert _events(api_client, unit["id"]) == [
        (None, "in_transit"),
        ("in_transit", "in_yard"),
        ("in_yard", "reserved"),
        ("reserved", "in_yard"),
        ("in_yard", "reserved"),
        ("reserved", "sold"),
        ("sold", "delivered"),
    ]
