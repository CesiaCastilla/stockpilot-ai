"""Fixtures for the evals: a real PostgreSQL, like the integration tests, and the
real AI model from .env. Run them with: uv run pytest -m eval"""

import os
import time
from collections.abc import Collection, Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stockpilot.core.config import Settings
from stockpilot.domain.delivery import Holiday
from stockpilot.domain.exchange_rate import ExchangeRate
from stockpilot.domain.vin_info import VehicleInfo
from tests.evals.report import Report
from tests.evals.scenario import load_scenarios
from tests.evals.world import NOW

# The same PostgreSQL container and rolled-back sessions as the integration tests.
from tests.integration.conftest import (  # noqa: F401 (fixtures, found by name)
    alembic_config,
    db_session,
    migrated_engine,
    postgres_url,
    session_factory,
)


class FixedExternalApis:
    """The evals measure the model: the external APIs always answer the same."""

    def decode(self, vin: str) -> VehicleInfo:
        return VehicleInfo()

    def holidays(self, year: int) -> Collection[Holiday]:
        return []

    def official_rate(self) -> ExchangeRate:
        return ExchangeRate(buy=Decimal(1580), sell=Decimal(1630), updated_at=NOW)


@pytest.fixture(scope="session")
def eval_settings() -> Settings:
    # More retries than the app: a full run can hit the free tier's per-minute limit.
    settings = Settings(llm_max_retries=6)
    key = settings.google_api_key
    if settings.llm_provider == "google_genai" and (key is None or not key.get_secret_value()):
        pytest.skip("The evals need the real model: set GOOGLE_API_KEY in .env")
    return settings


@pytest.fixture(scope="session")
def report(eval_settings: Settings, request: pytest.FixtureRequest) -> Iterator[Report]:
    selected = sum(1 for item in request.session.items if item.get_closest_marker("eval"))
    current = Report(
        provider=eval_settings.llm_provider,
        model=eval_settings.llm_model,
        started_at=datetime.now(UTC),
        selected=selected,
        total=len(load_scenarios()),
    )
    yield current
    if current.results:
        path = current.write()
        print(f"\nEval report: {path}")


@pytest.fixture
def pace() -> None:
    """Optional pause between scenarios, for a provider with a low per-minute limit."""
    time.sleep(float(os.environ.get("EVAL_PAUSE_SECONDS", "0")))
