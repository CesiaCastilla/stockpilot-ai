from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class ExchangeRate:
    """Official US dollar quote, in Argentine pesos per dollar."""

    buy: Decimal
    sell: Decimal
    # When the source updated it, not when we asked: the market closes at
    # night and on weekends, so a fresh answer can carry an old quote.
    updated_at: datetime
