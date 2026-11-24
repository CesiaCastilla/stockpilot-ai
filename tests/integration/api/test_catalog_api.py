from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Unit

pytestmark = pytest.mark.integration

MODELS = "/api/v1/vehicle-models"
TRIMS = "/api/v1/trims"


def _create_model(client: TestClient, name: str = "Pickup X") -> dict[str, Any]:
    response = client.post(MODELS, json={"name": name})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _create_trim(client: TestClient, model_id: int, name: str = "Base") -> dict[str, Any]:
    response = client.post(f"{MODELS}/{model_id}/trims", json={"name": name})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- Vehicle models -----------------------------------------------------------


def test_create_model_returns_it(api_client: TestClient) -> None:
    body = _create_model(api_client)

    assert body["id"] > 0
    assert body["name"] == "Pickup X"


def test_create_model_rejects_a_duplicate_name(api_client: TestClient) -> None:
    _create_model(api_client)

    response = api_client.post(MODELS, json={"name": "Pickup X"})

    assert response.status_code == 409
    assert response.json() == {"detail": "Vehicle model with name 'Pickup X' already exists"}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"name": ""}, id="empty-name"),
        pytest.param({"name": "x" * 81}, id="name-too-long"),
        pytest.param({}, id="missing-name"),
        pytest.param({"name": "Sedan", "brand": "x"}, id="unknown-field"),
    ],
)
def test_create_model_rejects_invalid_data(api_client: TestClient, payload: dict[str, Any]) -> None:
    assert api_client.post(MODELS, json=payload).status_code == 422


def test_get_model_and_unknown_model(api_client: TestClient) -> None:
    created = _create_model(api_client)

    assert api_client.get(f"{MODELS}/{created['id']}").json() == created
    missing = api_client.get(f"{MODELS}/999999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Vehicle model 999999 not found"}


def test_list_models_is_paginated_and_sorted_by_name(api_client: TestClient) -> None:
    for name in ("Sedan", "Hatchback", "Pickup X"):
        _create_model(api_client, name)

    body = api_client.get(MODELS, params={"limit": 2, "offset": 0}).json()

    assert [m["name"] for m in body["items"]] == ["Hatchback", "Pickup X"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 2, 0)


def test_rename_model(api_client: TestClient) -> None:
    created = _create_model(api_client)

    response = api_client.patch(f"{MODELS}/{created['id']}", json={"name": "Pickup Y"})

    assert response.status_code == 200
    assert response.json() == created | {"name": "Pickup Y"}


def test_rename_model_to_an_existing_name_changes_nothing(api_client: TestClient) -> None:
    _create_model(api_client, "Sedan")
    pickup = _create_model(api_client, "Pickup X")

    response = api_client.patch(f"{MODELS}/{pickup['id']}", json={"name": "Sedan"})

    assert response.status_code == 409
    # The savepoint rolled the rename back: the model keeps its name.
    assert api_client.get(f"{MODELS}/{pickup['id']}").json()["name"] == "Pickup X"


def test_rename_unknown_model_returns_404(api_client: TestClient) -> None:
    assert api_client.patch(f"{MODELS}/999999", json={"name": "x"}).status_code == 404


def test_delete_model(api_client: TestClient) -> None:
    created = _create_model(api_client)

    assert api_client.delete(f"{MODELS}/{created['id']}").status_code == 204
    assert api_client.get(f"{MODELS}/{created['id']}").status_code == 404


def test_delete_model_with_trims_is_rejected(api_client: TestClient) -> None:
    model = _create_model(api_client)
    _create_trim(api_client, model["id"])

    response = api_client.delete(f"{MODELS}/{model['id']}")

    assert response.status_code == 409
    assert response.json() == {"detail": "Vehicle model 'Pickup X' has trims and cannot be deleted"}
    assert api_client.get(f"{MODELS}/{model['id']}").status_code == 200


def test_delete_unknown_model_returns_404(api_client: TestClient) -> None:
    assert api_client.delete(f"{MODELS}/999999").status_code == 404


# --- Trims --------------------------------------------------------------------


def test_create_trim_belongs_to_the_model_in_the_url(api_client: TestClient) -> None:
    model = _create_model(api_client)

    trim = _create_trim(api_client, model["id"])

    assert trim["id"] > 0
    assert trim | {"id": 0} == {"id": 0, "vehicle_model_id": model["id"], "name": "Base"}


def test_create_trim_for_unknown_model_returns_404(api_client: TestClient) -> None:
    response = api_client.post(f"{MODELS}/999999/trims", json={"name": "Base"})

    assert response.status_code == 404


def test_trim_names_are_unique_per_model_not_globally(api_client: TestClient) -> None:
    pickup = _create_model(api_client, "Pickup X")
    sedan = _create_model(api_client, "Sedan")
    _create_trim(api_client, pickup["id"], "Base")

    same_name_other_model = api_client.post(f"{MODELS}/{sedan['id']}/trims", json={"name": "Base"})
    same_name_same_model = api_client.post(f"{MODELS}/{pickup['id']}/trims", json={"name": "Base"})

    assert same_name_other_model.status_code == 201
    assert same_name_same_model.status_code == 409
    assert same_name_same_model.json() == {"detail": "Trim with name 'Base' already exists"}


def test_list_trims_returns_only_that_model_sorted_by_name(api_client: TestClient) -> None:
    pickup = _create_model(api_client, "Pickup X")
    sedan = _create_model(api_client, "Sedan")
    for name in ("Limited", "Base"):
        _create_trim(api_client, pickup["id"], name)
    _create_trim(api_client, sedan["id"], "Sport")

    body = api_client.get(f"{MODELS}/{pickup['id']}/trims").json()

    assert [t["name"] for t in body] == ["Base", "Limited"]


def test_list_trims_of_unknown_model_returns_404(api_client: TestClient) -> None:
    # An empty list would hide a wrong id.
    assert api_client.get(f"{MODELS}/999999/trims").status_code == 404


def test_get_trim_and_unknown_trim(api_client: TestClient) -> None:
    trim = _create_trim(api_client, _create_model(api_client)["id"])

    assert api_client.get(f"{TRIMS}/{trim['id']}").json() == trim
    missing = api_client.get(f"{TRIMS}/999999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Trim 999999 not found"}


def test_rename_trim(api_client: TestClient) -> None:
    trim = _create_trim(api_client, _create_model(api_client)["id"])

    response = api_client.patch(f"{TRIMS}/{trim['id']}", json={"name": "Limited"})

    assert response.status_code == 200
    assert response.json() == trim | {"name": "Limited"}


def test_rename_trim_to_a_name_taken_in_its_model_is_rejected(api_client: TestClient) -> None:
    model = _create_model(api_client)
    _create_trim(api_client, model["id"], "Base")
    sport = _create_trim(api_client, model["id"], "Sport")

    response = api_client.patch(f"{TRIMS}/{sport['id']}", json={"name": "Base"})

    assert response.status_code == 409
    assert api_client.get(f"{TRIMS}/{sport['id']}").json()["name"] == "Sport"


def test_trim_cannot_be_moved_to_another_model(api_client: TestClient) -> None:
    trim = _create_trim(api_client, _create_model(api_client, "Pickup X")["id"])
    other = _create_model(api_client, "Sedan")

    response = api_client.patch(f"{TRIMS}/{trim['id']}", json={"vehicle_model_id": other["id"]})

    assert response.status_code == 422


def test_delete_trim(api_client: TestClient) -> None:
    trim = _create_trim(api_client, _create_model(api_client)["id"])

    assert api_client.delete(f"{TRIMS}/{trim['id']}").status_code == 204
    assert api_client.get(f"{TRIMS}/{trim['id']}").status_code == 404


def test_delete_trim_with_units_is_rejected(api_client: TestClient, db_session: Session) -> None:
    trim = _create_trim(api_client, _create_model(api_client)["id"])
    db_session.add(
        Unit(
            vin="8AFZZZ12345670001",
            trim_id=trim["id"],
            color="White",
            status=UnitStatus.IN_YARD,
            dealer=Dealer(code="D001", name="X", province="X", city="X", yard_capacity=5),
            received_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )
    db_session.flush()

    response = api_client.delete(f"{TRIMS}/{trim['id']}")

    assert response.status_code == 409
    assert response.json() == {"detail": "Trim 'Base' has units and cannot be deleted"}


def test_delete_unknown_trim_returns_404(api_client: TestClient) -> None:
    assert api_client.delete(f"{TRIMS}/999999").status_code == 404
