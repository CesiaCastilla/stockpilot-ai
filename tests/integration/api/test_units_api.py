from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from stockpilot.domain.unit_status import UnitStatus
from stockpilot.domain.vin import check_digit
from stockpilot.models import Dealer, Trim, Unit, VehicleModel

pytestmark = pytest.mark.integration

URL = "/api/v1/units"


@dataclass
class Network:
    pickup_base: Trim
    pickup_limited: Trim
    sedan_base: Trim
    rosario: Dealer
    cordoba: Dealer


@pytest.fixture
def network(db_session: Session) -> Network:
    pickup = VehicleModel(name="Pickup X")
    sedan = VehicleModel(name="Sedan")
    net = Network(
        pickup_base=Trim(vehicle_model=pickup, name="Base"),
        pickup_limited=Trim(vehicle_model=pickup, name="Limited"),
        sedan_base=Trim(vehicle_model=sedan, name="Base"),
        rosario=Dealer(code="D001", name="X", province="Santa Fe", city="X", yard_capacity=2),
        cordoba=Dealer(code="D002", name="Y", province="Córdoba", city="Y", yard_capacity=10),
    )
    db_session.add_all(vars(net).values())
    db_session.flush()
    return net


def _vin(n: int) -> str:
    # Starts with 8 (South America): no check digit required.
    return f"8AFZZZ1234567{n:04d}"


def _register(client: TestClient, **fields: Any) -> dict[str, Any]:
    response = client.post(URL, json={"vin": _vin(1), "color": "White"} | fields)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _insert(
    session: Session,
    n: int,
    trim: Trim,
    status: UnitStatus = UnitStatus.IN_YARD,
    dealer: Dealer | None = None,
    days_ago: int = 0,
    color: str = "White",
) -> Unit:
    """Arrange units directly in the database, e.g. with an older received_at."""
    unit = Unit(
        vin=_vin(n),
        trim=trim,
        color=color,
        status=status,
        dealer=dealer,
        received_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    session.add(unit)
    session.flush()
    return unit


# --- Register -----------------------------------------------------------------


def test_unit_without_dealer_waits_in_the_distribution_center(
    api_client: TestClient, network: Network
) -> None:
    before = datetime.now(UTC)

    body = _register(api_client, trim_id=network.pickup_base.id)

    assert body["status"] == "in_yard"
    assert body["dealer_id"] is None
    assert before <= datetime.fromisoformat(body["received_at"]) <= datetime.now(UTC)


def test_unit_with_dealer_is_shipped_there(api_client: TestClient, network: Network) -> None:
    body = _register(api_client, trim_id=network.pickup_base.id, dealer_id=network.cordoba.id)

    assert body["status"] == "in_transit"
    assert body["dealer_id"] == network.cordoba.id


def test_registration_is_the_first_event_of_the_history(
    api_client: TestClient, network: Network
) -> None:
    unit = _register(api_client, trim_id=network.pickup_base.id, dealer_id=network.cordoba.id)

    events = api_client.get(f"{URL}/{unit['vin']}/history").json()

    assert len(events) == 1
    assert events[0] | {"id": 0} == {
        "id": 0,
        "from_status": None,
        "to_status": "in_transit",
        "dealer_id": network.cordoba.id,
        "occurred_at": unit["received_at"],
    }


def test_vin_is_normalized_to_upper_case(api_client: TestClient, network: Network) -> None:
    body = _register(api_client, vin=f"  {_vin(1).lower()} ", trim_id=network.pickup_base.id)

    assert body["vin"] == _vin(1)


def test_duplicate_vin_is_rejected_even_in_another_case(
    api_client: TestClient, network: Network
) -> None:
    _register(api_client, trim_id=network.pickup_base.id)

    response = api_client.post(
        URL, json={"vin": _vin(1).lower(), "trim_id": network.pickup_base.id, "color": "Red"}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": f"Unit with VIN '{_vin(1)}' already exists"}


def _north_american_vin_with_wrong_check_digit() -> str:
    vin = "1HGCM82603A004352"
    wrong = next(d for d in "0123456789X" if d != check_digit(vin))
    return vin[:8] + wrong + vin[9:]


@pytest.mark.parametrize(
    "vin",
    [
        pytest.param("8AFZZZ123456", id="too-short"),
        pytest.param("8AFZZZ1234567000I", id="letter-I"),
        pytest.param(_north_american_vin_with_wrong_check_digit(), id="bad-check-digit"),
    ],
)
def test_invalid_vin_is_rejected(api_client: TestClient, network: Network, vin: str) -> None:
    response = api_client.post(
        URL, json={"vin": vin, "trim_id": network.pickup_base.id, "color": "White"}
    )

    assert response.status_code == 422
    assert response.json()["detail"].startswith("Invalid VIN")


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"color": ""}, id="empty-color"),
        pytest.param({"color": " "}, id="blank-color"),
        pytest.param({"status": "sold"}, id="status-is-not-chosen-by-the-client"),
    ],
)
def test_register_rejects_invalid_data(
    api_client: TestClient, network: Network, fields: dict[str, Any]
) -> None:
    payload = {"vin": _vin(1), "trim_id": network.pickup_base.id, "color": "White"} | fields

    assert api_client.post(URL, json=payload).status_code == 422


def test_register_with_unknown_trim_or_dealer_returns_404(
    api_client: TestClient, network: Network
) -> None:
    unknown_trim = api_client.post(URL, json={"vin": _vin(1), "trim_id": 999999, "color": "W"})
    unknown_dealer = api_client.post(
        URL,
        json={"vin": _vin(1), "trim_id": network.pickup_base.id, "color": "W", "dealer_id": 999999},
    )

    assert unknown_trim.status_code == 404
    assert unknown_trim.json() == {"detail": "Trim 999999 not found"}
    assert unknown_dealer.status_code == 404


def test_unit_cannot_be_shipped_to_a_full_yard(
    api_client: TestClient, db_session: Session, network: Network
) -> None:
    # Capacity 2: one unit in the yard plus one on its way fill it.
    _insert(db_session, 1, network.pickup_base, UnitStatus.IN_YARD, network.rosario)
    _insert(db_session, 2, network.pickup_base, UnitStatus.IN_TRANSIT, network.rosario)

    response = api_client.post(
        URL,
        json={
            "vin": _vin(3),
            "trim_id": network.pickup_base.id,
            "color": "White",
            "dealer_id": network.rosario.id,
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Dealer D001 yard is full (2 of 2 units)"}
    assert api_client.get(f"{URL}/by-vin/{_vin(3)}").status_code == 404


# --- Read ---------------------------------------------------------------------


def test_get_unit_by_id_and_by_vin(api_client: TestClient, network: Network) -> None:
    unit = _register(api_client, trim_id=network.pickup_base.id)

    assert api_client.get(f"{URL}/{unit['id']}").json() == unit
    assert api_client.get(f"{URL}/by-vin/{_vin(1).lower()}").json() == unit


def test_history_is_found_by_vin_in_any_case_and_only_of_that_unit(
    api_client: TestClient, network: Network
) -> None:
    _register(api_client, trim_id=network.pickup_base.id)
    _register(api_client, vin=_vin(2), trim_id=network.pickup_base.id, dealer_id=network.cordoba.id)

    events = api_client.get(f"{URL}/{_vin(2).lower()}/history").json()

    assert [e["to_status"] for e in events] == ["in_transit"]


def test_unknown_unit_returns_404(api_client: TestClient) -> None:
    assert api_client.get(f"{URL}/999999").status_code == 404
    for path in (f"by-vin/{_vin(9)}", f"{_vin(9)}/history"):
        response = api_client.get(f"{URL}/{path}")
        assert response.status_code == 404
        assert response.json() == {"detail": f"Unit {_vin(9)} not found"}


# --- Update -------------------------------------------------------------------


@pytest.mark.parametrize("field", ["color", "trim_id"])
def test_update_changes_only_the_fields_sent(
    api_client: TestClient, network: Network, field: str
) -> None:
    unit = _register(api_client, trim_id=network.pickup_base.id, dealer_id=network.cordoba.id)
    changes: dict[str, Any] = {field: {"color": "Red", "trim_id": network.pickup_limited.id}[field]}

    response = api_client.patch(f"{URL}/{unit['id']}", json=changes)

    assert response.status_code == 200
    assert response.json() == unit | changes
    assert api_client.get(f"{URL}/{unit['id']}").json() == unit | changes


def test_update_is_not_a_status_change(api_client: TestClient, network: Network) -> None:
    unit = _register(api_client, trim_id=network.pickup_base.id)

    api_client.patch(f"{URL}/{unit['id']}", json={"color": "Red"})

    assert len(api_client.get(f"{URL}/{unit['vin']}/history").json()) == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (UnitStatus.IN_TRANSIT, 200),
        (UnitStatus.IN_YARD, 200),
        (UnitStatus.RESERVED, 200),
        (UnitStatus.SOLD, 409),
        (UnitStatus.DELIVERED, 409),
    ],
)
def test_update_is_only_for_units_in_stock(
    api_client: TestClient,
    db_session: Session,
    network: Network,
    status: UnitStatus,
    expected: int,
) -> None:
    unit = _insert(db_session, 1, network.pickup_base, status, network.cordoba)

    response = api_client.patch(f"{URL}/{unit.id}", json={"color": "Red"})

    assert response.status_code == expected
    if expected == 409:
        assert response.json() == {
            "detail": f"Unit {_vin(1)} is {status.value}: only units in stock can be corrected"
        }
    color = api_client.get(f"{URL}/{unit.id}").json()["color"]
    assert color == ("Red" if expected == 200 else "White")


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"color": None}, id="null-color"),
        pytest.param({"trim_id": None}, id="null-trim"),
        pytest.param({"color": "  "}, id="blank-color"),
        pytest.param({"color": "x" * 41}, id="color-too-long"),
        pytest.param({"vin": _vin(2)}, id="vin-is-immutable"),
        pytest.param({"status": "sold"}, id="status-has-its-own-operations"),
        pytest.param({"dealer_id": 1}, id="dealer-has-its-own-operations"),
        pytest.param({"color": "Red", "received_at": "2026-01-01"}, id="received-at"),
    ],
)
def test_update_rejects_invalid_changes(
    api_client: TestClient, network: Network, changes: dict[str, Any]
) -> None:
    unit = _register(api_client, trim_id=network.pickup_base.id)

    assert api_client.patch(f"{URL}/{unit['id']}", json=changes).status_code == 422
    assert api_client.get(f"{URL}/{unit['id']}").json() == unit


def test_update_with_an_unknown_trim_changes_nothing(
    api_client: TestClient, network: Network
) -> None:
    unit = _register(api_client, trim_id=network.pickup_base.id)

    response = api_client.patch(f"{URL}/{unit['id']}", json={"trim_id": 999999, "color": "Red"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Trim 999999 not found"}
    assert api_client.get(f"{URL}/{unit['id']}").json() == unit


def test_update_unknown_unit_returns_404(api_client: TestClient) -> None:
    assert api_client.patch(f"{URL}/999999", json={"color": "Red"}).status_code == 404


# --- List ---------------------------------------------------------------------


@pytest.fixture
def stock(db_session: Session, network: Network) -> Network:
    """Five units across statuses, dealers and models."""
    _insert(db_session, 1, network.pickup_base, UnitStatus.IN_YARD, network.cordoba, days_ago=10)
    _insert(db_session, 2, network.pickup_limited, UnitStatus.RESERVED, network.cordoba, 30)
    _insert(db_session, 3, network.sedan_base, UnitStatus.SOLD, network.rosario, days_ago=50)
    _insert(db_session, 4, network.sedan_base, UnitStatus.IN_YARD, None, days_ago=5)
    _insert(db_session, 5, network.pickup_base, UnitStatus.IN_TRANSIT, network.rosario, 20)
    return network


def _vins(client: TestClient, **params: Any) -> list[str]:
    response = client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return [u["vin"][-1] for u in response.json()["items"]]


def test_list_is_sorted_oldest_first_and_paginated(api_client: TestClient, stock: Network) -> None:
    body = api_client.get(URL, params={"limit": 2, "offset": 1}).json()

    assert [u["vin"][-1] for u in body["items"]] == ["2", "5"]
    assert body["total"] == 5


def test_list_filters(api_client: TestClient, stock: Network) -> None:
    assert _vins(api_client, status=["in_yard", "reserved"]) == ["2", "1", "4"]
    assert _vins(api_client, dealer_id=stock.cordoba.id) == ["2", "1"]
    assert _vins(api_client, at_distribution_center=True) == ["4"]
    assert _vins(api_client, at_distribution_center=False) == ["3", "2", "5", "1"]
    assert _vins(api_client, trim_id=stock.pickup_base.id) == ["5", "1"]
    assert _vins(api_client, vehicle_model_id=stock.sedan_base.vehicle_model_id) == ["3", "4"]


def test_list_filters_are_combined(api_client: TestClient, stock: Network) -> None:
    params: dict[str, Any] = {
        "vehicle_model_id": stock.pickup_base.vehicle_model_id,
        "status": "in_yard",
    }

    body = api_client.get(URL, params=params).json()

    assert [u["vin"][-1] for u in body["items"]] == ["1"]
    assert body["total"] == 1


def test_list_filters_by_exact_color_in_any_case(
    api_client: TestClient, db_session: Session, network: Network
) -> None:
    _insert(db_session, 1, network.pickup_base, color="Black")
    _insert(db_session, 2, network.sedan_base, color="Black")
    _insert(db_session, 3, network.pickup_base, color="Red")
    _insert(db_session, 4, network.pickup_base, color="Pearl Black")

    for color in ("Black", "black", " BLACK "):
        assert sorted(_vins(api_client, color=color)) == ["1", "2"]
    assert _vins(api_client, color="Green") == []
    # "%" is plain text here, not a LIKE wildcard.
    assert _vins(api_client, color="%black") == []

    combined: dict[str, Any] = {"color": "black", "trim_id": network.pickup_base.id}
    body = api_client.get(URL, params=combined).json()
    assert [u["vin"][-1] for u in body["items"]] == ["1"]
    assert body["total"] == 1


def test_color_is_stored_trimmed_so_the_filter_finds_it(
    api_client: TestClient, network: Network
) -> None:
    unit = _register(api_client, trim_id=network.pickup_base.id, color=" Black ")
    assert unit["color"] == "Black"
    assert _vins(api_client, color="Black") == ["1"]

    api_client.patch(f"{URL}/{unit['id']}", json={"color": " Red "})
    assert _vins(api_client, color="Red") == ["1"]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"status": "lost"}, id="unknown-status"),
        pytest.param({"color": " "}, id="blank-color"),
    ],
)
def test_list_rejects_invalid_filters(api_client: TestClient, params: dict[str, str]) -> None:
    assert api_client.get(URL, params=params).status_code == 422
