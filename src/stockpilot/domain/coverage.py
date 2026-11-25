"""Coverage and aging rules. Pure functions: no database, no clock."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class CoverageStatus(StrEnum):
    SHORTAGE = "shortage"
    OK = "ok"
    # Nothing sold in the window: coverage is undefined, not infinite.
    NO_SALES = "no_sales"


@dataclass(frozen=True)
class CoveragePolicy:
    """Business thresholds, in days."""

    sales_window_days: int
    low_coverage_days: int
    aged_after_days: int

    def coverage_days(self, *, available: int, recent_sales: int) -> float | None:
        """Days until the stock runs out at the recent sales pace; None without sales."""
        if recent_sales == 0:
            return None
        # Rounded here, so the status always agrees with the number people see.
        return round(available * self.sales_window_days / recent_sales, 1)

    def status(self, coverage_days: float | None) -> CoverageStatus:
        if coverage_days is None:
            return CoverageStatus.NO_SALES
        if coverage_days < self.low_coverage_days:
            return CoverageStatus.SHORTAGE
        return CoverageStatus.OK

    def sales_since(self, now: datetime) -> datetime:
        return now - timedelta(days=self.sales_window_days)

    def aged_before(self, now: datetime) -> datetime:
        """Units received before this moment are aged."""
        return now - timedelta(days=self.aged_after_days)


def days_in_stock(received_at: datetime, now: datetime) -> int:
    """Full days since the unit entered the network. Transfers do not reset it."""
    return (now - received_at).days
