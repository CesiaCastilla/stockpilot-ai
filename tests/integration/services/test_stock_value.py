from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from stockpilot.domain.coverage import CoveragePolicy
from stockpilot.domain.errors import ExternalServiceError, NotFoundError
from stockpilot.domain.exchange_rate import ExchangeRate
from stockpilot.domain.stock_value import AgedStockValue
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Trim, Unit, VehicleModel
from stockpilot.services.coverage import CoverageService
from stockpilot.services.stock_value import StockValueService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
POLICY = CoveragePolicy(sales_window_days=60, low_coverage_days=15, aged_after_days=90)
RATE = ExchangeRate(buy=Decimal("1565"), sell=Decimal("1615.50"), updated_at=NOW)
OLD = timedelta(days=91)
SECOND = timedelta(seconds=1)


class FakeRates:
    def __init__(self, session: Session, rate: ExchangeRate | None = RATE) -> None:
        self._session = session
        self._rate = rate
        self.transaction_was_open: bool | None = None

    def official_rate(self) -> ExchangeRate:
        self.transaction_was_open = self._session.in_transaction()
        if self._rate is None:
            raise ExternalServiceError("DolarApi", "timeout")
        return self._rate


class World:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.serial = count(1)
        model = VehicleModel(name="Pickup X")
        self.base = Trim(vehicle_model=model, name="Base", list_price_usd=Decimal("20000.50"))
        self.limited = Trim(vehicle_model=model, name="Limited", list_price_usd=Decimal(30000))
        self.unpriced = Trim(vehicle_model=model, name="New")
        self.dealer = Dealer(code="D001", name="X", province="X", city="X", yard_capacity=50)
        self.other = Dealer(code="D002", name="Y", province="Y", city="Y", yard_capacity=50)
        session.add_all([self.base, self.limited, self.unpriced, self.dealer, self.other])
        session.flush()

    def unit(
        self,
        trim: Trim,
        age: timedelta = OLD,
        status: UnitStatus = UnitStatus.IN_YARD,
        dealer: Dealer | None = None,
    ) -> None:
        self.session.add(
            Unit(
                vin=f"8ZZZZZ1234567{next(self.serial):04d}",
                trim=trim,
                color="White",
                status=status,
                dealer=dealer or self.dealer,
                received_at=NOW - age,
            )
        )
        self.session.flush()


@pytest.fixture
def world(db_session: Session) -> World:
    return World(db_session)


def _value(world: World, rates: FakeRates | None = None) -> AgedStockValue:
    service = StockValueService(world.session, POLICY, rates or FakeRates(world.session))
    return service.aged_stock(world.dealer.id, now=NOW)


def test_value_in_dollars_and_pesos(world: World) -> None:
    world.unit(world.base)
    world.unit(world.base)
    world.unit(world.limited)

    assert _value(world) == AgedStockValue(
        units=3,
        units_without_price=0,
        usd=Decimal("70001.00"),
        ars=Decimal("113086615.50"),  # at the sell price, 1615.50
        rate=RATE,
    )


def test_only_aged_units_in_the_yard_of_the_dealer(world: World) -> None:
    world.unit(world.base, age=timedelta(days=90) + SECOND)
    world.unit(world.limited, age=timedelta(days=90))  # not aged yet
    world.unit(world.limited, dealer=world.other)
    for status in set(UnitStatus) - {UnitStatus.IN_YARD}:
        world.unit(world.limited, status=status)

    value = _value(world)

    assert (value.units, value.usd) == (1, Decimal("20000.50"))


def test_it_is_the_same_stock_that_coverage_calls_aged(world: World) -> None:
    world.unit(world.base)
    world.unit(world.limited, age=timedelta(days=90))
    world.unit(world.unpriced)

    coverage = CoverageService(world.session, POLICY).for_dealer(world.dealer.id, now=NOW)

    assert _value(world).units == sum(t.aged_units for t in coverage.trims) == 2


def test_units_without_price_are_reported(world: World) -> None:
    world.unit(world.base)
    world.unit(world.unpriced)

    value = _value(world)

    assert (value.units, value.units_without_price) == (2, 1)
    assert value.usd == Decimal("20000.50")


@pytest.mark.parametrize("unpriced", [0, 2])
def test_nothing_to_value_is_zero(world: World, unpriced: int) -> None:
    for _ in range(unpriced):
        world.unit(world.unpriced)

    value = _value(world)

    assert (value.units, value.units_without_price) == (unpriced, unpriced)
    assert (value.usd, value.ars) == (Decimal(0), Decimal(0))


def test_without_exchange_rate_it_still_answers_in_dollars(world: World) -> None:
    world.unit(world.base)

    with capture_logs() as logs:
        value = _value(world, FakeRates(world.session, rate=None))

    assert (value.usd, value.ars, value.rate) == (Decimal("20000.50"), None, None)
    assert [e["event"] for e in logs] == ["stock_value_without_rate"]


def test_no_transaction_is_open_while_the_api_answers(world: World) -> None:
    rates = FakeRates(world.session)

    _value(world, rates)

    assert rates.transaction_was_open is False


def test_unknown_dealer_never_reaches_the_api(world: World) -> None:
    rates = FakeRates(world.session)

    with pytest.raises(NotFoundError):
        StockValueService(world.session, POLICY, rates).aged_stock(999999, now=NOW)

    assert rates.transaction_was_open is None
