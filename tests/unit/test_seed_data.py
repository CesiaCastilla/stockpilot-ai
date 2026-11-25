"""The demo network is valid, reproducible and contains the scenarios the agent must handle."""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from scripts.seed import TRANSIT_DAYS, DemoData, SeedConfig, build_demo_data
from stockpilot.domain.unit_status import UnitStatus, ensure_transition
from stockpilot.domain.vin import check_digit, parse_vin
from stockpilot.models import UnitEvent

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CONFIG = SeedConfig()


@pytest.fixture(scope="module")
def data() -> DemoData:
    return build_demo_data(CONFIG, NOW)


def test_same_seed_builds_the_same_network(data: DemoData) -> None:
    again = build_demo_data(CONFIG, NOW)

    assert [u.vin for u in again.units] == [u.vin for u in data.units]
    assert [s.sold_at for s in again.sales] == [s.sold_at for s in data.sales]
    assert [e.occurred_at for e in again.events] == [e.occurred_at for e in data.events]


def test_different_seed_builds_a_different_network(data: DemoData) -> None:
    other = build_demo_data(SeedConfig(random_seed=7), NOW)

    assert [u.vin for u in other.units] != [u.vin for u in data.units]


def test_network_has_the_configured_size(data: DemoData) -> None:
    assert len(data.dealers) == CONFIG.dealers
    assert len(data.stock_units) == CONFIG.stock_units
    assert len({d.code for d in data.dealers}) == CONFIG.dealers


def test_every_trim_has_a_price(data: DemoData) -> None:
    assert all(t.list_price_usd is not None and t.list_price_usd > 0 for t in data.trims)


def test_vins_are_unique_and_well_formed(data: DemoData) -> None:
    vins = [u.vin for u in data.units]

    assert len(set(vins)) == len(vins)
    for vin in vins:
        assert parse_vin(vin) == vin
        assert vin[8] == check_digit(vin)


def test_dealer_stock_never_exceeds_yard_capacity(data: DemoData) -> None:
    stock = Counter(u.dealer.code for u in data.stock_units if u.dealer is not None)

    for dealer in data.dealers:
        assert stock[dealer.code] <= dealer.yard_capacity


def test_some_stock_waits_at_the_distribution_center(data: DemoData) -> None:
    at_center = [u for u in data.stock_units if u.dealer is None]

    assert at_center
    assert all(u.status is UnitStatus.IN_YARD for u in at_center)


def test_sales_are_consistent_with_their_units(data: DemoData) -> None:
    window_start = NOW - timedelta(days=CONFIG.sales_days)

    for sale in data.sales:
        assert window_start <= sale.sold_at <= NOW
        assert sale.unit.dealer is sale.dealer
        assert sale.unit.received_at < sale.sold_at
        assert sale.unit.status in (UnitStatus.SOLD, UnitStatus.DELIVERED)


def test_every_reserved_unit_has_exactly_one_active_reservation(data: DemoData) -> None:
    active = Counter(r.unit.vin for r in data.reservations if r.cancelled_at is None)
    reserved = {u.vin for u in data.units if u.status is UnitStatus.RESERVED}

    assert set(active) == reserved
    assert set(active.values()) == {1}


def test_reservation_dates_are_consistent(data: DemoData) -> None:
    for reservation in data.reservations:
        assert reservation.unit.received_at <= reservation.reserved_at <= NOW
        if reservation.cancelled_at is not None:
            assert reservation.reserved_at <= reservation.cancelled_at <= NOW


# --- History ------------------------------------------------------------------


@pytest.fixture(scope="module")
def histories(data: DemoData) -> dict[str, list[UnitEvent]]:
    by_vin: dict[str, list[UnitEvent]] = defaultdict(list)
    for event in data.events:
        by_vin[event.unit.vin].append(event)
    return by_vin


def test_every_history_follows_the_status_machine(
    data: DemoData, histories: dict[str, list[UnitEvent]]
) -> None:
    for unit in data.units:
        first, *rest = histories[unit.vin]

        assert first.from_status is None
        current = first.to_status
        for event in rest:
            assert event.from_status is current
            ensure_transition(current, event.to_status)
            current = event.to_status

        # The history ends where the unit is now.
        assert current is unit.status
        assert [first, *rest][-1].dealer is unit.dealer


def test_histories_start_as_a_registration(
    data: DemoData, histories: dict[str, list[UnitEvent]]
) -> None:
    # Same rule as UnitService.register.
    for unit in data.units:
        first = histories[unit.vin][0]
        expected = UnitStatus.IN_YARD if first.dealer is None else UnitStatus.IN_TRANSIT

        assert first.occurred_at == unit.received_at
        assert first.to_status is expected


def test_history_dates_are_strictly_increasing_and_never_in_the_future(
    histories: dict[str, list[UnitEvent]],
) -> None:
    for events in histories.values():
        dates = [e.occurred_at for e in events]

        assert all(a < b for a, b in pairwise(dates))
        assert dates[-1] <= NOW


def test_history_matches_reservations_and_sales(data: DemoData) -> None:
    def dates(
        *statuses: UnitStatus, previous: UnitStatus | None = None
    ) -> list[tuple[str, datetime]]:
        return sorted(
            (e.unit.vin, e.occurred_at)
            for e in data.events
            if e.to_status in statuses and previous in (None, e.from_status)
        )

    reservations = data.reservations
    assert dates(UnitStatus.SOLD) == sorted((s.unit.vin, s.sold_at) for s in data.sales)
    assert dates(UnitStatus.RESERVED) == sorted((r.unit.vin, r.reserved_at) for r in reservations)
    assert dates(UnitStatus.IN_YARD, previous=UnitStatus.RESERVED) == sorted(
        (r.unit.vin, r.cancelled_at) for r in reservations if r.cancelled_at is not None
    )


def test_units_in_transit_left_within_the_length_of_a_trip(
    data: DemoData, histories: dict[str, list[UnitEvent]]
) -> None:
    for unit in data.units:
        if unit.status is UnitStatus.IN_TRANSIT:
            assert NOW - histories[unit.vin][-1].occurred_at <= timedelta(days=TRANSIT_DAYS[1])


# --- Scenarios ----------------------------------------------------------------


def test_network_contains_shortages(data: DemoData) -> None:
    # A dealer x trim that sold in the last 30 days and has no stock left.
    recent = NOW - timedelta(days=30)
    selling = {(s.dealer.code, s.unit.trim.name) for s in data.sales if s.sold_at >= recent}
    stocked = {(u.dealer.code, u.trim.name) for u in data.stock_units if u.dealer is not None}

    assert selling - stocked


def test_network_contains_aged_units_where_the_trim_does_not_sell(data: DemoData) -> None:
    aged_since = NOW - timedelta(days=90)
    sold_cells = {(s.dealer.code, s.unit.trim.name) for s in data.sales}
    aged_without_sales = [
        u
        for u in data.stock_units
        if u.dealer is not None
        and u.received_at < aged_since
        and (u.dealer.code, u.trim.name) not in sold_cells
    ]

    assert aged_without_sales


def test_more_dealers_than_known_locations_is_rejected() -> None:
    with pytest.raises(ValueError, match="dealers"):
        build_demo_data(SeedConfig(dealers=31), NOW)
