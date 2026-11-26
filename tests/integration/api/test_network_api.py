import random
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
from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.main import create_app
from stockpilot.models import Dealer, Sale, TransferOrder, Trim, Unit, VehicleModel

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)
CENTER = None  # dealer_id of a unit at the distribution center


class World:
    """Two dealers, the distribution center and three trims."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.serial = count(1)
        pickup = VehicleModel(name="Pickup X")
        self.base = Trim(vehicle_model=pickup, name="Base")
        self.limited = Trim(vehicle_model=pickup, name="Limited")
        self.sedan = Trim(vehicle_model=VehicleModel(name="Sedan"), name="Base")
        self.dealer = self.new_dealer("D001")
        self.other = self.new_dealer("D002")
        session.add_all([self.base, self.limited, self.sedan])
        session.flush()

    def new_dealer(self, code: str, yard_capacity: int = 50) -> Dealer:
        dealer = Dealer(code=code, name=code, province="X", city="X", yard_capacity=yard_capacity)
        self.session.add(dealer)
        self.session.flush()
        return dealer

    def unit(
        self,
        trim: Trim,
        status: UnitStatus = UnitStatus.IN_YARD,
        dealer: Dealer | None = None,
        *,
        at_center: bool = False,
        age: timedelta = timedelta(days=10),
        color: str = "White",
    ) -> Unit:
        unit = Unit(
            vin=f"8AFZZZ1234567{next(self.serial):04d}",
            trim=trim,
            color=color,
            status=status,
            dealer=None if at_center else dealer or self.dealer,
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

    def order(
        self,
        unit: Unit,
        destination: Dealer,
        status: TransferOrderStatus = TransferOrderStatus.PENDING,
    ) -> TransferOrder:
        closed = status is not TransferOrderStatus.PENDING
        order = TransferOrder(
            unit=unit,
            origin_dealer=unit.dealer,
            destination_dealer=destination,
            status=status,
            reason="Shortage",
            decided_at=NOW if closed else None,
            rejection_reason="No" if status is TransferOrderStatus.REJECTED else None,
        )
        self.session.add(order)
        self.session.flush()
        return order


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


def _get(client: TestClient, path: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/v1/network/{path}", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _shortages(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = _get(client, "shortages", **params)["items"]
    return items


def _short_trims(client: TestClient, **params: Any) -> list[tuple[str, int]]:
    return [(s["dealer_code"], s["trim_id"]) for s in _shortages(client, **params)]


def _aged_vins(client: TestClient, **params: Any) -> list[str]:
    return [u["vin"] for u in _get(client, "aged-units", **params)["items"]]


# --- Shortages ----------------------------------------------------------------


def test_full_shortage_response(client: TestClient, world: World) -> None:
    world.unit(world.base)
    world.unit(world.base, UnitStatus.IN_TRANSIT)
    world.unit(world.base, UnitStatus.RESERVED)
    world.unit(world.sedan)  # another trim: takes a place in the yard
    world.sales(world.base, 12)

    assert _get(client, "shortages") == {
        "as_of": "2026-11-24T12:00:00Z",
        "sales_window_days": 60,
        "low_coverage_days": 15,
        "items": [
            {
                "dealer_id": world.dealer.id,
                "dealer_code": "D001",
                "vehicle_model_id": world.base.vehicle_model_id,
                "vehicle_model_name": "Pickup X",
                "trim_id": world.base.id,
                "trim_name": "Base",
                "in_yard": 1,
                "in_transit": 1,
                "recent_sales": 12,
                # 2 available (the reserved one is not) / (12 sales / 60 days)
                "coverage_days": 10.0,
                "pending_arrivals": 0,
                # 3 units are 15 days
                "units_needed": 1,
                # 50 - 4 units in stock (the reserved one has a place)
                "free_places": 46,
            }
        ],
    }


def test_only_trims_below_the_threshold_are_listed(client: TestClient, world: World) -> None:
    world.sales(world.base, 4)  # sold out: the most urgent case
    for _ in range(5):
        world.unit(world.limited)
    world.sales(world.limited, 20)  # 5 units are exactly 15 days: not short
    world.unit(world.sedan)  # stock without sales: no coverage, not a shortage

    [shortage] = _shortages(client)

    assert (shortage["trim_id"], shortage["in_yard"]) == (world.base.id, 0)
    assert (shortage["coverage_days"], shortage["units_needed"]) == (0.0, 1)


def test_only_recent_sales_count(client: TestClient, world: World) -> None:
    world.sales(world.base, ago=timedelta(days=60))  # first moment of the window
    world.sales(world.limited, ago=timedelta(days=60) + SECOND)
    world.sales(world.sedan, ago=-SECOND)  # after now: not sold yet

    assert _short_trims(client) == [("D001", world.base.id)]


def test_a_sale_counts_where_it_happened(
    client: TestClient, world: World, db_session: Session
) -> None:
    # The unit is listed at D001 but was sold at D002: the demand is at D002.
    unit = world.unit(world.base, UnitStatus.SOLD)
    db_session.add(Sale(unit=unit, dealer=world.other, sold_at=NOW - timedelta(days=5)))
    db_session.flush()

    assert _short_trims(client) == [("D002", world.base.id)]


def test_units_elsewhere_do_not_cover_a_dealer(client: TestClient, world: World) -> None:
    for _ in range(10):
        world.unit(world.base, at_center=True)
        world.unit(world.base, dealer=world.other)
    world.unit(world.base, UnitStatus.SOLD)
    world.unit(world.base, UnitStatus.DELIVERED)
    world.sales(world.base, 4)

    assert _shortages(client)[0]["coverage_days"] == 0.0


def test_pending_orders_reduce_what_is_still_needed(client: TestClient, world: World) -> None:
    world.unit(world.base)
    world.sales(world.base, 12)  # 5 days: 2 more units are needed
    world.order(world.unit(world.base, dealer=world.other), world.dealer)
    # None of these is a pending arrival of Base at D001.
    for closed in (TransferOrderStatus.APPROVED, TransferOrderStatus.REJECTED):
        world.order(world.unit(world.base, dealer=world.other), world.dealer, closed)
    world.order(world.unit(world.limited, dealer=world.other), world.dealer)
    world.order(world.unit(world.base, at_center=True), world.new_dealer("D003"))

    [shortage] = _shortages(client)

    # Not in stock yet: the coverage is what the dealer has today.
    assert shortage["coverage_days"] == 5.0
    assert shortage["pending_arrivals"] == 1
    assert shortage["units_needed"] == 1


def test_pending_orders_can_cover_the_whole_shortage(client: TestClient, world: World) -> None:
    world.sales(world.base, 4)
    world.order(world.unit(world.base, dealer=world.other), world.dealer)

    [shortage] = _shortages(client)

    # Still listed: nothing arrives unless a person approves the order.
    assert shortage["pending_arrivals"] == 1
    assert shortage["units_needed"] == 0


def test_free_places_count_every_trim_and_every_pending_order(
    client: TestClient, world: World
) -> None:
    small = world.new_dealer("D003", yard_capacity=10)
    world.sales(world.base, 4, dealer=small)
    world.unit(world.sedan, dealer=small)
    world.unit(world.sedan, UnitStatus.IN_TRANSIT, dealer=small)
    world.unit(world.sedan, UnitStatus.RESERVED, dealer=small)
    world.order(world.unit(world.limited), small)
    world.order(world.unit(world.base), small)
    # Do not take a place: sold, or no longer pending.
    world.unit(world.sedan, UnitStatus.DELIVERED, dealer=small)
    world.order(world.unit(world.sedan), small, TransferOrderStatus.REJECTED)

    [shortage] = _shortages(client)

    assert shortage["free_places"] == 10 - 3 - 2


def test_a_yard_over_its_capacity_has_no_free_places(
    client: TestClient, world: World, db_session: Session
) -> None:
    world.unit(world.base)
    world.unit(world.sedan)
    world.sales(world.base, 8)
    world.dealer.yard_capacity = 1  # lowered below the stock it already has
    db_session.flush()

    assert _shortages(client)[0]["free_places"] == 0


def test_lowest_coverage_first_then_most_sales(client: TestClient, world: World) -> None:
    third = world.new_dealer("D003")
    world.sales(world.base, 2, dealer=world.other)  # 0 days, 2 sales
    world.sales(world.base, 2, dealer=world.dealer)  # same: D001 before D002
    world.sales(world.limited, 9, dealer=world.dealer)  # 0 days, 9 sales
    world.unit(world.sedan, dealer=third)
    world.sales(world.sedan, 8, dealer=third)  # 7.5 days

    assert _short_trims(client) == [
        ("D001", world.limited.id),
        ("D001", world.base.id),
        ("D002", world.base.id),
        ("D003", world.sedan.id),
    ]


def test_shortages_by_trim_or_model(client: TestClient, world: World) -> None:
    for trim in (world.base, world.limited, world.sedan):
        world.sales(trim)
    pickup = world.base.vehicle_model_id

    assert _short_trims(client, trim_id=world.limited.id) == [("D001", world.limited.id)]
    assert {t for _, t in _short_trims(client, vehicle_model_id=pickup)} == {
        world.base.id,
        world.limited.id,
    }
    assert _short_trims(client, trim_id=world.sedan.id, vehicle_model_id=pickup) == []
    assert _short_trims(client, trim_id=999_999) == []


def test_shortage_thresholds_come_from_the_settings(
    postgres_url: str, db_session: Session, world: World
) -> None:
    for _ in range(5):
        world.unit(world.base)
    world.sales(world.base, 10)  # 30 days with a 60-day window
    world.sales(world.limited, ago=timedelta(days=40))

    with TestClient(_app(postgres_url, db_session, window=30, low=31)) as client:
        body = _get(client, "shortages")

    assert (body["sales_window_days"], body["low_coverage_days"]) == (30, 31)
    # 5 units / (10 sales / 30 days) = 15 days; the Limited sale is out of the window.
    assert [(s["trim_id"], s["coverage_days"]) for s in body["items"]] == [(world.base.id, 15.0)]


def test_shortages_agree_with_the_coverage_of_each_dealer(client: TestClient, world: World) -> None:
    """Two queries, one rule: the network must list exactly what each dealer shows as short."""
    rng = random.Random(7)
    dealers = [world.dealer, world.other, *(world.new_dealer(f"D{n:03d}") for n in range(3, 9))]
    trims = [world.base, world.limited, world.sedan]
    statuses = [UnitStatus.IN_YARD, UnitStatus.IN_TRANSIT, UnitStatus.RESERVED]
    for dealer in dealers:
        for trim in trims:
            for _ in range(rng.randint(0, 6)):
                world.unit(trim, rng.choice(statuses), dealer)
            world.sales(trim, rng.randint(0, 12), dealer, ago=timedelta(days=rng.randint(1, 80)))

    expected = {
        (dealer.code, t["trim_id"], t["coverage_days"])
        for dealer in dealers
        for t in client.get(f"/api/v1/dealers/{dealer.id}/coverage").json()["trims"]
        if t["status"] == "shortage"
    }
    network = {(s["dealer_code"], s["trim_id"], s["coverage_days"]) for s in _shortages(client)}

    assert len(expected) > 5  # the data has shortages to compare
    assert network == expected


# --- Aged units ---------------------------------------------------------------


def test_full_aged_units_response(client: TestClient, world: World) -> None:
    unit = world.unit(world.sedan, age=timedelta(days=120, hours=5), color="Red")
    world.unit(world.base)  # 10 days: not aged

    assert _get(client, "aged-units") == {
        "items": [
            {
                "unit_id": unit.id,
                "vin": unit.vin,
                "vehicle_model_id": world.sedan.vehicle_model_id,
                "vehicle_model_name": "Sedan",
                "trim_id": world.sedan.id,
                "trim_name": "Base",
                "color": "Red",
                "dealer_id": world.dealer.id,
                "dealer_code": "D001",
                "received_at": "2026-07-27T07:00:00Z",
                "days_in_stock": 120,
            }
        ],
        "total": 1,
        "limit": 50,
        "offset": 0,
        "as_of": "2026-11-24T12:00:00Z",
        "min_days": 90,
    }


def test_aged_means_more_than_the_threshold(client: TestClient, world: World) -> None:
    world.unit(world.base, age=timedelta(days=90))
    older = world.unit(world.base, age=timedelta(days=90) + SECOND)

    assert _aged_vins(client) == [older.vin]


def test_only_units_in_a_yard_can_be_moved(client: TestClient, world: World) -> None:
    old = timedelta(days=200)
    in_yard = world.unit(world.base, age=old)
    for status in (
        UnitStatus.IN_TRANSIT,
        UnitStatus.RESERVED,
        UnitStatus.SOLD,
        UnitStatus.DELIVERED,
    ):
        world.unit(world.base, status, age=old)

    assert _aged_vins(client) == [in_yard.vin]


def test_units_at_the_distribution_center_are_included(client: TestClient, world: World) -> None:
    unit = world.unit(world.base, at_center=True, age=timedelta(days=100))

    [item] = _get(client, "aged-units")["items"]

    assert (item["vin"], item["dealer_id"], item["dealer_code"]) == (unit.vin, None, None)


def test_units_with_a_pending_order_are_left_out(client: TestClient, world: World) -> None:
    old = timedelta(days=100)
    world.order(world.unit(world.base, age=old), world.other)
    approved_back = world.unit(world.base, age=old)
    world.order(approved_back, world.other, TransferOrderStatus.APPROVED)
    rejected = world.unit(world.base, age=old)
    world.order(rejected, world.other, TransferOrderStatus.REJECTED)

    # A closed order does not stop a new proposal.
    assert _aged_vins(client) == [approved_back.vin, rejected.vin]


def test_min_days(client: TestClient, world: World) -> None:
    ages = [timedelta(days=d) for d in (0, 10, 30, 100)]
    units = [world.unit(world.base, age=age + SECOND) for age in ages]
    oldest_first = [u.vin for u in reversed(units)]

    assert _aged_vins(client, min_days=100) == oldest_first[:1]
    assert _aged_vins(client, min_days=30) == oldest_first[:2]
    assert _aged_vins(client, min_days=10) == oldest_first[:3]
    # Every unit that could be moved today, the youngest included.
    assert _aged_vins(client, min_days=0) == oldest_first
    assert _get(client, "aged-units", min_days=0)["min_days"] == 0


def test_aged_threshold_comes_from_the_settings(
    postgres_url: str, db_session: Session, world: World
) -> None:
    unit = world.unit(world.base, age=timedelta(days=31))

    with TestClient(_app(postgres_url, db_session, aged=30)) as client:
        body = _get(client, "aged-units")

    assert body["min_days"] == 30
    assert [u["vin"] for u in body["items"]] == [unit.vin]


def test_aged_units_oldest_first_and_paginated(client: TestClient, world: World) -> None:
    received = [world.unit(world.base, age=timedelta(days=d)) for d in (95, 300, 120, 200)]
    oldest_first = [u.vin for u in sorted(received, key=lambda u: u.received_at)]

    page = _get(client, "aged-units", limit=2, offset=1)

    assert [u["vin"] for u in page["items"]] == oldest_first[1:3]
    assert (page["total"], page["limit"], page["offset"]) == (4, 2, 1)


def test_aged_units_by_trim_or_model(client: TestClient, world: World) -> None:
    old = timedelta(days=100)
    base, limited, sedan = (
        world.unit(t, age=old) for t in (world.base, world.limited, world.sedan)
    )
    pickup = world.base.vehicle_model_id

    assert _aged_vins(client, trim_id=world.sedan.id) == [sedan.vin]
    assert _aged_vins(client, vehicle_model_id=pickup) == [base.vin, limited.vin]
    assert _get(client, "aged-units", vehicle_model_id=pickup, trim_id=world.sedan.id)["total"] == 0


@pytest.mark.parametrize("min_days", [-1, 3651])
def test_min_days_out_of_range_is_rejected(client: TestClient, min_days: int) -> None:
    response = client.get("/api/v1/network/aged-units", params={"min_days": min_days})

    assert response.status_code == 422


def test_ten_years_is_accepted(client: TestClient, world: World) -> None:
    assert _get(client, "aged-units", min_days=3650)["total"] == 0
