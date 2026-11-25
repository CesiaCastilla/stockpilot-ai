from dataclasses import dataclass
from decimal import Decimal

from stockpilot.domain.exchange_rate import ExchangeRate


@dataclass(frozen=True)
class AgedStockValue:
    """List price of the units that have been too long in a yard."""

    units: int
    # Not in the amounts below: the real value is higher.
    units_without_price: int
    usd: Decimal
    # None when there is no exchange rate available.
    ars: Decimal | None
    rate: ExchangeRate | None
