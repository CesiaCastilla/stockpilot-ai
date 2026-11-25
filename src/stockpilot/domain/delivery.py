"""Delivery dates in business days."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta

from stockpilot.domain.errors import InvalidTransitDaysError

# Technical limit, not a logistics one: the value can come from the AI model,
# and an absurd one must fail instead of walking through centuries.
MAX_TRANSIT_DAYS = 365

_SATURDAY = 5


@dataclass(frozen=True)
class Holiday:
    day: date
    name: str


@dataclass(frozen=True)
class DeliveryEstimate:
    arrives_on: date
    # False: some holidays were unknown, so it could be later than arrives_on.
    holidays_checked: bool


def add_business_days(start: date, days: int, holidays: Collection[date]) -> date:
    """The date `days` business days after start, which itself does not count.

    The result is always a business day: with 0 days, a start on a weekend or
    holiday moves to the next one.
    """
    if not 0 <= days <= MAX_TRANSIT_DAYS:
        raise InvalidTransitDaysError(days, MAX_TRANSIT_DAYS)

    def is_business_day(day: date) -> bool:
        return day.weekday() < _SATURDAY and day not in holidays

    day, remaining = start, days
    while remaining > 0 or not is_business_day(day):
        day += timedelta(days=1)
        if is_business_day(day):
            remaining -= 1
    return day
