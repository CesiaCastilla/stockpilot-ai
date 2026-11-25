from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from stockpilot.api.dependencies import get_now
from stockpilot.core.config import Settings
from stockpilot.core.database import get_session
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.main import create_app
from stockpilot.models import Dealer, Sale, Trim, Unit, VehicleModel

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)


class World:
    """A dealer, a second one that must never leak into its numbers, and three trims."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.serial = count(1)
        pickup = VehicleModel(name="Pickup X")
        self.base = Trim(vehicle_model=pickup, name="Base")
        self.limited = Trim(vehicle_model=pickup, name="Limited")
        self.sedan = Trim(vehicle_model=VehicleModel(name="Sedan"), name="Base")
        self.dealer = Dealer(code="D001", name="X", province="X", city="X", yard_capacity=50)
        self.other = Dealer(code="D002", name="Y", province="Y", city="Y", yard_capacity=50)
        session.add_all([self.base, self.limited, self.sedan, self.dealer, self.other])
        session.flush()

    def unit(
        self,
        trim: Trim,
        status: UnitStatus = UnitStatus.IN_YARD,
        dealer: Dealer | None = None,
        age: timedelta = timedelta(days=10),
    ) -> Unit:
        unit = Unit(
            vin=f"8AFZZZ1234567{next(self.serial):04d}",
            trim=trim,
            color="White",
            status=status,
            dealer=dealer or self.dealer,
            received_at=NOW - age,
        )
        self.session.add(unit)
        self.session.flush()
        return unit

    def sales(
        self,
        trim: Trim,
        n: int = 1,
        dealer: Dealer | None = None,
        ago: timedelta = timedelta(days=5),
    ) -> None:
        for _ in range(n):
            unit = self.unit(trim, UnitStatus.SOLD, dealer, age=ago + timedelta(days=30))
            self.session.add(Sale(unit=unit, dealer=unit.dealer, sold_at=NOW - ago))
        self.session.flush()


@pytest.fixture
def world(db_session: Session) -> World:
    return World(db_session)


def _app(
    postgres_url: str, session: Session, *, window: int = 60, low: int = 15, aged: int = 90
) -> FastAPI:
    settings = Settings(
        database_url=postgres_url,
        sales_window_days=window,
        low_coverage_days=low,
        aged_after_days=aged,
    )
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_now] = lambda: NOW
    return app


@pytest.fixture
def client(postgres_url: str, db_session: Session) -> Iterator[TestClient]:
    with TestClient(_app(postgres_url, db_session)) as client:
        yield client


def _coverage(client: TestClient, dealer: Dealer) -> dict[str, Any]:
    response = client.get(f"/api/v1/dealers/{dealer.id}/coverage")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _only_trim(client: TestClient, world: World) -> dict[str, Any]:
    trims = _coverage(client, world.dealer)["trims"]
    assert len(trims) == 1
    row: dict[str, Any] = trims[0]
    return row


# --- Coverage -----------------------------------------------------------------


def test_full_response(client: TestClient, world: World) -> None:
    for age in (10, 100):
        world.unit(world.base, age=timedelta(days=age))
    world.unit(world.base, UnitStatus.IN_TRANSIT)
    world.unit(world.base, UnitStatus.RESERVED)
    world.sales(world.base, 4)

    assert _coverage(client, world.dealer) == {
        "dealer_id": world.dealer.id,
        "dealer_code": "D001",
        "yard_capacity": 50,
        "stock_units": 4,
        "as_of": "2026-11-24T12:00:00Z",
        "sales_window_days": 60,
        "low_coverage_days": 15,
        "aged_after_days": 90,
        "trims": [
            {
                "vehicle_model_id": world.base.vehicle_model_id,
                "vehicle_model_name": "Pickup X",
                "trim_id": world.base.id,
                "trim_name": "Base",
                "in_yard": 2,
                "in_transit": 1,
                "reserved": 1,
                "recent_sales": 4,
                # 3 available (the reserved one is not) / (4 sales / 60 days)
                "coverage_days": 45.0,
                "status": "ok",
                "aged_units": 1,
                "oldest_unit_days": 100,
            }
        ],
    }


def test_sold_out_trim_is_listed_as_a_shortage(client: TestClient, world: World) -> None:
    world.sales(world.base, 2)

    row = _only_trim(client, world)

    assert (row["in_yard"], row["coverage_days"], row["status"]) == (0, 0.0, "shortage")
    assert row["oldest_unit_days"] is None


def test_incoming_units_count_as_coverage(client: TestClient, world: World) -> None:
    world.unit(world.base, UnitStatus.IN_TRANSIT)
    world.sales(world.base, 1)

    row = _only_trim(client, world)

    assert (row["coverage_days"], row["status"]) == (60.0, "ok")


def test_stock_without_sales_has_no_coverage(client: TestClient, world: World) -> None:
    world.unit(world.base)

    row = _only_trim(client, world)

    assert (row["coverage_days"], row["status"]) == (None, "no_sales")


def test_only_sales_inside_the_window_count(client: TestClient, world: World) -> None:
    world.unit(world.base)
    world.sales(world.base, ago=timedelta(days=60))  # the edge is included
    world.sales(world.base, ago=timedelta(days=60) + SECOND)
    world.sales(world.base, ago=-SECOND)  # in the future

    assert _only_trim(client, world)["recent_sales"] == 1


def test_other_dealers_do_not_count(client: TestClient, world: World) -> None:
    world.unit(world.base)
    world.unit(world.base, dealer=world.other)
    world.sales(world.base, 3, dealer=world.other)

    row = _only_trim(client, world)

    assert (row["in_yard"], row["recent_sales"]) == (1, 0)


def test_stock_and_sales_do_not_multiply_each_other(client: TestClient, world: World) -> None:
    # A single query joining both to trims would report 3 x 2 = 6 of each.
    for _ in range(3):
        world.unit(world.base)
    world.sales(world.base, 2)

    row = _only_trim(client, world)

    assert (row["in_yard"], row["recent_sales"]) == (3, 2)


def test_most_urgent_trims_come_first_and_those_without_sales_last(
    client: TestClient, world: World
) -> None:
    world.unit(world.base)  # no sales
    world.unit(world.limited)
    world.sales(world.limited, 1)  # 60 days
    world.sales(world.sedan, 1)  # sold out

    trims = _coverage(client, world.dealer)["trims"]

    assert [t["trim_id"] for t in trims] == [world.sedan.id, world.limited.id, world.base.id]


# --- Aging --------------------------------------------------------------------


def test_aged_means_strictly_more_than_the_threshold(client: TestClient, world: World) -> None:
    world.unit(world.base, age=timedelta(days=90))
    world.unit(world.base, age=timedelta(days=90) + SECOND)

    assert _only_trim(client, world)["aged_units"] == 1


@pytest.mark.parametrize("status", [UnitStatus.RESERVED, UnitStatus.IN_TRANSIT])
def test_only_available_units_in_the_yard_age(
    client: TestClient, world: World, status: UnitStatus
) -> None:
    world.unit(world.base, status, age=timedelta(days=200))

    row = _only_trim(client, world)

    assert (row["aged_units"], row["oldest_unit_days"]) == (0, None)


# --- Dealer -------------------------------------------------------------------


def test_empty_dealer_and_unknown_dealer(client: TestClient, world: World) -> None:
    body = _coverage(client, world.dealer)
    assert (body["stock_units"], body["trims"]) == (0, [])

    response = client.get("/api/v1/dealers/999999/coverage")
    assert response.status_code == 404
    assert response.json() == {"detail": "Dealer 999999 not found"}


def test_thresholds_come_from_settings(
    postgres_url: str, db_session: Session, world: World
) -> None:
    world.unit(world.base, age=timedelta(days=20))
    world.sales(world.base, 1)
    app = _app(postgres_url, db_session, window=30, low=31, aged=15)

    with TestClient(app) as client:
        row = _only_trim(client, world)

    assert (row["coverage_days"], row["status"], row["aged_units"]) == (30.0, "shortage", 1)


def test_the_real_clock_is_used_by_default(api_client: TestClient, world: World) -> None:
    before = datetime.now(UTC)

    as_of = datetime.fromisoformat(_coverage(api_client, world.dealer)["as_of"])

    assert before <= as_of <= datetime.now(UTC)
