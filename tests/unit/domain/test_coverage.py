from datetime import UTC, datetime, timedelta

import pytest

from stockpilot.domain.coverage import CoveragePolicy, CoverageStatus, days_in_stock

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
POLICY = CoveragePolicy(sales_window_days=60, low_coverage_days=15, aged_after_days=90)


@pytest.mark.parametrize(
    ("available", "recent_sales", "expected"),
    [
        pytest.param(10, 60, 10.0, id="one-sale-per-day"),
        pytest.param(5, 4, 75.0, id="slow-seller"),
        pytest.param(1, 7, 8.6, id="rounded-to-one-decimal"),
        pytest.param(0, 3, 0.0, id="sold-out"),
        pytest.param(5, 0, None, id="no-sales-is-undefined"),
        pytest.param(0, 0, None, id="nothing-at-all"),
    ],
)
def test_coverage_days(available: int, recent_sales: int, expected: float | None) -> None:
    assert POLICY.coverage_days(available=available, recent_sales=recent_sales) == expected


@pytest.mark.parametrize(
    ("coverage_days", "expected"),
    [
        (0.0, CoverageStatus.SHORTAGE),
        (14.9, CoverageStatus.SHORTAGE),
        (15.0, CoverageStatus.OK),
        (None, CoverageStatus.NO_SALES),
    ],
)
def test_status(coverage_days: float | None, expected: CoverageStatus) -> None:
    assert POLICY.status(coverage_days) is expected


def test_status_agrees_with_the_rounded_number() -> None:
    # 14.96... is shown as 15.0, so it must not be reported as "less than 15".
    days = POLICY.coverage_days(available=182, recent_sales=730)

    assert days == 15.0
    assert POLICY.status(days) is CoverageStatus.OK


def test_windows_go_back_from_now() -> None:
    assert POLICY.sales_since(NOW) == datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    assert POLICY.aged_before(NOW) == datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(0), 0),
        (timedelta(hours=23, minutes=59), 0),
        (timedelta(days=1), 1),
        (timedelta(days=90, hours=23), 90),
    ],
)
def test_days_in_stock_counts_full_days(age: timedelta, expected: int) -> None:
    assert days_in_stock(NOW - age, NOW) == expected


@pytest.mark.parametrize(
    ("available", "recent_sales", "expected"),
    [
        # 15 days at 1 sale a day.
        pytest.param(0, 60, 15, id="sold-out"),
        pytest.param(10, 60, 5, id="short"),
        pytest.param(15, 60, 0, id="exactly-at-the-threshold"),
        pytest.param(40, 60, 0, id="plenty"),
        pytest.param(3, 0, 0, id="no-sales-is-not-a-shortage"),
        # 182 units are 14.96 days, shown as 15.0: that is already enough.
        pytest.param(182, 730, 0, id="rounded-up-to-the-threshold"),
        pytest.param(181, 730, 1, id="just-below-after-rounding"),
        pytest.param(0, 1, 1, id="one-unit-is-60-days"),
    ],
)
def test_units_to_cover(available: int, recent_sales: int, expected: int) -> None:
    assert POLICY.units_to_cover(available=available, recent_sales=recent_sales) == expected


def test_units_to_cover_is_the_fewest_that_end_the_shortage() -> None:
    def short(available: int, sales: int) -> bool:
        days = POLICY.coverage_days(available=available, recent_sales=sales)
        return POLICY.status(days) is CoverageStatus.SHORTAGE

    for sales in range(1, 120):
        for available in range(40):
            needed = POLICY.units_to_cover(available=available, recent_sales=sales)
            assert not short(available + needed, sales)
            if needed:
                assert short(available + needed - 1, sales)
