from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from stockpilot.core.config import Settings
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.domain.exchange_rate import ExchangeRate
from stockpilot.integrations.dolarapi import DolarApiClient, build_dolar_api

URL = "https://dolar.example.test/v1/dolares/oficial"

# A real response (2026-11-24), with decimals added to the prices.
RESPONSE = """{
  "moneda": "USD",
  "casa": "oficial",
  "nombre": "Oficial",
  "compra": 1565.10,
  "venta": 1615.3,
  "fechaActualizacion": "2026-11-24T18:00:00.000Z"
}"""


@pytest.fixture
def client() -> Iterator[DolarApiClient]:
    settings = Settings(dolar_api_url="https://dolar.example.test", http_max_attempts=1)
    with httpx.Client() as http:
        yield build_dolar_api(settings, http)


def test_official_rate(client: DolarApiClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).respond(text=RESPONSE)

    assert client.official_rate() == ExchangeRate(
        buy=Decimal("1565.10"),
        sell=Decimal("1615.3"),
        updated_at=datetime(2026, 11, 24, 18, tzinfo=UTC),
    )


def test_rate_is_cached(client: DolarApiClient, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).respond(text=RESPONSE)

    assert client.official_rate() == client.official_rate()
    assert route.call_count == 1


def test_failure_is_reported_and_not_cached(respx_mock: respx.MockRouter) -> None:
    settings = Settings(
        dolar_api_url="https://dolar.example.test",
        http_max_attempts=1,
        http_failure_cooldown_seconds=0,
    )
    client = build_dolar_api(settings, httpx.Client())
    respx_mock.get(URL).side_effect = [httpx.Response(503), httpx.Response(200, text=RESPONSE)]

    with pytest.raises(ExternalServiceError, match="DolarApi is not available: HTTP 503"):
        client.official_rate()

    assert client.official_rate().sell == Decimal("1615.3")


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"compra": 0}, id="zero"),
        pytest.param({"venta": -1}, id="negative"),
        pytest.param({"venta": None}, id="null"),
        pytest.param({"fechaActualizacion": "2026-11-24T18:00:00"}, id="no-time-zone"),
    ],
)
def test_impossible_quotes_are_rejected(
    client: DolarApiClient, respx_mock: respx.MockRouter, fields: dict[str, Any]
) -> None:
    quote = {"compra": 1565, "venta": 1615, "fechaActualizacion": "2026-11-24T18:00:00Z"}
    respx_mock.get(URL).respond(json=quote | fields)

    with pytest.raises(ExternalServiceError, match="unexpected response"):
        client.official_rate()
