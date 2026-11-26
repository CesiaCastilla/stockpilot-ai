from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, TransferOrder, Trim, Unit, UnitEvent, VehicleModel
from stockpilot.schemas.transfers import TransferProposal
from stockpilot.services.transfers import TransferService

pytestmark = pytest.mark.integration

URL = "/api/v1/transfer-orders"
VIN = "8AFZZZ12345670001"
RECEIVED_AT = datetime(2026, 6, 1, tzinfo=UTC)


def _dealer(session: Session, code: str, *, capacity: int = 5) -> Dealer:
    dealer = Dealer(code=code, name="X", province="X", city="X", yard_capacity=capacity)
    session.add(dealer)
    session.flush()
    return dealer


@pytest.fixture
def trim(db_session: Session) -> Trim:
    trim = Trim(vehicle_model=VehicleModel(name="Pickup X"), name="Base")
    db_session.add(trim)
    db_session.flush()
    return trim


@pytest.fixture
def origin(db_session: Session) -> Dealer:
    return _dealer(db_session, "D001")


@pytest.fixture
def destination(db_session: Session) -> Dealer:
    return _dealer(db_session, "D002", capacity=2)


def _unit(session: Session, trim: Trim, dealer: Dealer | None, vin: str = VIN) -> Unit:
    unit = Unit(
        vin=vin,
        trim=trim,
        color="White",
        status=UnitStatus.IN_YARD,
        dealer=dealer,
        received_at=RECEIVED_AT,
    )
    session.add(unit)
    session.flush()
    return unit


@pytest.fixture
def unit(db_session: Session, trim: Trim, origin: Dealer) -> Unit:
    return _unit(db_session, trim, origin)


def _propose(session: Session, vin: str, destination: Dealer) -> TransferOrder:
    return TransferService(session).propose(
        TransferProposal(vin=vin, destination_dealer_id=destination.id, reason="Shortage")
    )


@pytest.fixture
def order(db_session: Session, unit: Unit, destination: Dealer) -> TransferOrder:
    return _propose(db_session, unit.vin, destination)


def _events(session: Session) -> int | None:
    return session.scalar(select(func.count()).select_from(UnitEvent))


def _assert_still_pending(client: TestClient, session: Session, order: TransferOrder) -> None:
    body = client.get(f"{URL}/{order.id}").json()
    assert (body["status"], body["decided_at"]) == ("pending", None)
    assert _events(session) == 0


# --- Read -----------------------------------------------------------------------


def test_order_shows_what_a_person_needs_to_decide(
    api_client: TestClient,
    order: TransferOrder,
    unit: Unit,
    origin: Dealer,
    destination: Dealer,
) -> None:
    response = api_client.get(f"{URL}/{order.id}")

    assert response.status_code == 200
    body = response.json()
    assert datetime.fromisoformat(body.pop("created_at")).tzinfo is not None
    assert body == {
        "id": order.id,
        "unit_id": unit.id,
        "vin": VIN,
        "origin_dealer_id": origin.id,
        "origin_dealer_code": "D001",
        "destination_dealer_id": destination.id,
        "destination_dealer_code": "D002",
        "status": "pending",
        "reason": "Shortage",
        "decided_at": None,
        "rejection_reason": None,
        "agent_run_id": None,
    }


def test_order_from_the_distribution_center_has_no_origin(
    api_client: TestClient, db_session: Session, trim: Trim, destination: Dealer
) -> None:
    _unit(db_session, trim, None)
    order = _propose(db_session, VIN, destination)

    body = api_client.get(f"{URL}/{order.id}").json()

    assert (body["origin_dealer_id"], body["origin_dealer_code"]) == (None, None)


def _orders(session: Session, trim: Trim, origin: Dealer, destination: Dealer) -> dict[str, int]:
    """Three orders, the newest with the lowest id."""
    ids: dict[str, int] = {}
    created = datetime(2026, 8, 1, tzinfo=UTC)
    for number, status in enumerate(TransferOrderStatus):
        closed = status is not TransferOrderStatus.PENDING
        order = TransferOrder(
            unit=_unit(session, trim, origin, vin=f"8AFZZZ1234567000{number}"),
            origin_dealer=origin,
            destination_dealer=destination,
            status=status,
            reason="Shortage",
            created_at=created - timedelta(days=number),
            decided_at=created if closed else None,
            rejection_reason="No" if status is TransferOrderStatus.REJECTED else None,
        )
        session.add(order)
        session.flush()
        ids[status.value] = order.id
    return ids


def test_list_is_sorted_oldest_first(
    api_client: TestClient, db_session: Session, trim: Trim, origin: Dealer, destination: Dealer
) -> None:
    ids = _orders(db_session, trim, origin, destination)

    body = api_client.get(URL).json()

    assert [o["id"] for o in body["items"]] == [ids["rejected"], ids["approved"], ids["pending"]]
    assert body["total"] == 3


@pytest.mark.parametrize("status", list(TransferOrderStatus))
def test_list_can_be_filtered_by_status(
    api_client: TestClient,
    db_session: Session,
    trim: Trim,
    origin: Dealer,
    destination: Dealer,
    status: TransferOrderStatus,
) -> None:
    ids = _orders(db_session, trim, origin, destination)

    body = api_client.get(URL, params={"status": status}).json()

    assert [o["id"] for o in body["items"]] == [ids[status]]
    assert body["total"] == 1


def test_list_is_paginated(
    api_client: TestClient, db_session: Session, trim: Trim, origin: Dealer, destination: Dealer
) -> None:
    ids = _orders(db_session, trim, origin, destination)

    body = api_client.get(URL, params={"limit": 1, "offset": 1}).json()

    assert [o["id"] for o in body["items"]] == [ids["approved"]]
    assert (body["total"], body["limit"], body["offset"]) == (3, 1, 1)


def test_unknown_status_filter_is_a_422(api_client: TestClient) -> None:
    assert api_client.get(URL, params={"status": "maybe"}).status_code == 422


def test_list_does_not_query_once_per_order(
    api_client: TestClient, db_session: Session, trim: Trim, origin: Dealer, destination: Dealer
) -> None:
    _orders(db_session, trim, origin, destination)
    db_session.commit()
    db_session.expunge_all()
    statements: list[str] = []

    def record(_c: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if statement.startswith("SELECT"):
            statements.append(statement)

    event.listen(db_session.connection(), "before_cursor_execute", record)
    api_client.get(URL)

    assert len(statements) == 2  # the page and its total


# --- Approve --------------------------------------------------------------------


def test_approving_ships_the_unit(
    api_client: TestClient, order: TransferOrder, unit: Unit, destination: Dealer
) -> None:
    before = datetime.now(UTC)

    response = api_client.post(f"{URL}/{order.id}/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert before <= datetime.fromisoformat(body["decided_at"]) <= datetime.now(UTC)
    assert body["rejection_reason"] is None
    shipped = api_client.get(f"/api/v1/units/{unit.id}").json()
    assert (shipped["status"], shipped["dealer_id"]) == ("in_transit", destination.id)


def test_approving_keeps_the_order_origin_and_the_unit_age(
    api_client: TestClient, order: TransferOrder, unit: Unit, origin: Dealer
) -> None:
    body = api_client.post(f"{URL}/{order.id}/approve").json()

    assert body["origin_dealer_code"] == "D001"
    shipped = api_client.get(f"/api/v1/units/{unit.id}").json()
    assert datetime.fromisoformat(shipped["received_at"]) == RECEIVED_AT


def test_approving_leaves_an_event_with_the_destination(
    api_client: TestClient, order: TransferOrder, destination: Dealer
) -> None:
    decided_at = api_client.post(f"{URL}/{order.id}/approve").json()["decided_at"]

    (shipped,) = api_client.get(f"/api/v1/units/{VIN}/history").json()

    assert (shipped["from_status"], shipped["to_status"]) == ("in_yard", "in_transit")
    assert shipped["dealer_id"] == destination.id
    assert shipped["occurred_at"] == decided_at


def test_approval_is_committed(
    api_client: TestClient, db_session: Session, order: TransferOrder, unit: Unit
) -> None:
    api_client.post(f"{URL}/{order.id}/approve")

    db_session.rollback()
    assert order.status is TransferOrderStatus.APPROVED
    assert unit.status is UnitStatus.IN_TRANSIT


def test_shipped_unit_arrives_at_its_destination(
    api_client: TestClient, order: TransferOrder, unit: Unit, destination: Dealer
) -> None:
    api_client.post(f"{URL}/{order.id}/approve")

    body = api_client.post(f"/api/v1/units/{unit.id}/arrive").json()

    assert (body["status"], body["dealer_id"]) == ("in_yard", destination.id)


def test_unit_of_the_distribution_center_is_assigned(
    api_client: TestClient, db_session: Session, trim: Trim, destination: Dealer
) -> None:
    unit = _unit(db_session, trim, None)
    order = _propose(db_session, VIN, destination)

    response = api_client.post(f"{URL}/{order.id}/approve")

    assert response.status_code == 200
    assert (unit.status, unit.dealer_id) == (UnitStatus.IN_TRANSIT, destination.id)


def test_order_for_the_last_place_can_be_approved(
    api_client: TestClient, db_session: Session, trim: Trim, unit: Unit, destination: Dealer
) -> None:
    _unit(db_session, trim, destination, vin="8AFZZZ12345670002")
    order = _propose(db_session, VIN, destination)  # its own place must not block it

    assert api_client.post(f"{URL}/{order.id}/approve").status_code == 200


@pytest.mark.parametrize("operation", ["reserve", "sell"])
def test_unit_taken_by_a_customer_is_not_shipped(
    api_client: TestClient, db_session: Session, order: TransferOrder, unit: Unit, operation: str
) -> None:
    # A pending order does not hold the unit: a customer comes first.
    taken = api_client.post(f"/api/v1/units/{unit.id}/{operation}")
    assert taken.status_code == 200

    response = api_client.post(f"{URL}/{order.id}/approve")

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"Unit {VIN} is {taken.json()['status']}: only units in a yard can be transferred"
    }
    assert api_client.get(f"{URL}/{order.id}").json()["status"] == "pending"


def test_unit_is_not_shipped_to_a_yard_that_filled_up(
    api_client: TestClient,
    db_session: Session,
    trim: Trim,
    order: TransferOrder,
    destination: Dealer,
) -> None:
    for number in range(2):
        _unit(db_session, trim, destination, vin=f"8AFZZZ1234567100{number}")

    response = api_client.post(f"{URL}/{order.id}/approve")

    assert response.status_code == 409
    assert "D002 has no room" in response.json()["detail"]
    _assert_still_pending(api_client, db_session, order)


def test_unit_that_moved_is_not_shipped(
    api_client: TestClient, db_session: Session, order: TransferOrder, unit: Unit
) -> None:
    unit.dealer = _dealer(db_session, "D003")
    db_session.flush()

    response = api_client.post(f"{URL}/{order.id}/approve")

    assert response.status_code == 409
    assert "is not where it was" in response.json()["detail"]
    _assert_still_pending(api_client, db_session, order)


# --- Reject ---------------------------------------------------------------------


def test_rejecting_does_not_move_the_unit(
    api_client: TestClient, db_session: Session, order: TransferOrder, unit: Unit, origin: Dealer
) -> None:
    before = datetime.now(UTC)

    response = api_client.post(f"{URL}/{order.id}/reject", json={"reason": " Promised "})

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["rejection_reason"]) == ("rejected", "Promised")
    assert before <= datetime.fromisoformat(body["decided_at"]) <= datetime.now(UTC)
    assert body["reason"] == "Shortage"
    assert (unit.status, unit.dealer_id) == (UnitStatus.IN_YARD, origin.id)
    assert _events(db_session) == 0


def test_rejection_is_committed(
    api_client: TestClient, db_session: Session, order: TransferOrder
) -> None:
    api_client.post(f"{URL}/{order.id}/reject", json={"reason": "No"})

    db_session.rollback()
    assert order.status is TransferOrderStatus.REJECTED


def test_rejected_unit_can_be_proposed_again(
    api_client: TestClient, db_session: Session, order: TransferOrder, destination: Dealer
) -> None:
    api_client.post(f"{URL}/{order.id}/reject", json={"reason": "No"})

    assert _propose(db_session, VIN, destination).id != order.id


def test_order_that_cannot_be_executed_can_be_rejected(
    api_client: TestClient, order: TransferOrder, unit: Unit
) -> None:
    # The way out of an order that a sale left behind.
    api_client.post(f"/api/v1/units/{unit.id}/sell")

    response = api_client.post(f"{URL}/{order.id}/reject", json={"reason": "Sold"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "body",
    [{}, {"reason": None}, {"reason": "  "}, {"reason": "x" * 501}, {"reason": "No", "by": "me"}],
)
def test_rejecting_needs_a_reason(
    api_client: TestClient, db_session: Session, order: TransferOrder, body: dict[str, Any]
) -> None:
    assert api_client.post(f"{URL}/{order.id}/reject", json=body).status_code == 422
    _assert_still_pending(api_client, db_session, order)


# --- Errors ---------------------------------------------------------------------


@pytest.mark.parametrize("first", ["approve", "reject"])
@pytest.mark.parametrize("second", ["approve", "reject"])
def test_order_is_decided_only_once(
    api_client: TestClient, order: TransferOrder, first: str, second: str
) -> None:
    url, body = f"{URL}/{order.id}", {"reason": "No"}
    decided = api_client.post(f"{url}/{first}", json=body).json()

    response = api_client.post(f"{url}/{second}", json={"reason": "Changed my mind"})

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"Transfer order {order.id} is already {decided['status']}"
    }
    assert api_client.get(url).json() == decided


@pytest.mark.parametrize(
    ("method", "path"), [("GET", ""), ("POST", "/approve"), ("POST", "/reject")]
)
def test_unknown_order_is_a_404(api_client: TestClient, method: str, path: str) -> None:
    response = api_client.request(method, f"{URL}/999999{path}", json={"reason": "No"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Transfer order 999999 not found"}


def test_orders_cannot_be_created_by_hand(api_client: TestClient) -> None:
    assert api_client.post(URL, json={}).status_code == 405


def test_dealer_with_orders_cannot_be_deleted(
    api_client: TestClient, order: TransferOrder, destination: Dealer
) -> None:
    response = api_client.delete(f"/api/v1/dealers/{destination.id}")

    assert response.status_code == 409
    assert "transfer orders" in response.json()["detail"]
