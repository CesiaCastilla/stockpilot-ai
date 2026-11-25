from typing import Any

import pytest
from pydantic import ValidationError

from stockpilot.schemas.catalog import TrimCreate


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        (25000, "25000.00"),
        (25000.5, "25000.50"),
        ("25000.99", "25000.99"),
        ("0.01", "0.01"),
        ("99999999.99", "99999999.99"),
    ],
)
def test_price_always_has_two_decimals(sent: Any, stored: str) -> None:
    # The database answers "25000.00": so must the response of the POST.
    assert str(TrimCreate(name="Base", list_price_usd=sent).list_price_usd) == stored


@pytest.mark.parametrize("price", [0, -1, "25000.999", "100000000", "NaN", "Infinity", "cheap"])
def test_impossible_prices_are_rejected(price: Any) -> None:
    with pytest.raises(ValidationError):
        TrimCreate(name="Base", list_price_usd=price)


def test_price_is_optional() -> None:
    assert TrimCreate(name="Base").list_price_usd is None
