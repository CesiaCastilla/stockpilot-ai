from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Trim, Unit, VehicleModel

pytestmark = pytest.mark.integration

URL = "/api/v1/dealers"


def _payload(**overrides: Any) -> dict[str, Any]:
    return {
        "code": "D001",
        "name": "Automotores Rosario",
        "province": "Santa Fe",
        "city": "Rosario",
        "yard_capacity": 50,
    } | overrides


def _create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post(URL, json=_payload(**overrides))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _add_units(session: Session, dealer_id: int, statuses: list[UnitStatus]) -> None:
    trim = Trim(vehicle_model=VehicleModel(name="Pickup X"), name="Base")
    for n, status in enumerate(statuses):
        session.add(
            Unit(
                vin=f"8AFZZZ1234567{n:04d}",
                trim=trim,
                color="White",
                status=status,
                dealer_id=dealer_id,
                received_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
        )
    session.flush()


# --- Create -------------------------------------------------------------------


def test_create_returns_the_new_dealer(api_client: TestClient) -> None:
    body = _create(api_client)

    assert body["id"] > 0
    assert body | {"id": 0} == _payload() | {"id": 0, "latitude": None, "longitude": None}


def test_create_rejects_a_duplicate_code(api_client: TestClient) -> None:
    _create(api_client)

    response = api_client.post(URL, json=_payload(name="Another dealer"))

    assert response.status_code == 409
    assert response.json() == {"detail": "Dealer with code 'D001' already exists"}


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"yard_capacity": 0}, id="capacity-zero"),
        pytest.param({"code": "d-1"}, id="code-lowercase-and-symbol"),
        pytest.param({"name": ""}, id="empty-name"),
        pytest.param({"latitude": 91}, id="latitude-out-of-range"),
        pytest.param({"unknown_field": "x"}, id="unknown-field"),
    ],
)
def test_create_rejects_invalid_data(api_client: TestClient, overrides: dict[str, Any]) -> None:
    response = api_client.post(URL, json=_payload(**overrides))

    assert response.status_code == 422


# --- Read ---------------------------------------------------------------------


def test_get_returns_the_dealer(api_client: TestClient) -> None:
    created = _create(api_client)

    response = api_client.get(f"{URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_dealer_returns_404(api_client: TestClient) -> None:
    response = api_client.get(f"{URL}/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Dealer 999999 not found"}


def test_list_is_paginated_and_sorted_by_code(api_client: TestClient) -> None:
    for code in ("D003", "D001", "D002"):
        _create(api_client, code=code)

    response = api_client.get(URL, params={"limit": 2, "offset": 1})

    body = response.json()
    assert [d["code"] for d in body["items"]] == ["D002", "D003"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 2, 1)


def test_list_filters_by_province(api_client: TestClient) -> None:
    _create(api_client, code="D001", province="Santa Fe")
    _create(api_client, code="D002", province="Mendoza")

    body = api_client.get(URL, params={"province": "Mendoza"}).json()

    assert [d["code"] for d in body["items"]] == ["D002"]
    assert body["total"] == 1


def test_list_rejects_a_limit_above_200(api_client: TestClient) -> None:
    assert api_client.get(URL, params={"limit": 201}).status_code == 422


# --- Update -------------------------------------------------------------------


def test_update_changes_only_the_fields_sent(api_client: TestClient) -> None:
    created = _create(api_client, latitude=-32.95, longitude=-60.65)

    response = api_client.patch(f"{URL}/{created['id']}", json={"name": "New name"})

    assert response.status_code == 200
    assert response.json() == created | {"name": "New name"}


def test_update_can_clear_coordinates_with_null(api_client: TestClient) -> None:
    created = _create(api_client, latitude=-32.95, longitude=-60.65)

    body = api_client.patch(f"{URL}/{created['id']}", json={"latitude": None}).json()

    assert body["latitude"] is None
    assert body["longitude"] == -60.65


@pytest.mark.parametrize("field", ["province", "city"])
def test_moving_a_dealer_clears_its_old_coordinates(api_client: TestClient, field: str) -> None:
    created = _create(api_client, latitude=-32.95, longitude=-60.65)

    body = api_client.patch(f"{URL}/{created['id']}", json={field: "Elsewhere"}).json()

    assert body == created | {field: "Elsewhere", "latitude": None, "longitude": None}


def test_moving_a_dealer_can_bring_its_new_coordinates(api_client: TestClient) -> None:
    created = _create(api_client, latitude=-32.95, longitude=-60.65)
    changes = {"city": "Elsewhere", "latitude": -31.4, "longitude": -64.2}

    assert api_client.patch(f"{URL}/{created['id']}", json=changes).json() == created | changes


def test_sending_the_same_address_keeps_the_coordinates(api_client: TestClient) -> None:
    created = _create(api_client, latitude=-32.95, longitude=-60.65)
    same = {"province": created["province"], "city": created["city"]}

    assert api_client.patch(f"{URL}/{created['id']}", json=same).json() == created


def test_names_are_trimmed(api_client: TestClient) -> None:
    # Georef matches exact names, so " Tandil " would never be located.
    created = _create(api_client, province=" Buenos Aires ", city=" Tandil ")

    assert (created["province"], created["city"]) == ("Buenos Aires", "Tandil")


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"city": " "}, id="blank-city"),
        pytest.param({"name": None}, id="null-required-field"),
        pytest.param({"code": "D999"}, id="code-is-immutable"),
        pytest.param({"yard_capacity": -1}, id="negative-capacity"),
    ],
)
def test_update_rejects_invalid_changes(api_client: TestClient, changes: dict[str, Any]) -> None:
    created = _create(api_client)

    response = api_client.patch(f"{URL}/{created['id']}", json=changes)

    assert response.status_code == 422


def test_update_unknown_dealer_returns_404(api_client: TestClient) -> None:
    assert api_client.patch(f"{URL}/999999", json={"name": "x"}).status_code == 404


def test_capacity_cannot_go_below_the_units_in_stock(
    api_client: TestClient, db_session: Session
) -> None:
    created = _create(api_client, yard_capacity=10)
    stock = [UnitStatus.IN_YARD, UnitStatus.RESERVED, UnitStatus.IN_TRANSIT]
    _add_units(db_session, created["id"], [*stock, UnitStatus.DELIVERED])

    too_small = api_client.patch(f"{URL}/{created['id']}", json={"yard_capacity": 2})
    exact = api_client.patch(f"{URL}/{created['id']}", json={"yard_capacity": 3})

    assert too_small.status_code == 409
    assert "below the 3 units" in too_small.json()["detail"]
    # Delivered units have left the yard: they do not count.
    assert exact.status_code == 200


# --- Delete -------------------------------------------------------------------


def test_delete_removes_the_dealer(api_client: TestClient) -> None:
    created = _create(api_client)

    response = api_client.delete(f"{URL}/{created['id']}")

    assert response.status_code == 204
    assert api_client.get(f"{URL}/{created['id']}").status_code == 404


def test_delete_dealer_with_units_is_rejected(api_client: TestClient, db_session: Session) -> None:
    created = _create(api_client)
    _add_units(db_session, created["id"], [UnitStatus.DELIVERED])

    response = api_client.delete(f"{URL}/{created['id']}")

    assert response.status_code == 409
    assert "cannot be deleted" in response.json()["detail"]
    assert api_client.get(f"{URL}/{created['id']}").status_code == 200


def test_delete_unknown_dealer_returns_404(api_client: TestClient) -> None:
    assert api_client.delete(f"{URL}/999999").status_code == 404
