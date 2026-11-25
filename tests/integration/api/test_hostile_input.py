"""Values the database cannot hold are the client's error: 422, never 500.

The cases come from the OpenAPI schema, so a new endpoint is covered as soon
as it exists. If it needs a body field that VALID does not have, the baseline
test fails and says so.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from stockpilot.core.config import Settings
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.main import create_app
from stockpilot.models import Dealer, Trim, Unit, VehicleModel

pytestmark = pytest.mark.integration

SPEC = create_app(Settings()).openapi()
VIN = "8AFZZZ12345670001"

# Two sizes: beyond INTEGER but still a BIGINT, and beyond both.
HUGE_INTEGERS = [2**31, -(2**31) - 1, 10**30, -(10**30)]
NUL_TEXT = "a\x00b"

# A valid value for every body field. Ids come from the data of each test.
VALID: dict[str, Any] = {
    "vin": "8AFZZZ12345670002",
    "color": "White",
    "code": "D002",
    "name": "Other",
    "province": "X",
    "city": "X",
    "yard_capacity": 5,
    "list_price_usd": "100.00",
}


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    parameters: dict[str, dict[str, Any]]
    body: dict[str, dict[str, Any]]

    def __str__(self) -> str:
        return f"{self.method.upper()} {self.path}"


@dataclass(frozen=True)
class Case:
    operation: Operation
    field: str
    value: int | str

    def __str__(self) -> str:
        return f"{self.operation} {self.field}={self.value!r}"


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = SPEC["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _types(schema: dict[str, Any]) -> set[str]:
    schema = _resolve(schema)
    if "anyOf" in schema:
        return {kind for option in schema["anyOf"] for kind in _types(option)}
    return {schema["type"]}


def _operations() -> Iterator[Operation]:
    for path, methods in SPEC["paths"].items():
        for method, operation in methods.items():
            body: dict[str, Any] = {}
            if "requestBody" in operation:
                content = operation["requestBody"]["content"]["application/json"]
                body = _resolve(content["schema"])["properties"]
            parameters = {p["name"]: p for p in operation.get("parameters", [])}
            yield Operation(method, path, parameters, body)


def _cases() -> Iterator[Case]:
    for operation in _operations():
        schemas = {name: p["schema"] for name, p in operation.parameters.items()}
        for field, schema in {**schemas, **operation.body}.items():
            types = _types(schema)
            if "integer" in types:
                yield from (Case(operation, field, value) for value in HUGE_INTEGERS)
            # An enum or a date already has a closed set of values.
            elif "string" in types and not {"enum", "format"} & _resolve(schema).keys():
                yield Case(operation, field, NUL_TEXT)


OPERATIONS = list(_operations())
CASES = list(_cases())


@pytest.fixture
def ids(db_session: Session) -> dict[str, int | str]:
    """Rows that exist: a 404 would hide that the value reaches the database."""
    dealer = Dealer(code="D001", name="X", province="X", city="X", yard_capacity=5)
    trim = Trim(vehicle_model=VehicleModel(name="Pickup X"), name="Base")
    unit = Unit(
        vin=VIN,
        trim=trim,
        color="White",
        status=UnitStatus.IN_YARD,
        dealer=dealer,
        received_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    db_session.add_all([dealer, unit])
    db_session.commit()
    return {
        "dealer_id": dealer.id,
        "trim_id": trim.id,
        "vehicle_model_id": trim.vehicle_model_id,
        "model_id": trim.vehicle_model_id,
        "unit_id": unit.id,
        "vin": VIN,
    }


def _send(
    client: TestClient,
    operation: Operation,
    ids: dict[str, int | str],
    hostile: dict[str, int | str] | None = None,
) -> tuple[int, str]:
    """Status and body of the response."""
    hostile = hostile or {}
    path = operation.path
    query: dict[str, str] = {}
    for name, parameter in operation.parameters.items():
        if parameter["in"] == "path":
            value = hostile.get(name, ids[name])
            path = path.replace(f"{{{name}}}", quote(str(value), safe=""))
        elif name in hostile:
            query[name] = str(hostile[name])
    body = {f: hostile.get(f, VALID.get(f, ids.get(f))) for f in operation.body}
    response = client.request(operation.method, path, params=query, json=body or None)
    return response.status_code, response.text


@pytest.mark.parametrize("operation", OPERATIONS, ids=str)
def test_baseline_request_is_well_formed(
    api_client: TestClient, ids: dict[str, int | str], operation: Operation
) -> None:
    # Without this, a 422 below could come from anything else in the request.
    status, text = _send(api_client, operation, ids)

    assert status in {200, 201, 204, 409}, text


@pytest.mark.parametrize("case", CASES, ids=str)
def test_value_the_database_cannot_hold_is_a_422(
    api_client: TestClient, ids: dict[str, int | str], case: Case
) -> None:
    status, text = _send(api_client, case.operation, ids, {case.field: case.value})

    assert status == 422, text
