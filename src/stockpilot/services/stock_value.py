from datetime import datetime
from typing import Protocol

import structlog
from sqlalchemy.orm import Session

from stockpilot.domain.coverage import CoveragePolicy
from stockpilot.domain.errors import ExternalServiceError, NotFoundError
from stockpilot.domain.exchange_rate import ExchangeRate
from stockpilot.domain.stock_value import AgedStockValue
from stockpilot.repositories.coverage import CoverageRepository
from stockpilot.repositories.dealers import DealerRepository

logger = structlog.get_logger(__name__)


class ExchangeRateSource(Protocol):
    def official_rate(self) -> ExchangeRate:
        """Raises ExternalServiceError."""
        ...


class StockValueService:
    """Money tied up in aged stock. Calculated here, never by the AI model."""

    def __init__(self, session: Session, policy: CoveragePolicy, rates: ExchangeRateSource) -> None:
        self.session = session
        self.dealers = DealerRepository(session)
        self.coverage = CoverageRepository(session)
        self._policy = policy
        self._rates = rates

    def aged_stock(self, dealer_id: int, *, now: datetime) -> AgedStockValue:
        if self.dealers.get(dealer_id) is None:
            raise NotFoundError("Dealer", dealer_id)
        aged = self.coverage.aged_value(dealer_id, aged_before=self._policy.aged_before(now))
        # Ends the transaction: none stays open while an external API answers.
        self.session.commit()

        rate = self._rate()
        return AgedStockValue(
            units=aged.units,
            units_without_price=aged.units - aged.priced_units,
            usd=aged.usd,
            ars=rate.to_ars(aged.usd) if rate is not None else None,
            rate=rate,
        )

    def _rate(self) -> ExchangeRate | None:
        try:
            return self._rates.official_rate()
        except ExternalServiceError as error:
            # The value in dollars is still useful without it.
            logger.warning("stock_value_without_rate", error=str(error))
            return None
