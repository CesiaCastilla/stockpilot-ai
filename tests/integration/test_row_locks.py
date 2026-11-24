"""Row locks, with two real transactions.

Unlike the other tests, these commit: a lock can only be observed from a
second connection, which cannot see uncommitted data. The fixture cleans up.
"""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from stockpilot.domain.errors import ConflictError, DomainError, InvalidStatusTransitionError
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Sale, Trim, Unit, UnitEvent, VehicleModel
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.repositories.units import UnitRepository
from stockpilot.schemas.units import UnitCreate
from stockpilot.services.units import UnitService

pytestmark = pytest.mark.integration


class Committed:
    """Ids of a committed dealer (capacity 2) with one unit in its yard."""

    def __init__(self, session: Session) -> None:
        dealer = Dealer(code="LOCK1", name="X", province="X", city="X", yard_capacity=2)
        trim = Trim(vehicle_model=VehicleModel(name="Lock test model"), name="Base")
        unit = Unit(
            vin="8AFZZZ12345679999",
            trim=trim,
            color="White",
            status=UnitStatus.IN_YARD,
            dealer=dealer,
            received_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        session.add_all([dealer, unit])
        session.commit()
        self.dealer_id, self.trim_id, self.unit_id = dealer.id, trim.id, unit.id


@pytest.fixture
def committed(migrated_engine: Engine) -> Iterator[Committed]:
    try:
        with Session(migrated_engine) as session:
            data = Committed(session)
        yield data
    finally:
        with Session(migrated_engine) as session:
            for table in (UnitEvent, Sale, Unit, Trim, VehicleModel, Dealer):
                session.execute(delete(table))
            session.commit()


@pytest.mark.parametrize("entity", ["dealer", "unit"])
def test_get_for_update_locks_the_row(
    migrated_engine: Engine, committed: Committed, entity: str
) -> None:
    def lock(session: Session) -> None:
        if entity == "dealer":
            DealerRepository(session).get_for_update(committed.dealer_id)
        else:
            UnitRepository(session).get_for_update(committed.unit_id)

    with Session(migrated_engine) as first, Session(migrated_engine) as second:
        lock(first)
        second.execute(text("SET LOCAL lock_timeout = '100ms'"))

        with pytest.raises(OperationalError, match="lock timeout"):
            lock(second)


def _run_at_once(engine: Engine, actions: list[Callable[[UnitService], object]]) -> list[object]:
    """Run each action in its own thread and transaction; return results or domain errors."""
    barrier = Barrier(len(actions))

    def run(action: Callable[[UnitService], object]) -> object:
        with Session(engine) as session:
            session.connection()  # connect first, so both start at the same time
            barrier.wait(timeout=5)
            try:
                action(UnitService(session))
            except DomainError as error:
                return error
            return None

    with ThreadPoolExecutor(len(actions)) as pool:
        return list(pool.map(run, actions))


def test_concurrent_sales_of_one_unit_sell_it_once(
    migrated_engine: Engine, committed: Committed
) -> None:
    results = _run_at_once(migrated_engine, [lambda s: s.sell(committed.unit_id)] * 2)

    # The loser waits for the winner, then sees a sold unit: a clean 409.
    assert results.count(None) == 1
    assert sum(isinstance(r, InvalidStatusTransitionError) for r in results) == 1
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Sale)) == 1


def test_concurrent_registrations_do_not_exceed_the_yard_capacity(
    migrated_engine: Engine, committed: Committed
) -> None:
    def register(vin: str) -> Callable[[UnitService], object]:
        data = UnitCreate(
            vin=vin, trim_id=committed.trim_id, color="White", dealer_id=committed.dealer_id
        )
        return lambda service: service.register(data)

    # One place left, two units for it.
    results = _run_at_once(
        migrated_engine, [register("8AFZZZ12345670001"), register("8AFZZZ12345670002")]
    )

    assert results.count(None) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    with Session(migrated_engine) as session:
        assert DealerRepository(session).count_stock_units(committed.dealer_id) == 2
