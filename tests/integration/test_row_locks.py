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
from stockpilot.domain.transfers import InvalidTransferError, TransferOrderStatus, TransferRule
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Sale, TransferOrder, Trim, Unit, UnitEvent, VehicleModel
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.repositories.units import UnitRepository
from stockpilot.schemas.transfers import TransferProposal
from stockpilot.schemas.units import UnitCreate, UnitUpdate
from stockpilot.services.transfers import TransferService
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
            for table in (TransferOrder, UnitEvent, Sale, Unit, Trim, VehicleModel, Dealer):
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


def _run_at_once(engine: Engine, actions: list[Callable[[Session], object]]) -> list[object]:
    """Run each action in its own thread and transaction; return results or domain errors."""
    barrier = Barrier(len(actions))

    def run(action: Callable[[Session], object]) -> object:
        with Session(engine) as session:
            session.connection()  # connect first, so both start at the same time
            barrier.wait(timeout=5)
            try:
                action(session)
            except DomainError as error:
                return error
            return None

    with ThreadPoolExecutor(len(actions)) as pool:
        return list(pool.map(run, actions))


def test_concurrent_sales_of_one_unit_sell_it_once(
    migrated_engine: Engine, committed: Committed
) -> None:
    results = _run_at_once(migrated_engine, [lambda s: UnitService(s).sell(committed.unit_id)] * 2)

    # The loser waits for the winner, then sees a sold unit: a clean 409.
    assert results.count(None) == 1
    assert sum(isinstance(r, InvalidStatusTransitionError) for r in results) == 1
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Sale)) == 1


def test_update_waits_for_a_sale_in_progress(migrated_engine: Engine, committed: Committed) -> None:
    with Session(migrated_engine) as sale, Session(migrated_engine) as update:
        UnitRepository(sale).get_for_update(committed.unit_id)
        update.execute(text("SET LOCAL lock_timeout = '100ms'"))

        # The same color, so there is no UPDATE to block: only the lock can wait.
        with pytest.raises(OperationalError, match="lock timeout"):
            UnitService(update).update(committed.unit_id, UnitUpdate(color="White"))


def test_concurrent_registrations_do_not_exceed_the_yard_capacity(
    migrated_engine: Engine, committed: Committed
) -> None:
    def register(vin: str) -> Callable[[Session], object]:
        data = UnitCreate(
            vin=vin, trim_id=committed.trim_id, color="White", dealer_id=committed.dealer_id
        )
        return lambda session: UnitService(session).register(data)

    # One place left, two units for it.
    results = _run_at_once(
        migrated_engine, [register("8AFZZZ12345670001"), register("8AFZZZ12345670002")]
    )

    assert results.count(None) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    with Session(migrated_engine) as session:
        assert DealerRepository(session).count_stock_units(committed.dealer_id) == 2


# --- Transfer proposals ---------------------------------------------------------


def _commit_destination(engine: Engine, *, capacity: int) -> int:
    with Session(engine) as session:
        dealer = Dealer(code="LOCK2", name="X", province="X", city="X", yard_capacity=capacity)
        session.add(dealer)
        session.commit()
        return dealer.id


def _propose(vin: str, dealer_id: int) -> Callable[[Session], object]:
    proposal = TransferProposal(vin=vin, destination_dealer_id=dealer_id, reason="Shortage")
    return lambda session: TransferService(session).propose(proposal)


def _loser_broke(results: list[object], rule: TransferRule) -> bool:
    (error,) = [r for r in results if r is not None]
    return isinstance(error, InvalidTransferError) and [v.rule for v in error.violations] == [rule]


def test_concurrent_proposals_of_one_unit_create_one_order(
    migrated_engine: Engine, committed: Committed
) -> None:
    dealer_id = _commit_destination(migrated_engine, capacity=5)

    results = _run_at_once(migrated_engine, [_propose("8AFZZZ12345679999", dealer_id)] * 2)

    assert results.count(None) == 1
    assert _loser_broke(results, TransferRule.NOT_ALREADY_PROPOSED)


def test_concurrent_proposals_do_not_promise_the_same_place_twice(
    migrated_engine: Engine, committed: Committed
) -> None:
    dealer_id = _commit_destination(migrated_engine, capacity=1)
    with Session(migrated_engine) as session:
        session.add(
            Unit(
                vin="8AFZZZ12345678888",
                trim_id=committed.trim_id,
                color="White",
                status=UnitStatus.IN_YARD,
                dealer_id=committed.dealer_id,
                received_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
        )
        session.commit()

    results = _run_at_once(
        migrated_engine,
        [_propose("8AFZZZ12345679999", dealer_id), _propose("8AFZZZ12345678888", dealer_id)],
    )

    assert results.count(None) == 1
    assert _loser_broke(results, TransferRule.YARD_HAS_ROOM)


def test_a_proposal_waits_for_a_sale_in_progress(
    migrated_engine: Engine, committed: Committed
) -> None:
    dealer_id = _commit_destination(migrated_engine, capacity=5)
    with Session(migrated_engine) as sale, Session(migrated_engine) as proposal:
        UnitRepository(sale).get_for_update(committed.unit_id)
        proposal.execute(text("SET LOCAL lock_timeout = '100ms'"))

        with pytest.raises(OperationalError, match="lock timeout"):
            _propose("8AFZZZ12345679999", dealer_id)(proposal)


# --- Transfer decisions -----------------------------------------------------------


def _commit_order(engine: Engine, unit_id: int, destination_id: int) -> int:
    # Without the validator: some of these orders no longer fit together.
    with Session(engine) as session:
        unit = session.get_one(Unit, unit_id)
        order = TransferOrder(
            unit_id=unit_id,
            origin_dealer_id=unit.dealer_id,
            destination_dealer_id=destination_id,
            status=TransferOrderStatus.PENDING,
            reason="Shortage",
        )
        session.add(order)
        session.commit()
        return order.id


def _count_events(engine: Engine) -> int | None:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(UnitEvent))


def test_an_order_is_approved_once(migrated_engine: Engine, committed: Committed) -> None:
    dealer_id = _commit_destination(migrated_engine, capacity=5)
    order_id = _commit_order(migrated_engine, committed.unit_id, dealer_id)

    results = _run_at_once(migrated_engine, [lambda s: TransferService(s).approve(order_id)] * 2)

    assert results.count(None) == 1
    (error,) = [r for r in results if r is not None]
    assert str(error) == f"Transfer order {order_id} is already approved"
    assert _count_events(migrated_engine) == 1


def test_an_order_is_approved_or_rejected_but_not_both(
    migrated_engine: Engine, committed: Committed
) -> None:
    dealer_id = _commit_destination(migrated_engine, capacity=5)
    order_id = _commit_order(migrated_engine, committed.unit_id, dealer_id)

    results = _run_at_once(
        migrated_engine,
        [
            lambda s: TransferService(s).approve(order_id),
            lambda s: TransferService(s).reject(order_id, "No"),
        ],
    )

    assert results.count(None) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    with Session(migrated_engine) as session:
        order = session.get_one(TransferOrder, order_id)
        approved = order.status is TransferOrderStatus.APPROVED
        # The unit moved only if the approval won.
        assert (order.unit.status is UnitStatus.IN_TRANSIT) == approved


def test_approvals_do_not_exceed_the_yard_capacity(
    migrated_engine: Engine, committed: Committed
) -> None:
    dealer_id = _commit_destination(migrated_engine, capacity=1)
    with Session(migrated_engine) as session:
        other = Unit(
            vin="8AFZZZ12345678888",
            trim_id=committed.trim_id,
            color="White",
            status=UnitStatus.IN_YARD,
            dealer_id=committed.dealer_id,
            received_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        session.add(other)
        session.commit()
        other_id = other.id
    first = _commit_order(migrated_engine, committed.unit_id, dealer_id)
    second = _commit_order(migrated_engine, other_id, dealer_id)

    results = _run_at_once(
        migrated_engine,
        [
            lambda s: TransferService(s).approve(first),
            lambda s: TransferService(s).approve(second),
        ],
    )

    assert results.count(None) == 1
    assert _loser_broke(results, TransferRule.YARD_HAS_ROOM)


def test_a_unit_is_sold_or_shipped_but_not_both(
    migrated_engine: Engine, committed: Committed
) -> None:
    dealer_id = _commit_destination(migrated_engine, capacity=5)
    order_id = _commit_order(migrated_engine, committed.unit_id, dealer_id)

    results = _run_at_once(
        migrated_engine,
        [
            lambda s: TransferService(s).approve(order_id),
            lambda s: UnitService(s).sell(committed.unit_id),
        ],
    )

    assert results.count(None) == 1
    assert _count_events(migrated_engine) == 1


def test_crossed_transfers_do_not_deadlock(migrated_engine: Engine, committed: Committed) -> None:
    # A -> B and B -> A at the same time.
    dealer_id = _commit_destination(migrated_engine, capacity=5)
    with Session(migrated_engine) as session:
        other = Unit(
            vin="8AFZZZ12345678888",
            trim_id=committed.trim_id,
            color="White",
            status=UnitStatus.IN_YARD,
            dealer_id=dealer_id,
            received_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        session.add(other)
        session.commit()
        other_id = other.id
    there = _commit_order(migrated_engine, committed.unit_id, dealer_id)
    back = _commit_order(migrated_engine, other_id, committed.dealer_id)

    results = _run_at_once(
        migrated_engine,
        [lambda s: TransferService(s).approve(there), lambda s: TransferService(s).approve(back)],
    )

    assert results == [None, None]
