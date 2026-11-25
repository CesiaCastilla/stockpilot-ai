import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from stockpilot.core.config import Settings
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.domain.geo import Coordinates, Location, Place
from stockpilot.integrations.georef import GeorefClient, build_georef

URL = "https://georef.example.test/localidades-censales"


def _city(name: str, province: str, lat: float, lon: float) -> dict[str, Any]:
    return {
        "centroide": {"lat": lat, "lon": lon},
        "id": "1",
        "nombre": name,
        "provincia": {"nombre": province},
    }


def _results(*results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"resultados": [{"cantidad": len(r), "localidades_censales": r} for r in results]}


# Shape and names of a real response (2026-11-25): found, ambiguous and not found.
RESPONSE = _results(
    [_city("Córdoba", "Córdoba", -31.4168, -64.1836)],
    [
        _city("San José", "Catamarca", -28.3, -65.7),
        _city("San José", "Catamarca", -26.7, -66.0),
    ],
    [],
)
PLACES = [
    Place("CORDOBA", "cordoba"),
    Place("Catamarca", "San José"),
    Place("Santa Fe", "Rosarioo"),
]


@pytest.fixture
def client() -> Iterator[GeorefClient]:
    settings = Settings(georef_url="https://georef.example.test", http_max_attempts=2)
    with httpx.Client() as http:
        yield build_georef(settings, http)


def test_matches_of_each_place_in_order(client: GeorefClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).respond(json=RESPONSE)

    cordoba, san_jose, rosarioo = client.locate(PLACES)

    assert cordoba == (Location("Córdoba", "Córdoba", Coordinates(-31.4168, -64.1836)),)
    assert len(san_jose) == 2
    assert rosarioo == ()


def test_asks_for_exact_names_in_a_single_request(
    client: GeorefClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(URL).respond(json=RESPONSE)

    client.locate(PLACES)

    assert route.call_count == 1
    queries = json.loads(route.calls.last.request.content)["localidades_censales"]
    assert len(queries) == 3
    assert queries[0] == {
        "nombre": "cordoba",
        "provincia": "CORDOBA",
        "exacto": True,
        "max": 2,
        "campos": "nombre,provincia.nombre,centroide",
    }


def test_rate_limit_is_retried(client: GeorefClient, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(URL)
    route.side_effect = [httpx.Response(429), httpx.Response(200, json=RESPONSE)]

    assert len(client.locate(PLACES)) == 3
    assert route.call_count == 2


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(_results([], []), id="fewer-results-than-places"),
        pytest.param(_results([], [], [], []), id="more-results-than-places"),
        pytest.param(_results([_city("X", "X", -91, 0)], [], []), id="latitude-out-of-range"),
        pytest.param(_results([_city("X", "X", 0, 181)], [], []), id="longitude-out-of-range"),
        pytest.param({"resultados": [{}, {}, {}]}, id="no-cities"),
        pytest.param({"errores": []}, id="no-results"),
    ],
)
def test_unexpected_responses_are_rejected(
    client: GeorefClient, respx_mock: respx.MockRouter, response: dict[str, Any]
) -> None:
    respx_mock.post(URL).respond(json=response)

    with pytest.raises(ExternalServiceError, match="Georef is not available: unexpected response"):
        client.locate(PLACES)
