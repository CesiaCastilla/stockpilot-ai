from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stockpilot.domain.exchange_rate import ExchangeRate


@pytest.mark.parametrize(
    ("usd", "expected"),
    [
        pytest.param("100", "161550.00", id="sell-price"),
        pytest.param("0.01", "16.16", id="rounded-to-cents"),
        pytest.param("0", "0.00", id="zero"),
    ],
)
def test_to_ars(usd: str, expected: str) -> None:
    rate = ExchangeRate(
        buy=Decimal("1565"), sell=Decimal("1615.50"), updated_at=datetime(2026, 11, 25, tzinfo=UTC)
    )

    assert str(rate.to_ars(Decimal(usd))) == expected
