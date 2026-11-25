"""Seed a fictitious dealer network for local development and demos.

Everything is invented: model names, dealers and VINs belong to no real
manufacturer. The same --seed always builds the same network, so a bug found
on the demo data can be reproduced.

The data is generated on purpose with the two problems StockPilot solves:
- shortages: dealers that keep selling a trim they have no stock of;
- aging: units sitting for months at dealers where that trim does not sell.

Every unit comes with a history (unit_events) that the real service could
have produced: same status machine, same registration rules.

    uv run python scripts/seed.py           # refuses to touch a non-empty database
    uv run python scripts/seed.py --reset   # wipes the business tables first
"""

import argparse
import math
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from stockpilot.core.config import get_settings
from stockpilot.core.database import build_engine, build_session_factory
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.domain.vin import VIN_ALPHABET, check_digit
from stockpilot.models import Dealer, Reservation, Sale, Trim, Unit, UnitEvent, VehicleModel

# Fictitious models and prices: {model: [(trim, national popularity weight, list price in USD)]}
CATALOG: dict[str, list[tuple[str, float, int]]] = {
    "Aconcagua": [("Base 4x2", 1.0, 36_000), ("SR 4x4", 1.6, 45_000), ("Limited 4x4", 0.7, 52_000)],
    "Talampaya": [("Comfort", 1.3, 27_000), ("Highline", 0.8, 31_500)],
    "Tandil": [("Start", 1.8, 17_500), ("Plus", 1.4, 19_800), ("Sport", 0.5, 22_400)],
    "Ibera": [("Comfort", 0.9, 33_000), ("Premium", 0.5, 38_500)],
    "Lanin": [("Cargo", 0.6, 28_500), ("Passenger", 0.3, 32_000)],
}

COLORS: dict[str, float] = {
    "White": 3.0,
    "Black": 2.0,
    "Silver": 2.0,
    "Gray": 1.5,
    "Red": 0.8,
    "Blue": 0.7,
}

# (province, city) as Georef names them. Dealers are generic and fictitious.
DEALER_LOCATIONS: list[tuple[str, str]] = [
    ("Ciudad Autónoma de Buenos Aires", "Ciudad Autónoma de Buenos Aires"),
    ("Buenos Aires", "La Plata"),
    ("Buenos Aires", "Mar del Plata"),
    ("Buenos Aires", "Bahía Blanca"),
    ("Buenos Aires", "Pilar"),
    ("Buenos Aires", "Tandil"),
    ("Córdoba", "Córdoba"),
    ("Córdoba", "Río Cuarto"),
    ("Córdoba", "Villa María"),
    ("Santa Fe", "Rosario"),
    ("Santa Fe", "Santa Fe"),
    ("Santa Fe", "Rafaela"),
    ("Mendoza", "Mendoza"),
    ("Mendoza", "San Rafael"),
    ("Tucumán", "San Miguel de Tucumán"),
    ("Salta", "Salta"),
    ("Jujuy", "San Salvador de Jujuy"),
    ("Neuquén", "Neuquén"),
    ("Río Negro", "San Carlos de Bariloche"),
    ("Chubut", "Comodoro Rivadavia"),
    ("Chubut", "Trelew"),
    ("Entre Ríos", "Paraná"),
    ("Corrientes", "Corrientes"),
    ("Misiones", "Posadas"),
    ("Chaco", "Resistencia"),
    ("San Juan", "San Juan"),
    ("San Luis", "San Luis"),
    ("La Pampa", "Santa Rosa"),
    ("Santiago del Estero", "Santiago del Estero"),
    ("Santa Cruz", "Río Gallegos"),
]

# Share of dealer x trim combinations forced into each problem scenario.
NO_DEMAND_SHARE = 0.10  # never sells there -> its stock ages
SOLD_OUT_SHARE = 0.12  # sells there but gets no stock -> shortage
DISTRIBUTION_CENTER_SHARE = 0.10  # stock not yet assigned to any dealer
RECENT_SALE_DAYS = 7  # sold but not yet delivered
TRANSIT_DAYS = (1.0, 4.0)  # trip from the distribution center to a dealer

BUSINESS_TABLES = (
    "unit_events",
    "sales",
    "reservations",
    "units",
    "trims",
    "vehicle_models",
    "dealers",
)

# --- VIN ---------------------------------------------------------------------

_VIN_CHARS = "".join(sorted(VIN_ALPHABET))


def fake_vin(rng: random.Random, serial: int) -> str:
    """Made-up but well-formed VIN; the serial number keeps every VIN unique."""
    wmi = "8ZZ"  # fictitious manufacturer code
    descriptor = "".join(rng.choice(_VIN_CHARS) for _ in range(5))
    year, plant = "T", "A"  # 2026, plant A
    vin = f"{wmi}{descriptor}0{year}{plant}{serial:06d}"
    # Not required outside North America, but a valid one keeps the data realistic.
    return vin[:8] + check_digit(vin) + vin[9:]


# --- Data generation (pure: no database) --------------------------------------


@dataclass(frozen=True)
class SeedConfig:
    dealers: int = 30
    stock_units: int = 2000  # units currently in the network, besides the sold ones
    sales_days: int = 180
    daily_network_sales: float = 25.0
    random_seed: int = 42


@dataclass
class DemoData:
    dealers: list[Dealer] = field(default_factory=list)
    trims: list[Trim] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    reservations: list[Reservation] = field(default_factory=list)
    sales: list[Sale] = field(default_factory=list)
    events: list[UnitEvent] = field(default_factory=list)

    @property
    def stock_units(self) -> list[Unit]:
        return [u for u in self.units if u.status not in (UnitStatus.SOLD, UnitStatus.DELIVERED)]


Cell = tuple[int, int]  # (dealer index, trim index)


class _Builder:
    def __init__(self, config: SeedConfig, now: datetime) -> None:
        if config.dealers > len(DEALER_LOCATIONS):
            raise ValueError(f"At most {len(DEALER_LOCATIONS)} dealers are supported")
        self.config = config
        self.now = now
        self.rng = random.Random(config.random_seed)
        # Separate generator: adding history did not change the network itself.
        self.history_rng = random.Random(f"history-{config.random_seed}")
        self.data = DemoData()
        self._next_serial = 1

    def build(self) -> DemoData:
        self._build_catalog()
        self._build_dealers()
        sizes = [self.rng.uniform(0.5, 2.0) for _ in self.data.dealers]
        rates = self._daily_sales_rates(sizes)
        self._build_stock(sizes, rates)
        self._build_sales(rates)
        return self.data

    def _build_catalog(self) -> None:
        for model_name, trims in CATALOG.items():
            model = VehicleModel(name=model_name)
            self.data.trims += [
                Trim(vehicle_model=model, name=name, list_price_usd=Decimal(price))
                for name, _, price in trims
            ]
        self._popularity = [weight for trims in CATALOG.values() for _, weight, _ in trims]

    def _build_dealers(self) -> None:
        for number, (province, city) in enumerate(DEALER_LOCATIONS[: self.config.dealers], start=1):
            self.data.dealers.append(
                Dealer(
                    code=f"D{number:03d}",
                    name=f"Automotores {city}",
                    province=province,
                    city=city,
                    yard_capacity=1,  # set once the stock is allocated
                )
            )

    def _cells(self) -> list[Cell]:
        return [(d, t) for d in range(len(self.data.dealers)) for t in range(len(self.data.trims))]

    def _daily_sales_rates(self, sizes: list[float]) -> dict[Cell, float]:
        demand = {
            (d, t): 0.0
            if self.rng.random() < NO_DEMAND_SHARE
            else sizes[d] * self._popularity[t] * self.rng.uniform(0.3, 1.7)
            for d, t in self._cells()
        }
        scale = self.config.daily_network_sales / sum(demand.values())
        # Probability of selling one unit per day; capped so it stays a probability.
        return {cell: min(value * scale, 0.9) for cell, value in demand.items()}

    def _build_stock(self, sizes: list[float], rates: dict[Cell, float]) -> None:
        # Stock is allocated independently of demand, as a mismatched network would be.
        weights = {
            (d, t): 0.0
            if rates[(d, t)] > 0 and self.rng.random() < SOLD_OUT_SHARE
            else sizes[d] * self.rng.uniform(0.2, 1.8)
            for d, t in self._cells()
        }
        at_center = round(self.config.stock_units * DISTRIBUTION_CENTER_SHARE)
        cells = list(weights)
        allocation = Counter(
            self.rng.choices(
                cells, weights=list(weights.values()), k=self.config.stock_units - at_center
            )
        )

        for (d, t), count in sorted(allocation.items()):
            never_sells = rates[(d, t)] == 0
            for _ in range(count):
                self._add_dealer_stock_unit(d, t, aged=never_sells)

        center_trims = self.rng.choices(
            range(len(self.data.trims)), weights=self._popularity, k=at_center
        )
        for t in center_trims:
            days = self.rng.uniform(0, 30)
            unit = self._new_unit(t, None, UnitStatus.IN_YARD, self.now - timedelta(days=days))
            self._log(unit, (UnitStatus.IN_YARD, unit.received_at))

        # Capacity >= current stock; ratios close to 1.0 leave some yards almost full.
        stock_per_dealer: Counter[int] = Counter()
        for (d, _trim), count in allocation.items():
            stock_per_dealer[d] += count
        for d, dealer in enumerate(self.data.dealers):
            stock = stock_per_dealer[d]
            dealer.yard_capacity = max(10, math.ceil(stock * self.rng.uniform(1.0, 1.6)))

    def _add_dealer_stock_unit(self, d: int, t: int, *, aged: bool) -> None:
        # Aged cells: 3 to 10 months in the yard. Others: mostly recent, long tail.
        days = self.rng.uniform(90, 300) if aged else min(self.rng.expovariate(1 / 40), 300)
        received_at = self.now - timedelta(days=days)

        roll = self.rng.random()
        if roll < 0.07:
            status = UnitStatus.IN_TRANSIT
        elif roll < 0.15:
            status = UnitStatus.RESERVED
        else:
            status = UnitStatus.IN_YARD
        unit = self._new_unit(t, self.data.dealers[d], status, received_at)

        if status is UnitStatus.IN_TRANSIT:
            self._log_shipment(unit)
            return

        # A customer reserves a unit that is already in the yard.
        arrived_at = self._arrival(received_at, before=self.now)
        steps = [(UnitStatus.IN_TRANSIT, received_at), (UnitStatus.IN_YARD, arrived_at)]
        if status is UnitStatus.RESERVED:
            earliest = max(arrived_at, self.now - timedelta(days=10))
            reserved_at = earliest + (self.now - earliest) * self.rng.random()
            steps.append((UnitStatus.RESERVED, reserved_at))
            self.data.reservations.append(
                Reservation(unit=unit, dealer=unit.dealer, reserved_at=reserved_at)
            )
        elif self.rng.random() < 0.05:
            # History: a reservation that fell through.
            span = self.now - arrived_at
            reserved_at = arrived_at + span * self.rng.uniform(0, 0.5)
            cancelled_at = min(self.now, reserved_at + timedelta(days=self.rng.uniform(1, 5)))
            steps += [(UnitStatus.RESERVED, reserved_at), (UnitStatus.IN_YARD, cancelled_at)]
            self.data.reservations.append(
                Reservation(
                    unit=unit,
                    dealer=unit.dealer,
                    reserved_at=reserved_at,
                    cancelled_at=cancelled_at,
                )
            )
        self._log(unit, *steps)

    def _build_sales(self, rates: dict[Cell, float]) -> None:
        for day in range(1, self.config.sales_days + 1):
            for (d, t), rate in rates.items():
                if rate == 0 or self.rng.random() >= rate:
                    continue
                sold_at = self.now - timedelta(days=self.rng.uniform(day - 1, day))
                received_at = sold_at - timedelta(days=self.rng.uniform(5, 90))
                recent = self.now - sold_at < timedelta(days=RECENT_SALE_DAYS)
                status = UnitStatus.SOLD if recent else UnitStatus.DELIVERED
                dealer = self.data.dealers[d]
                unit = self._new_unit(t, dealer, status, received_at)
                self.data.sales.append(Sale(unit=unit, dealer=dealer, sold_at=sold_at))

                steps = [
                    (UnitStatus.IN_TRANSIT, received_at),
                    (UnitStatus.IN_YARD, self._arrival(received_at, before=sold_at)),
                    (UnitStatus.SOLD, sold_at),
                ]
                if status is UnitStatus.DELIVERED:
                    days = self.history_rng.uniform(1, RECENT_SALE_DAYS)
                    steps.append((UnitStatus.DELIVERED, sold_at + timedelta(days=days)))
                self._log(unit, *steps)

    def _new_unit(
        self, t: int, dealer: Dealer | None, status: UnitStatus, received_at: datetime
    ) -> Unit:
        unit = Unit(
            vin=fake_vin(self.rng, self._next_serial),
            trim=self.data.trims[t],
            color=self.rng.choices(list(COLORS), weights=list(COLORS.values()))[0],
            status=status,
            dealer=dealer,
            received_at=received_at,
        )
        self._next_serial += 1
        self.data.units.append(unit)
        return unit

    # History: the events UnitService would have recorded.

    def _arrival(self, shipped_at: datetime, *, before: datetime) -> datetime:
        """When the trip ended, always before the next thing that happened to the unit."""
        trip = timedelta(days=self.history_rng.uniform(*TRANSIT_DAYS))
        return shipped_at + min(trip, (before - shipped_at) / 2)

    def _log_shipment(self, unit: Unit) -> None:
        """A unit on its way right now. A trip takes days, so an older unit
        waited at the distribution center and was shipped recently."""
        trip = timedelta(days=self.history_rng.uniform(0, TRANSIT_DAYS[1]))
        shipped_at = self.now - trip
        if unit.received_at >= shipped_at:
            self._log(unit, (UnitStatus.IN_TRANSIT, unit.received_at))
            return
        self._log(
            unit,
            (UnitStatus.IN_YARD, unit.received_at),
            (UnitStatus.IN_TRANSIT, shipped_at),
            from_center=True,
        )

    def _log(
        self, unit: Unit, *steps: tuple[UnitStatus, datetime], from_center: bool = False
    ) -> None:
        """Record the steps in order. from_center: the first one happened at the
        distribution center, before the unit had a dealer."""
        previous: UnitStatus | None = None
        for status, occurred_at in steps:
            self.data.events.append(
                UnitEvent(
                    unit=unit,
                    from_status=previous,
                    to_status=status,
                    dealer=None if from_center and previous is None else unit.dealer,
                    occurred_at=occurred_at,
                )
            )
            previous = status


def build_demo_data(config: SeedConfig, now: datetime) -> DemoData:
    """Build the whole network in memory. `now` is injected so tests are reproducible."""
    return _Builder(config, now).build()


# --- Persistence ---------------------------------------------------------------


class DatabaseNotEmptyError(RuntimeError):
    pass


def seed(session: Session, data: DemoData, *, reset: bool = False) -> None:
    """Insert the demo data. The caller owns the transaction (commit or rollback)."""
    if reset:
        session.execute(text(f"TRUNCATE {', '.join(BUSINESS_TABLES)} RESTART IDENTITY CASCADE"))
    elif session.scalar(select(func.count()).select_from(Dealer)):
        raise DatabaseNotEmptyError(
            "The database already has data. Run with --reset to wipe it and seed again."
        )
    session.add_all(
        [
            *data.dealers,
            *data.trims,
            *data.units,
            *data.reservations,
            *data.sales,
            *data.events,
        ]
    )
    session.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--reset", action="store_true", help="wipe business tables first")
    parser.add_argument("--seed", type=int, default=SeedConfig.random_seed, help="random seed")
    args = parser.parse_args(argv)

    data = build_demo_data(SeedConfig(random_seed=args.seed), now=datetime.now(UTC))
    engine = build_engine(get_settings())
    try:
        with build_session_factory(engine)() as session, session.begin():
            seed(session, data, reset=args.reset)
    except DatabaseNotEmptyError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print(
        f"Seeded {len(data.dealers)} dealers, {len(data.trims)} trims, "
        f"{len(data.stock_units)} units in stock, {len(data.sales)} sales, "
        f"{len(data.reservations)} reservations and {len(data.events)} unit events."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
