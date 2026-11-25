from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from stockpilot.domain.unit_status import STOCK_STATUSES, UnitStatus
from stockpilot.models import Sale, Trim, Unit, VehicleModel


@dataclass(frozen=True)
class TrimStockRow:
    vehicle_model_id: int
    vehicle_model_name: str
    trim_id: int
    trim_name: str
    in_yard: int
    in_transit: int
    reserved: int
    recent_sales: int
    aged_units: int
    oldest_received_at: datetime | None


class CoverageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_trim(
        self, dealer_id: int, *, sales_since: datetime, aged_before: datetime, now: datetime
    ) -> Sequence[TrimStockRow]:
        """Stock and recent sales of a dealer, one row per trim that has either.

        Stock and sales are aggregated in separate subqueries: joining both to
        trims directly would multiply their rows and inflate the counts.
        """
        in_yard = Unit.status == UnitStatus.IN_YARD
        stock = (
            select(
                Unit.trim_id,
                func.count().filter(in_yard).label("in_yard"),
                func.count().filter(Unit.status == UnitStatus.IN_TRANSIT).label("in_transit"),
                func.count().filter(Unit.status == UnitStatus.RESERVED).label("reserved"),
                func.count().filter(in_yard, Unit.received_at < aged_before).label("aged_units"),
                func.min(Unit.received_at).filter(in_yard).label("oldest_received_at"),
            )
            .where(Unit.dealer_id == dealer_id, Unit.status.in_(STOCK_STATUSES))
            .group_by(Unit.trim_id)
            .subquery()
        )
        sales = (
            select(Unit.trim_id, func.count().label("recent_sales"))
            .join(Sale, Sale.unit_id == Unit.id)
            # The dealer of the sale, not of the unit: that is where the demand was.
            .where(Sale.dealer_id == dealer_id, Sale.sold_at >= sales_since, Sale.sold_at <= now)
            .group_by(Unit.trim_id)
            .subquery()
        )
        query = (
            select(
                VehicleModel.id,
                VehicleModel.name,
                Trim.id,
                Trim.name,
                func.coalesce(stock.c.in_yard, 0),
                func.coalesce(stock.c.in_transit, 0),
                func.coalesce(stock.c.reserved, 0),
                func.coalesce(sales.c.recent_sales, 0),
                func.coalesce(stock.c.aged_units, 0),
                stock.c.oldest_received_at,
            )
            .select_from(Trim)
            .join(VehicleModel, VehicleModel.id == Trim.vehicle_model_id)
            # Outer joins: a sold-out trim has sales and no stock, the case that matters most.
            .outerjoin(stock, stock.c.trim_id == Trim.id)
            .outerjoin(sales, sales.c.trim_id == Trim.id)
            .where(or_(stock.c.trim_id.is_not(None), sales.c.trim_id.is_not(None)))
            .order_by(VehicleModel.name, Trim.name)
        )
        return [TrimStockRow(*row) for row in self.session.execute(query)]
