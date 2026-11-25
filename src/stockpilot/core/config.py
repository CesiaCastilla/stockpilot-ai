"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, NonNegativeFloat, PositiveFloat, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "StockPilot"
    environment: str = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # "console" is easier to read during development.
    log_format: Literal["json", "console"] = "json"
    # Local default matches docker-compose.yml; any real deployment overrides it.
    database_url: str = "postgresql+psycopg://stockpilot:stockpilot@localhost:5432/stockpilot"
    # Seconds to wait for PostgreSQL before giving up, so /health never hangs.
    database_connect_timeout: int = 3

    # Coverage and aging thresholds, in days.
    sales_window_days: PositiveInt = 60
    low_coverage_days: PositiveInt = 15
    aged_after_days: PositiveInt = 90

    # Where units without a dealer are. The network is fictitious, and so is this point.
    distribution_center_latitude: float = Field(default=-33.4, ge=-90, le=90)
    distribution_center_longitude: float = Field(default=-61.5, ge=-180, le=180)

    # External APIs. Timeouts apply to each attempt.
    http_connect_timeout_seconds: PositiveFloat = 2
    http_timeout_seconds: PositiveFloat = 5
    http_max_attempts: PositiveInt = 3
    # After an API fails every attempt, how long to fail fast instead of asking again.
    http_failure_cooldown_seconds: NonNegativeFloat = 60
    dolar_api_url: str = "https://dolarapi.com"
    exchange_rate_ttl_seconds: PositiveInt = 600
    argentina_datos_url: str = "https://api.argentinadatos.com"
    # A day, not forever: holidays are added or moved during the year.
    holidays_ttl_seconds: PositiveInt = 86_400
    vpic_url: str = "https://vpic.nhtsa.dot.gov/api/vehicles"
    vin_ttl_seconds: PositiveInt = 86_400
    # v1.0 on purpose: v2.0 has no single Buenos Aires city, only its 15 districts.
    georef_url: str = "https://apis.datos.gob.ar/georef/api/v1.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
