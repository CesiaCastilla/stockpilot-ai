from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, tuple_
from sqlalchemy.orm import Session

from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.domain.unit_status import STOCK_STATUSES, UnitStatus
from stockpilot.models import Dealer, Sale, TransferOrder, Trim, Unit, VehicleModel


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


@dataclass(frozen=True)
class DealerTrimRow:
    """One trim with recent sales at one dealer, plus the yard of that dealer."""

    dealer_id: int
    dealer_code: str
    vehicle_model_id: int
    vehicle_model_name: str
    trim_id: int
    trim_name: str
    in_yard: int
    in_transit: int
    recent_sales: int
    # Units of this trim in pending orders to this dealer.
    pending_arrivals: int
    yard_capacity: int
    # Every trim: what the validator compares with the capacity.
    yard_stock_units: int
    yard_pending_arrivals: int


@dataclass(frozen=True)
class AgedUnitRow:
    unit_id: int
    vin: str
    vehicle_model_id: int
    vehicle_model_name: str
    trim_id: int
    trim_name: str
    color: str
    dealer_id: int | None
    dealer_code: str | None
    received_at: datetime


@dataclass(frozen=True)
class AgedValueRow:
    units: int
    priced_units: int
    usd: Decimal


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

    def aged_value(self, dealer_id: int, *, aged_before: datetime) -> AgedValueRow:
        """The same aged units that by_trim counts, at list price."""
        query = (
            select(
                func.count(),
                func.count(Trim.list_price_usd),  # skips NULLs, like SUM does
                func.sum(Trim.list_price_usd),
            )
            .select_from(Unit)
            .join(Trim, Trim.id == Unit.trim_id)
            .where(
                Unit.dealer_id == dealer_id,
                Unit.status == UnitStatus.IN_YARD,
                Unit.received_at < aged_before,
            )
        )
        units, priced_units, usd = self.session.execute(query).one()
        # SUM of no rows is NULL.
        return AgedValueRow(
            units=units, priced_units=priced_units, usd=usd if usd is not None else Decimal(0)
        )

    # --- Whole network ----------------------------------------------------------

    def network_by_trim(
        self,
        *,
        sales_since: datetime,
        now: datetime,
        trim_id: int | None = None,
        vehicle_model_id: int | None = None,
    ) -> Sequence[DealerTrimRow]:
        """Every dealer and trim with recent sales, in one query.

        Only trims that sold: without sales there is no shortage. Like by_trim,
        each count is aggregated in its own subquery so that joins cannot
        multiply rows. Units at the distribution center (no dealer) find no
        dealer to join and are left out.
        """
        sales = (
            select(Sale.dealer_id, Unit.trim_id, func.count().label("n"))
            .join(Unit, Unit.id == Sale.unit_id)
            .where(Sale.sold_at >= sales_since, Sale.sold_at <= now)
            .group_by(Sale.dealer_id, Unit.trim_id)
            .subquery()
        )
        stock = (
            select(
                Unit.dealer_id,
                Unit.trim_id,
                func.count().filter(Unit.status == UnitStatus.IN_YARD).label("in_yard"),
                func.count().filter(Unit.status == UnitStatus.IN_TRANSIT).label("in_transit"),
            )
            # Not needed by the counts: it only skips the sold units, most of the table.
            .where(Unit.status.in_([UnitStatus.IN_YARD, UnitStatus.IN_TRANSIT]))
            .group_by(Unit.dealer_id, Unit.trim_id)
            .subquery()
        )
        arrivals = (
            select(TransferOrder.destination_dealer_id, Unit.trim_id, func.count().label("n"))
            .join(Unit, Unit.id == TransferOrder.unit_id)
            .where(TransferOrder.status == TransferOrderStatus.PENDING)
            .group_by(TransferOrder.destination_dealer_id, Unit.trim_id)
            .subquery()
        )
        yard_stock = (
            select(Unit.dealer_id, func.count().label("n"))
            .where(Unit.status.in_(STOCK_STATUSES))
            .group_by(Unit.dealer_id)
            .subquery()
        )
        yard_arrivals = (
            select(TransferOrder.destination_dealer_id, func.count().label("n"))
            .where(TransferOrder.status == TransferOrderStatus.PENDING)
            .group_by(TransferOrder.destination_dealer_id)
            .subquery()
        )
        query = (
            # Labelled: rows are built by name, so the column order cannot mix them up.
            select(
                Dealer.id.label("dealer_id"),
                Dealer.code.label("dealer_code"),
                VehicleModel.id.label("vehicle_model_id"),
                VehicleModel.name.label("vehicle_model_name"),
                Trim.id.label("trim_id"),
                Trim.name.label("trim_name"),
                func.coalesce(stock.c.in_yard, 0).label("in_yard"),
                func.coalesce(stock.c.in_transit, 0).label("in_transit"),
                sales.c.n.label("recent_sales"),
                func.coalesce(arrivals.c.n, 0).label("pending_arrivals"),
                Dealer.yard_capacity,
                func.coalesce(yard_stock.c.n, 0).label("yard_stock_units"),
                func.coalesce(yard_arrivals.c.n, 0).label("yard_pending_arrivals"),
            )
            .select_from(sales)
            .join(Dealer, Dealer.id == sales.c.dealer_id)
            .join(Trim, Trim.id == sales.c.trim_id)
            .join(VehicleModel, VehicleModel.id == Trim.vehicle_model_id)
            .outerjoin(stock, and_(stock.c.dealer_id == Dealer.id, stock.c.trim_id == Trim.id))
            .outerjoin(
                arrivals,
                and_(arrivals.c.destination_dealer_id == Dealer.id, arrivals.c.trim_id == Trim.id),
            )
            .outerjoin(yard_stock, yard_stock.c.dealer_id == Dealer.id)
            .outerjoin(yard_arrivals, yard_arrivals.c.destination_dealer_id == Dealer.id)
            .where(*_catalog_filters(trim_id, vehicle_model_id))
        )
        return [DealerTrimRow(**row._mapping) for row in self.session.execute(query)]

    def aged_units(
        self,
        *,
        received_before: datetime,
        trim_id: int | None,
        vehicle_model_id: int | None,
        limit: int,
        offset: int,
        excluded: Collection[tuple[int, int]] = (),
    ) -> Sequence[AgedUnitRow]:
        """Units that could be moved today, oldest first, at a dealer or at the center."""
        query = (
            # Labelled: rows are built by name, so the column order cannot mix them up.
            select(
                Unit.id.label("unit_id"),
                Unit.vin,
                VehicleModel.id.label("vehicle_model_id"),
                VehicleModel.name.label("vehicle_model_name"),
                Trim.id.label("trim_id"),
                Trim.name.label("trim_name"),
                Unit.color,
                Unit.dealer_id,
                Dealer.code.label("dealer_code"),
                Unit.received_at,
            )
            .join(Trim, Trim.id == Unit.trim_id)
            .join(VehicleModel, VehicleModel.id == Trim.vehicle_model_id)
            # Outer: a unit at the distribution center has no dealer.
            .outerjoin(Dealer, Dealer.id == Unit.dealer_id)
            .where(*_aged(received_before, trim_id, vehicle_model_id, excluded))
            .order_by(Unit.received_at, Unit.id)
            .limit(limit)
            .offset(offset)
        )
        return [AgedUnitRow(**row._mapping) for row in self.session.execute(query)]

    def count_aged_units(
        self,
        *,
        received_before: datetime,
        trim_id: int | None,
        vehicle_model_id: int | None,
        excluded: Collection[tuple[int, int]] = (),
    ) -> int:
        query = (
            select(func.count())
            .select_from(Unit)
            .join(Trim, Trim.id == Unit.trim_id)
            .where(*_aged(received_before, trim_id, vehicle_model_id, excluded))
        )
        return self.session.scalar(query) or 0


def _catalog_filters(
    trim_id: int | None, vehicle_model_id: int | None
) -> list[ColumnElement[bool]]:
    """Needs Trim in the query."""
    conditions: list[ColumnElement[bool]] = []
    if trim_id is not None:
        conditions.append(Trim.id == trim_id)
    if vehicle_model_id is not None:
        conditions.append(Trim.vehicle_model_id == vehicle_model_id)
    return conditions


def _aged(
    received_before: datetime,
    trim_id: int | None,
    vehicle_model_id: int | None,
    excluded: Collection[tuple[int, int]] = (),
) -> list[ColumnElement[bool]]:
    """The same WHERE for the page and its count."""
    has_pending_order = exists().where(
        TransferOrder.unit_id == Unit.id, TransferOrder.status == TransferOrderStatus.PENDING
    )
    return [
        # In the yard: reserved, sold or in transit units cannot be moved.
        Unit.status == UnitStatus.IN_YARD,
        Unit.received_at < received_before,
        # Already proposed: the validator would reject it again.
        ~has_pending_order,
        *_catalog_filters(trim_id, vehicle_model_id),
        # Units of these (dealer, trim) pairs are left out. The distribution center
        # must be kept explicitly: (NULL, trim) NOT IN (...) is NULL, not true.
        *(
            [
                or_(
                    Unit.dealer_id.is_(None),
                    tuple_(Unit.dealer_id, Unit.trim_id).not_in(list(excluded)),
                )
            ]
            if excluded
            else []
        ),
    ]
