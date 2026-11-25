"""The job end to end: settings, HTTP client (mocked network), database and exit code."""

import json
from collections.abc import Iterator

import pytest
import respx
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from stockpilot.core.config import Settings
from stockpilot.core.logging import configure_logging
from stockpilot.models import Dealer
from stockpilot.workflows.sync_dealer_locations import run

pytestmark = pytest.mark.integration

URL = "https://georef.example.test/localidades-censales"


@pytest.fixture
def settings(postgres_url: str, migrated_engine: Engine) -> Iterator[Settings]:
    # The job opens its own session and commits, so the data is cleaned up by hand.
    try:
        with Session(migrated_engine) as session:
            session.add(
                Dealer(code="JOB1", name="X", province="cordoba", city="cordoba", yard_capacity=1)
            )
            session.commit()
        yield Settings(
            database_url=postgres_url,
            georef_url="https://georef.example.test",
            http_max_attempts=1,
            log_format="json",
        )
    finally:
        # The job pointed the logs to the output captured by the test.
        configure_logging(Settings())
        with Session(migrated_engine) as session:
            session.execute(delete(Dealer))
            session.commit()


def _latitude(engine: Engine) -> float | None:
    with Session(engine) as session:
        return session.scalar(select(Dealer.latitude).where(Dealer.code == "JOB1"))


def test_job_locates_dealers(
    settings: Settings, migrated_engine: Engine, respx_mock: respx.MockRouter
) -> None:
    city = {
        "nombre": "Córdoba",
        "provincia": {"nombre": "Córdoba"},
        "centroide": {"lat": -31.4, "lon": -64.2},
    }
    respx_mock.post(URL).respond(json={"resultados": [{"localidades_censales": [city]}]})

    assert run(settings) == 0
    assert _latitude(migrated_engine) == -31.4


def test_every_event_of_a_run_has_the_same_id(
    settings: Settings, respx_mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    respx_mock.post(URL).respond(503)

    run(settings)
    run(settings)

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["message"] for e in events] == [
        "external_api_failed",
        "dealer_location_sync_failed",
    ] * 2
    first, also_first, second, also_second = (e["correlation_id"] for e in events)
    assert first == also_first
    assert second == also_second
    assert first != second


def test_job_reports_a_failure_with_its_exit_code(
    settings: Settings, migrated_engine: Engine, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(URL).respond(503)

    assert run(settings) == 1
    assert _latitude(migrated_engine) is None
