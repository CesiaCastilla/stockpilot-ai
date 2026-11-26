"""Builds the small network of one scenario in the database."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

from sqlalchemy.orm import Session

from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models import Dealer, Sale, TransferOrder, Trim, Unit, VehicleModel
from stockpilot.schemas.agent_runs import AgentRunRequest
from tests.evals.scenario import CENTER, Scenario

# Every eval sees the same day, so the ages in the YAML are exact.
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

# Fictitious models and trims, with fictitious list prices in dollars.
CATALOG: dict[str, dict[str, int]] = {
    "Aconcagua": {"SR": 38_000, "SR 4x4": 45_000},
    "Tandil": {"Start": 21_000, "Plus": 25_000},
    "Lanin": {"Cargo": 30_000},
}


@dataclass
class World:
    """What was built, with the names the YAML uses."""

    dealers: dict[str, Dealer]
    trims: dict[str, Trim]
    # VIN -> label, for the units that have one.
    labels: dict[str, str] = field(default_factory=dict)

    def code(self, dealer_id: int | None) -> str:
        if dealer_id is None:
            return CENTER
        return next(code for code, dealer in self.dealers.items() if dealer.id == dealer_id)

    def request(self, scenario: Scenario) -> AgentRunRequest:
        model_id = None
        if scenario.request.model is not None:
            model_id = next(
                t.vehicle_model_id
                for name, t in self.trims.items()
                if name.split(" ", 1)[0] == scenario.request.model
            )
        trim = scenario.request.trim
        return AgentRunRequest(
            max_proposals=scenario.request.max_proposals,
            trim_id=None if trim is None else self.trims[trim].id,
            vehicle_model_id=model_id,
        )


def build_world(session: Session, scenario: Scenario) -> World:
    trims: dict[str, Trim] = {}
    for model_name, trim_prices in CATALOG.items():
        model = VehicleModel(name=model_name)
        for trim_name, price in trim_prices.items():
            trims[f"{model_name} {trim_name}"] = Trim(
                vehicle_model=model, name=trim_name, list_price_usd=Decimal(price)
            )
    dealers = {
        code: Dealer(
            code=code,
            name=f"Concesionario {spec.city}",
            province=spec.city,
            city=spec.city,
            yard_capacity=spec.capacity,
            latitude=spec.at[0],
            longitude=spec.at[1],
        )
        for code, spec in scenario.dealers.items()
    }
    session.add_all([*trims.values(), *dealers.values()])
    world = World(dealers=dealers, trims=trims)
    serial = count(1)

    def new_unit(trim: str, dealer: str, status: UnitStatus, days: int) -> Unit:
        unit = Unit(
            vin=f"8AFZZZ{next(serial):011d}",
            trim=trims[trim],
            color="Blanco",
            status=status,
            dealer=None if dealer == CENTER else dealers[dealer],
            received_at=NOW - timedelta(days=days),
        )
        session.add(unit)
        return unit

    for stock in scenario.stock:
        for _ in range(stock.count):
            unit = new_unit(stock.trim, stock.dealer, stock.status, stock.days)
            if stock.label:
                world.labels[unit.vin] = stock.label
            if stock.pending_to:
                session.add(
                    TransferOrder(
                        unit=unit,
                        origin_dealer=unit.dealer,
                        destination_dealer=dealers[stock.pending_to],
                        status=TransferOrderStatus.PENDING,
                        reason="Propuesta anterior",
                    )
                )
    for sales in scenario.sales:
        for _ in range(sales.count):
            sold = new_unit(sales.trim, sales.dealer, UnitStatus.SOLD, sales.days_ago + 30)
            session.add(
                Sale(
                    unit=sold, dealer=dealers[sales.dealer], sold_at=NOW - timedelta(sales.days_ago)
                )
            )
    session.flush()
    return world
