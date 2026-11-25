from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scripts.seed import DatabaseNotEmptyError, SeedConfig, build_demo_data, seed
from stockpilot.models import Dealer, Reservation, Sale, Unit, UnitEvent

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
# Small network: the full one is covered by the unit tests, this checks persistence.
SMALL = SeedConfig(dealers=5, stock_units=100, sales_days=30, daily_network_sales=5)


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_seed_persists_the_whole_network(db_session: Session) -> None:
    data = build_demo_data(SMALL, NOW)

    seed(db_session, data)

    assert _count(db_session, Dealer) == len(data.dealers)
    assert _count(db_session, Unit) == len(data.units)
    assert _count(db_session, Sale) == len(data.sales)
    assert _count(db_session, Reservation) == len(data.reservations)
    assert _count(db_session, UnitEvent) == len(data.events)


def test_seed_refuses_a_database_that_already_has_data(db_session: Session) -> None:
    seed(db_session, build_demo_data(SMALL, NOW))

    with pytest.raises(DatabaseNotEmptyError):
        seed(db_session, build_demo_data(SMALL, NOW))


def test_seed_with_reset_replaces_the_existing_data(db_session: Session) -> None:
    seed(db_session, build_demo_data(SMALL, NOW))
    data = build_demo_data(SMALL, NOW)

    seed(db_session, data, reset=True)

    assert _count(db_session, Unit) == len(data.units)
    assert _count(db_session, UnitEvent) == len(data.events)
