from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from stockpilot.core.config import Settings
from stockpilot.domain.delivery import Holiday
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.integrations.argentinadatos import ArgentinaDatosClient, build_argentina_datos

URL = "https://datos.example.test/v1/feriados"

# Part of a real response (2026-11-25): one holiday of each type.
RESPONSE = [
    {"fecha": "2026-07-09", "tipo": "inamovible", "nombre": "Día de la Independencia"},
    {"fecha": "2026-07-10", "tipo": "puente", "nombre": "Puente turístico no laborable"},
    {
        "fecha": "2026-11-23",
        "tipo": "trasladable",
        "nombre": "Día de la Soberanía Nacional (20/11)",
    },
]


@pytest.fixture
def client() -> Iterator[ArgentinaDatosClient]:
    settings = Settings(argentina_datos_url="https://datos.example.test", http_max_attempts=1)
    with httpx.Client() as http:
        yield build_argentina_datos(settings, http)


def test_holidays_of_every_type(client: ArgentinaDatosClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{URL}/2026").respond(json=RESPONSE)

    assert client.holidays(2026) == (
        Holiday(date(2026, 7, 9), "Día de la Independencia"),
        Holiday(date(2026, 7, 10), "Puente turístico no laborable"),
        Holiday(date(2026, 11, 23), "Día de la Soberanía Nacional (20/11)"),
    )


def test_each_year_is_cached(client: ArgentinaDatosClient, respx_mock: respx.MockRouter) -> None:
    this_year = respx_mock.get(f"{URL}/2026").respond(json=RESPONSE)
    next_year = respx_mock.get(f"{URL}/2027").respond(
        json=[{"fecha": "2027-01-01", "tipo": "inamovible", "nombre": "Año nuevo"}]
    )

    for _ in range(2):
        assert len(client.holidays(2026)) == 3
        assert len(client.holidays(2027)) == 1
    assert (this_year.call_count, next_year.call_count) == (1, 1)


def test_year_not_published_yet(client: ArgentinaDatosClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{URL}/2028").respond(404, json={"error": "Not found"})

    with pytest.raises(ExternalServiceError, match="ArgentinaDatos is not available: HTTP 404"):
        client.holidays(2028)


@pytest.mark.parametrize(
    "response",
    [
        pytest.param([], id="no-holidays"),
        pytest.param([{"fecha": "09/07/2026", "nombre": "X"}], id="date-format"),
        pytest.param([{"nombre": "X"}], id="no-date"),
        pytest.param({"error": "Not found"}, id="not-a-list"),
    ],
)
def test_unexpected_responses_are_rejected(
    client: ArgentinaDatosClient, respx_mock: respx.MockRouter, response: Any
) -> None:
    respx_mock.get(f"{URL}/2026").respond(json=response)

    with pytest.raises(ExternalServiceError, match="unexpected response"):
        client.holidays(2026)
