from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from stockpilot.domain.coverage import CoveragePolicy, CoverageStatus, days_in_stock
from stockpilot.domain.errors import NotFoundError
from stockpilot.repositories.coverage import (
    AgedUnitRow,
    CoverageRepository,
    DealerTrimRow,
    TrimStockRow,
)
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.schemas.coverage import (
    AgedUnit,
    AgedUnitPage,
    DealerCoverage,
    NetworkFilters,
    Shortage,
    ShortageReport,
    TrimCoverage,
)


class CoverageService:
    """Coverage and aging. Read-only. `now` is a parameter, so results are reproducible."""

    def __init__(self, session: Session, policy: CoveragePolicy) -> None:
        self.policy = policy
        self.dealers = DealerRepository(session)
        self.coverage = CoverageRepository(session)

    def for_dealer(self, dealer_id: int, *, now: datetime) -> DealerCoverage:
        dealer = self.dealers.get(dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer", dealer_id)

        rows = self.coverage.by_trim(
            dealer_id,
            sales_since=self.policy.sales_since(now),
            aged_before=self.policy.aged_before(now),
            now=now,
        )
        trims = [self._trim_coverage(row, now) for row in rows]
        # Most urgent first; trims without sales have no coverage and go last.
        # sort() is stable: ties keep the repository order (model, trim).
        trims.sort(key=lambda t: float("inf") if t.coverage_days is None else t.coverage_days)

        return DealerCoverage(
            dealer_id=dealer.id,
            dealer_code=dealer.code,
            yard_capacity=dealer.yard_capacity,
            stock_units=sum(t.in_yard + t.in_transit + t.reserved for t in trims),
            as_of=now,
            sales_window_days=self.policy.sales_window_days,
            low_coverage_days=self.policy.low_coverage_days,
            aged_after_days=self.policy.aged_after_days,
            trims=trims,
        )

    def shortages(self, filters: NetworkFilters, *, now: datetime) -> ShortageReport:
        """Every dealer and trim in shortage. Not paginated: at most one row per pair.

        The same rules as for_dealer, so both always agree on what is short.
        """
        rows = self.coverage.network_by_trim(
            sales_since=self.policy.sales_since(now),
            now=now,
            trim_id=filters.trim_id,
            vehicle_model_id=filters.vehicle_model_id,
        )
        items = [s for s in map(self._shortage, rows) if s is not None]
        # Sold out first; among equals, where it sells the most.
        items.sort(
            key=lambda s: (
                s.coverage_days,
                -s.recent_sales,
                s.dealer_code,
                s.vehicle_model_name,
                s.trim_name,
            )
        )
        return ShortageReport(
            as_of=now,
            sales_window_days=self.policy.sales_window_days,
            low_coverage_days=self.policy.low_coverage_days,
            items=items,
        )

    def aged_units(
        self,
        filters: NetworkFilters,
        *,
        now: datetime,
        min_days: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AgedUnitPage:
        """Units in a yard for more than min_days (the aging threshold by default)
        that could be moved: not reserved, not in transit, without a pending order.

        min_days=0 lists every movable unit, the distribution center included.
        """
        days = self.policy.aged_after_days if min_days is None else min_days
        received_before = now - timedelta(days=days)
        rows = self.coverage.aged_units(
            received_before=received_before,
            trim_id=filters.trim_id,
            vehicle_model_id=filters.vehicle_model_id,
            limit=limit,
            offset=offset,
        )
        total = self.coverage.count_aged_units(
            received_before=received_before,
            trim_id=filters.trim_id,
            vehicle_model_id=filters.vehicle_model_id,
        )
        return AgedUnitPage(
            items=[_aged_unit(row, now) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            as_of=now,
            min_days=days,
        )

    def _shortage(self, row: DealerTrimRow) -> Shortage | None:
        available = row.in_yard + row.in_transit
        coverage_days = self.policy.coverage_days(
            available=available, recent_sales=row.recent_sales
        )
        if self.policy.status(coverage_days) is not CoverageStatus.SHORTAGE:
            return None
        assert coverage_days is not None  # a shortage always has sales; this is for mypy
        free_places = row.yard_capacity - row.yard_stock_units - row.yard_pending_arrivals
        return Shortage(
            dealer_id=row.dealer_id,
            dealer_code=row.dealer_code,
            vehicle_model_id=row.vehicle_model_id,
            vehicle_model_name=row.vehicle_model_name,
            trim_id=row.trim_id,
            trim_name=row.trim_name,
            in_yard=row.in_yard,
            in_transit=row.in_transit,
            recent_sales=row.recent_sales,
            coverage_days=coverage_days,
            pending_arrivals=row.pending_arrivals,
            # Computed here and not left to the model: language models get arithmetic wrong.
            units_needed=self.policy.units_to_cover(
                available=available + row.pending_arrivals, recent_sales=row.recent_sales
            ),
            # Negative if the capacity was lowered below the stock: no room is no room.
            free_places=max(0, free_places),
        )

    def _trim_coverage(self, row: TrimStockRow, now: datetime) -> TrimCoverage:
        # In transit counts: otherwise a unit already on its way would not stop
        # the agent from sending another one. Reserved does not: it has an owner.
        coverage_days = self.policy.coverage_days(
            available=row.in_yard + row.in_transit, recent_sales=row.recent_sales
        )
        oldest = row.oldest_received_at
        return TrimCoverage(
            vehicle_model_id=row.vehicle_model_id,
            vehicle_model_name=row.vehicle_model_name,
            trim_id=row.trim_id,
            trim_name=row.trim_name,
            in_yard=row.in_yard,
            in_transit=row.in_transit,
            reserved=row.reserved,
            recent_sales=row.recent_sales,
            coverage_days=coverage_days,
            status=self.policy.status(coverage_days),
            aged_units=row.aged_units,
            oldest_unit_days=None if oldest is None else days_in_stock(oldest, now),
        )


def _aged_unit(row: AgedUnitRow, now: datetime) -> AgedUnit:
    return AgedUnit(
        unit_id=row.unit_id,
        vin=row.vin,
        vehicle_model_id=row.vehicle_model_id,
        vehicle_model_name=row.vehicle_model_name,
        trim_id=row.trim_id,
        trim_name=row.trim_name,
        color=row.color,
        dealer_id=row.dealer_id,
        dealer_code=row.dealer_code,
        received_at=row.received_at,
        days_in_stock=days_in_stock(row.received_at, now),
    )
