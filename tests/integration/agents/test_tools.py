"""Tools that read the database, against a real PostgreSQL. External APIs are fakes."""

import json
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from stockpilot.agents.tools import ToolContext, build_tools
from stockpilot.core.config import Settings
from stockpilot.domain.delivery import Holiday
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.domain.exchange_rate import ExchangeRate
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.domain.vin import model_year
from stockpilot.domain.vin_info import VehicleInfo
from stockpilot.models import Dealer, Sale, Trim, Unit, VehicleModel
from stockpilot.schemas.coverage import NetworkFilters
from stockpilot.services.coverage import CoverageService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # not the .env of the developer
POLICY = SETTINGS.coverage_policy()
RATE = ExchangeRate(buy=Decimal("1565"), sell=Decimal("1600"), updated_at=NOW)


class FakeDecoder:
    def __init__(self) -> None:
        self.info = VehicleInfo()
        self.asked: list[str] = []

    def decode(self, vin: str) -> VehicleInfo:
        self.asked.append(vin)
        return self.info


class FakeRates:
    def __init__(self, rate: ExchangeRate | None = RATE) -> None:
        self.rate = rate

    def official_rate(self) -> ExchangeRate:
        if self.rate is None:
            raise ExternalServiceError("DolarApi", "timeout")
        return self.rate


class NoHolidays:
    def holidays(self, year: int) -> Collection[Holiday]:
        return []


class World:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.serial = count(1)
        self.trim = Trim(
            vehicle_model=VehicleModel(name="Aconcagua"), name="SR", list_price_usd=Decimal(30000)
        )
        # About 10 km apart.
        self.north = self.dealer("D001", -34.50, -58.50)
        self.south = self.dealer("D002", -34.59, -58.50)
        self.unlocated = self.dealer("D003", None, None)
        session.add(self.trim)
        session.flush()

    def dealer(self, code: str, latitude: float | None, longitude: float | None) -> Dealer:
        dealer = Dealer(
            code=code,
            name=code,
            province="X",
            city="X",
            yard_capacity=50,
            latitude=latitude,
            longitude=longitude,
        )
        self.session.add(dealer)
        self.session.flush()
        return dealer

    def unit(
        self, dealer: Dealer | None, age_days: int, status: UnitStatus = UnitStatus.IN_YARD
    ) -> Unit:
        unit = Unit(
            vin=f"8AFZZZ1234567{next(self.serial):04d}",
            trim=self.trim,
            color="White",
            status=status,
            dealer=dealer,
            received_at=NOW - timedelta(days=age_days),
        )
        self.session.add(unit)
        self.session.flush()
        return unit

    def sale(self, dealer: Dealer) -> None:
        unit = self.unit(dealer, 40, UnitStatus.SOLD)
        self.session.add(Sale(unit=unit, dealer=dealer, sold_at=NOW - timedelta(days=3)))
        self.session.flush()


@pytest.fixture
def world(db_session: Session) -> World:
    return World(db_session)


def _build(
    db_session: Session, *, decoder: FakeDecoder, rates: FakeRates, now: datetime = NOW
) -> dict[str, BaseTool]:
    connection = db_session.connection()

    def session_factory() -> Session:
        # Sees what the test wrote, and its writes are rolled back with it.
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    context = ToolContext(
        session_factory=session_factory,
        policy=POLICY,
        distribution_center=SETTINGS.distribution_center(),
        vin_decoder=decoder,
        holidays=NoHolidays(),
        rates=rates,
        clock=lambda: now,
    )
    return {tool.name: tool for tool in build_tools(context)}


@pytest.fixture
def decoder() -> FakeDecoder:
    return FakeDecoder()


@pytest.fixture
def rates() -> FakeRates:
    return FakeRates()


@pytest.fixture
def tools(db_session: Session, decoder: FakeDecoder, rates: FakeRates) -> dict[str, BaseTool]:
    return _build(db_session, decoder=decoder, rates=rates)


def _call(tools: dict[str, BaseTool], name: str, **args: Any) -> Any:
    answer = tools[name].invoke(args)
    assert isinstance(answer, str)
    return json.loads(answer)


# --- Shortages and aged units -------------------------------------------------


def test_shortages_are_those_of_the_service(
    tools: dict[str, BaseTool], world: World, db_session: Session
) -> None:
    for dealer in (world.north, world.south, world.unlocated):
        world.sale(dealer)
    expected = CoverageService(db_session, POLICY).shortages(NetworkFilters(), now=NOW)

    answer = _call(tools, "get_dealers_with_shortage")

    assert answer == {**expected.model_dump(mode="json"), "total": 3}


def test_shortages_are_cut_to_the_limit_but_the_total_counts_all(
    tools: dict[str, BaseTool], world: World
) -> None:
    for dealer in (world.north, world.south, world.unlocated):
        world.sale(dealer)

    answer = _call(tools, "get_dealers_with_shortage", limit=2)

    assert [s["dealer_code"] for s in answer["items"]] == ["D001", "D002"]
    assert answer["total"] == 3


def test_shortages_by_trim(tools: dict[str, BaseTool], world: World) -> None:
    world.sale(world.north)

    assert _call(tools, "get_dealers_with_shortage", trim_id=world.trim.id)["total"] == 1
    assert _call(tools, "get_dealers_with_shortage", trim_id=world.trim.id + 1)["total"] == 0


def test_aged_units(tools: dict[str, BaseTool], world: World) -> None:
    old = world.unit(world.north, 120)
    center = world.unit(None, 100)
    world.unit(world.south, 10)

    answer = _call(tools, "get_aged_units")

    assert [(u["vin"], u["dealer_id"], u["days_in_stock"]) for u in answer["items"]] == [
        (old.vin, world.north.id, 120),
        (center.vin, None, 100),
    ]
    assert (answer["total"], answer["min_days"]) == (2, 90)
    assert "offset" not in answer


def test_aged_units_with_min_days_and_limit(tools: dict[str, BaseTool], world: World) -> None:
    for age in (5, 50, 150):
        world.unit(world.north, age)

    answer = _call(tools, "get_aged_units", min_days=0, limit=1)

    assert [u["days_in_stock"] for u in answer["items"]] == [150]
    assert answer["total"] == 3


def test_aged_units_by_trim(tools: dict[str, BaseTool], world: World, db_session: Session) -> None:
    other = Trim(vehicle_model=world.trim.vehicle_model, name="Other")
    db_session.add(other)
    world.unit(world.north, 100)

    assert _call(tools, "get_aged_units", trim_id=world.trim.id)["total"] == 1
    assert _call(tools, "get_aged_units", trim_id=other.id)["total"] == 0


def test_both_lists_by_model(tools: dict[str, BaseTool], world: World) -> None:
    world.unit(world.north, 100)
    world.sale(world.south)
    model = world.trim.vehicle_model_id

    for name in ("get_aged_units", "get_dealers_with_shortage"):
        assert _call(tools, name, vehicle_model_id=model)["total"] == 1, name
        assert _call(tools, name, vehicle_model_id=model + 1)["total"] == 0, name


# --- VIN ----------------------------------------------------------------------


def test_decode_vin_completes_vpic_with_the_catalog(
    tools: dict[str, BaseTool], world: World, decoder: FakeDecoder
) -> None:
    unit = world.unit(world.north, 10)
    decoder.info = VehicleInfo(make="ACME")

    answer = _call(tools, "decode_vin", vin=unit.vin.lower())

    assert answer["info"] == {
        "make": "ACME",
        "model": "Aconcagua",
        "year": model_year(unit.vin, today=NOW.date()),
    }
    assert (answer["vin"], answer["source"], answer["vpic_checked"]) == (unit.vin, "combined", True)


def test_decode_vin_reads_the_year_with_the_clock_of_the_run(
    db_session: Session, world: World, decoder: FakeDecoder, rates: FakeRates
) -> None:
    unit = world.unit(world.north, 10)
    unit.vin = "8AFZZZ123A5670001"  # year code A: 2010 or 2040
    db_session.flush()
    later = datetime(2040, 1, 1, tzinfo=UTC)

    answer = _call(
        _build(db_session, decoder=decoder, rates=rates, now=later), "decode_vin", vin=unit.vin
    )

    # With the real date, 2040 is still too far away to be a model year.
    assert model_year(unit.vin, today=NOW.date()) is None
    assert answer["info"]["year"] == 2040


def test_an_invalid_vin_goes_back_to_the_model_without_asking_vpic(
    tools: dict[str, BaseTool], decoder: FakeDecoder
) -> None:
    answer = _call(tools, "decode_vin", vin="TOO-SHORT")

    assert "Invalid VIN" in answer["error"]
    assert decoder.asked == []


# --- Distance -----------------------------------------------------------------


def test_distance_between_dealers(tools: dict[str, BaseTool], world: World) -> None:
    answer = _call(
        tools, "get_distance", origin_dealer_id=world.north.id, destination_dealer_id=world.south.id
    )

    assert answer == {"km": 10.0, "kind": "straight line"}


def test_distance_from_the_distribution_center(tools: dict[str, BaseTool], world: World) -> None:
    answer = _call(
        tools, "get_distance", origin_dealer_id=None, destination_dealer_id=world.north.id
    )

    assert answer["km"] > 100  # the fictitious center is in the middle of the country


@pytest.mark.parametrize(
    ("dealer", "message"),
    [("unknown", "Dealer 999999 not found"), ("unlocated", "D003 has no coordinates")],
)
def test_distance_errors_go_back_to_the_model(
    tools: dict[str, BaseTool], world: World, dealer: str, message: str
) -> None:
    dealer_id = 999_999 if dealer == "unknown" else world.unlocated.id

    answer = _call(
        tools, "get_distance", origin_dealer_id=world.north.id, destination_dealer_id=dealer_id
    )

    assert message in answer["error"]


# --- Aged stock value ---------------------------------------------------------


def test_aged_stock_value_in_dollars_and_pesos(tools: dict[str, BaseTool], world: World) -> None:
    world.unit(world.north, 100)
    world.unit(world.north, 200)
    world.unit(world.north, 10)  # not aged

    answer = _call(tools, "get_aged_stock_value", dealer_id=world.north.id)

    assert answer == {
        "units": 2,
        "units_without_price": 0,
        "usd": "60000.00",
        "ars": "96000000.00",
        "rate": {"buy": "1565", "sell": "1600", "updated_at": "2026-11-26T12:00:00Z"},
    }


def test_aged_stock_value_without_the_rate(
    tools: dict[str, BaseTool], world: World, rates: FakeRates
) -> None:
    world.unit(world.north, 100)
    rates.rate = None

    answer = _call(tools, "get_aged_stock_value", dealer_id=world.north.id)

    assert (answer["usd"], answer["ars"], answer["rate"]) == ("30000.00", None, None)


def test_an_error_sent_back_to_the_model_is_logged(tools: dict[str, BaseTool]) -> None:
    with capture_logs() as logs:
        _call(tools, "get_aged_stock_value", dealer_id=999_999)

    [log] = logs
    assert (log["event"], log["tool"], log["log_level"]) == (
        "agent_tool_failed",
        "get_aged_stock_value",
        "warning",
    )
    assert log["error"] == "Dealer 999999 not found"
    assert log["args"] == {"dealer_id": 999_999}


def test_aged_stock_value_of_an_unknown_dealer(tools: dict[str, BaseTool]) -> None:
    answer = _call(tools, "get_aged_stock_value", dealer_id=999_999)

    assert answer == {"error": "Dealer 999999 not found"}


def test_each_call_opens_its_own_session_and_closes_it(
    db_session: Session, world: World, decoder: FakeDecoder, rates: FakeRates
) -> None:
    connection = db_session.connection()
    opened: list[Session] = []

    def session_factory() -> Session:
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        opened.append(session)
        return session

    context = ToolContext(
        session_factory=session_factory,
        policy=POLICY,
        distribution_center=SETTINGS.distribution_center(),
        vin_decoder=decoder,
        holidays=NoHolidays(),
        rates=rates,
        clock=lambda: NOW,
    )
    tools = {tool.name: tool for tool in build_tools(context)}
    world.unit(world.north, 100)

    _call(tools, "get_aged_units")
    _call(tools, "get_aged_stock_value", dealer_id=world.north.id)
    _call(tools, "get_distance", origin_dealer_id=world.north.id, destination_dealer_id=999_999)

    # No transaction stays open while the model thinks, not even after an error.
    assert len(opened) == 3
    assert not any(session.in_transaction() for session in opened)
