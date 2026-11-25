from collections.abc import Collection
from datetime import date
from typing import Protocol

import structlog

from stockpilot.domain.delivery import DeliveryEstimate, Holiday, add_business_days
from stockpilot.domain.errors import ExternalServiceError

logger = structlog.get_logger(__name__)


class HolidayCalendar(Protocol):
    """What this service needs, not who provides it: tests use a fake calendar."""

    def holidays(self, year: int) -> Collection[Holiday]:
        """Non-working days of a year. Raises ExternalServiceError."""
        ...


class DeliveryService:
    def __init__(self, calendar: HolidayCalendar) -> None:
        self._calendar = calendar

    def estimate(self, start: date, transit_days: int) -> DeliveryEstimate:
        """When a unit that leaves on start arrives, after transit_days business days.

        Without the holidays of a year it still answers, counting only
        weekends in that year, and says so in the result.
        """
        holidays: set[date] = set()
        checked = True
        arrives_on = add_business_days(start, transit_days, holidays)

        # Holidays can push the arrival into the next year, whose own holidays
        # then matter too. Most estimates need a single year.
        year = start.year
        while year <= arrives_on.year:
            try:
                holidays.update(holiday.day for holiday in self._calendar.holidays(year))
            except ExternalServiceError as error:
                logger.warning("delivery_without_holidays", year=year, error=str(error))
                checked = False
            arrives_on = add_business_days(start, transit_days, holidays)
            year += 1
        return DeliveryEstimate(arrives_on=arrives_on, holidays_checked=checked)
