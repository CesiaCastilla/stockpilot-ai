from collections.abc import Collection
from datetime import date

import pytest
from structlog.testing import capture_logs

from stockpilot.domain.delivery import DeliveryEstimate, Holiday
from stockpilot.domain.errors import ExternalServiceError, InvalidTransitDaysError
from stockpilot.services.delivery import DeliveryService


class FakeCalendar:
    """Years that are not given fail, like an API that is down or has not published them."""

    def __init__(self, *dates: date, years: Collection[int]) -> None:
        self._dates = dates
        self._years = years
        self.asked: list[int] = []

    def holidays(self, year: int) -> list[Holiday]:
        self.asked.append(year)
        if year not in self._years:
            raise ExternalServiceError("Calendar", "HTTP 404")
        return [Holiday(day=d, name="X") for d in self._dates if d.year == year]


def test_skips_the_holidays_of_the_year() -> None:
    calendar = FakeCalendar(date(2026, 12, 8), years=[2026])

    estimate = DeliveryService(calendar).estimate(date(2026, 12, 7), 1)

    assert estimate == DeliveryEstimate(date(2026, 12, 9), holidays_checked=True)
    assert calendar.asked == [2026]  # the next year is not needed


def test_transit_that_crosses_the_year_uses_both_calendars() -> None:
    calendar = FakeCalendar(date(2026, 12, 31), date(2027, 1, 1), years=[2026, 2027])

    estimate = DeliveryService(calendar).estimate(date(2026, 12, 29), 2)

    # Wednesday 30, then Thursday 31 and Friday 1 are holidays, and the weekend.
    assert estimate == DeliveryEstimate(date(2027, 1, 4), holidays_checked=True)


def test_holidays_that_push_the_arrival_into_the_next_year() -> None:
    # Counting only weekends it arrives on December 31, within 2026.
    calendar = FakeCalendar(date(2026, 12, 31), date(2027, 1, 1), years=[2026, 2027])

    estimate = DeliveryService(calendar).estimate(date(2026, 12, 30), 1)

    assert estimate == DeliveryEstimate(date(2027, 1, 4), holidays_checked=True)
    assert calendar.asked == [2026, 2027]


def test_without_holidays_it_counts_weekends_and_says_so() -> None:
    with capture_logs() as logs:
        estimate = DeliveryService(FakeCalendar(years=[])).estimate(date(2026, 12, 4), 1)

    assert estimate == DeliveryEstimate(date(2026, 12, 7), holidays_checked=False)
    assert logs == [
        {
            "event": "delivery_without_holidays",
            "log_level": "warning",
            "year": 2026,
            "error": "Calendar is not available: HTTP 404",
        }
    ]


@pytest.mark.parametrize("known_year", [2026, 2027])
def test_one_unknown_year_is_enough_to_say_so(known_year: int) -> None:
    calendar = FakeCalendar(date(2026, 12, 31), date(2027, 1, 1), years=[known_year])

    estimate = DeliveryService(calendar).estimate(date(2026, 12, 30), 2)

    # The holiday of the known year is still skipped: knowing both it would
    # be January 5, and knowing none January 1.
    assert estimate == DeliveryEstimate(date(2027, 1, 4), holidays_checked=False)


def test_invalid_transit_days_never_reach_the_calendar() -> None:
    calendar = FakeCalendar(years=[2026])

    with pytest.raises(InvalidTransitDaysError):
        DeliveryService(calendar).estimate(date(2026, 12, 1), -1)

    assert calendar.asked == []
