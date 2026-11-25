from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from stockpilot.core.config import Settings
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.domain.vin_info import VehicleInfo
from stockpilot.integrations.vpic import VpicClient, build_vpic

VIN = "8ZZ84G7Y4TA000001"
URL = f"https://vpic.example.test/DecodeVinValues/{VIN}"


def _response(**fields: Any) -> dict[str, Any]:
    """Shape of a real response (2026-11-25), with made-up values."""
    vehicle = {"Make": "", "Model": "", "ModelYear": "", "ErrorCode": "7", "VIN": VIN}
    return {"Count": 1, "Message": "", "SearchCriteria": "", "Results": [vehicle | fields]}


@pytest.fixture
def client() -> Iterator[VpicClient]:
    settings = Settings(vpic_url="https://vpic.example.test", http_max_attempts=1)
    with httpx.Client() as http:
        yield build_vpic(settings, http)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        pytest.param(
            {"Make": "ACME", "Model": "Roadster", "ModelYear": "2026", "ErrorCode": "0"},
            VehicleInfo("ACME", "Roadster", 2026),
            id="complete",
        ),
        pytest.param(
            {"Make": "ACME", "Model": "Roadster", "ModelYear": "2026", "ErrorCode": "1"},
            VehicleInfo("ACME", "Roadster", 2026),
            id="wrong-check-digit-still-decodes",
        ),
        pytest.param(
            {"Make": "ACME", "ModelYear": "2026", "ErrorCode": "1,8"},
            VehicleInfo("ACME", None, 2026),
            id="partial",
        ),
        pytest.param({}, VehicleInfo(), id="unknown-manufacturer"),
    ],
)
def test_decode(
    client: VpicClient,
    respx_mock: respx.MockRouter,
    fields: dict[str, Any],
    expected: VehicleInfo,
) -> None:
    respx_mock.get(URL, params={"format": "json"}).respond(json=_response(**fields))

    assert client.decode(VIN) == expected


def test_each_vin_is_cached(client: VpicClient, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).respond(json=_response(Make="ACME"))
    other = respx_mock.get(url__startswith="https://vpic.example.test/DecodeVinValues/8ZZVX")
    other.respond(json=_response())

    for _ in range(2):
        assert client.decode(VIN).make == "ACME"
        assert client.decode("8ZZVXRD65TA000002").make is None
    assert (route.call_count, other.call_count) == (1, 1)


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"Results": []}, id="no-results"),
        pytest.param({"Results": _response()["Results"] * 2}, id="two-results"),
        pytest.param({"Results": [{"Model": "", "ModelYear": ""}]}, id="no-make"),
        pytest.param({"Results": [{"Make": "", "ModelYear": ""}]}, id="no-model"),
        pytest.param({"Results": [{"Make": "", "Model": ""}]}, id="no-year"),
        pytest.param(_response(ModelYear="soon"), id="year-is-not-a-number"),
    ],
)
def test_unexpected_responses_are_rejected(
    client: VpicClient, respx_mock: respx.MockRouter, response: dict[str, Any]
) -> None:
    respx_mock.get(URL).respond(json=response)

    with pytest.raises(ExternalServiceError, match="vPIC is not available: unexpected response"):
        client.decode(VIN)
