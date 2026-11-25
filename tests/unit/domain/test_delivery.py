from datetime import date

import pytest

from stockpilot.domain.delivery import add_business_days
from stockpilot.domain.errors import InvalidTransitDaysError

# December 2026: the 7th (Monday) and 8th (Tuesday) are holidays, the 25th a Friday.
HOLIDAYS = {date(2026, 12, 7), date(2026, 12, 8), date(2026, 12, 25)}


@pytest.mark.parametrize(
    ("start", "days", "expected"),
    [
        pytest.param(date(2026, 12, 1), 1, date(2026, 12, 2), id="next-day"),
        pytest.param(date(2026, 12, 1), 3, date(2026, 12, 4), id="within-the-week"),
        pytest.param(date(2026, 12, 11), 1, date(2026, 12, 14), id="friday-to-monday"),
        pytest.param(date(2026, 12, 4), 1, date(2026, 12, 9), id="weekend-plus-two-holidays"),
        pytest.param(date(2026, 12, 3), 2, date(2026, 12, 9), id="holidays-in-the-middle"),
        pytest.param(date(2026, 12, 7), 1, date(2026, 12, 9), id="leaves-on-a-holiday"),
        pytest.param(date(2026, 12, 12), 1, date(2026, 12, 14), id="leaves-on-saturday"),
        pytest.param(date(2026, 12, 13), 1, date(2026, 12, 14), id="leaves-on-sunday"),
        pytest.param(date(2026, 12, 9), 10, date(2026, 12, 23), id="ten-days-are-two-weeks"),
        pytest.param(date(2026, 12, 24), 1, date(2026, 12, 28), id="holiday-next-to-weekend"),
        pytest.param(date(2026, 12, 1), 0, date(2026, 12, 1), id="zero-days-on-a-business-day"),
        pytest.param(date(2026, 12, 5), 0, date(2026, 12, 9), id="zero-days-on-a-weekend"),
        pytest.param(date(2026, 12, 25), 0, date(2026, 12, 28), id="zero-days-on-a-holiday"),
    ],
)
def test_add_business_days(start: date, days: int, expected: date) -> None:
    assert add_business_days(start, days, HOLIDAYS) == expected


def test_holiday_on_a_weekend_is_not_counted_twice() -> None:
    saturday = date(2026, 12, 12)

    assert add_business_days(date(2026, 12, 11), 1, {saturday}) == date(2026, 12, 14)


@pytest.mark.parametrize("days", [-1, 366])
def test_impossible_transit_days_are_rejected(days: int) -> None:
    with pytest.raises(InvalidTransitDaysError, match=f"between 0 and 365, got {days}"):
        add_business_days(date(2026, 12, 1), days, set())


def test_longest_transit_is_accepted() -> None:
    # 365 business days are 73 weeks.
    assert add_business_days(date(2026, 12, 1), 365, set()) == date(2028, 4, 25)
