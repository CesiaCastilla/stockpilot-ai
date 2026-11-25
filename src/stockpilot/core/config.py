"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache

from pydantic import PositiveFloat, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "StockPilot"
    environment: str = "local"
    # Local default matches docker-compose.yml; any real deployment overrides it.
    database_url: str = "postgresql+psycopg://stockpilot:stockpilot@localhost:5432/stockpilot"
    # Seconds to wait for PostgreSQL before giving up, so /health never hangs.
    database_connect_timeout: int = 3

    # Coverage and aging thresholds, in days.
    sales_window_days: PositiveInt = 60
    low_coverage_days: PositiveInt = 15
    aged_after_days: PositiveInt = 90

    # External APIs. Timeouts apply to each attempt.
    http_connect_timeout_seconds: PositiveFloat = 2
    http_timeout_seconds: PositiveFloat = 5
    http_max_attempts: PositiveInt = 3
    dolar_api_url: str = "https://dolarapi.com"
    exchange_rate_ttl_seconds: PositiveInt = 600
    argentina_datos_url: str = "https://api.argentinadatos.com"
    # A day, not forever: holidays are added or moved during the year.
    holidays_ttl_seconds: PositiveInt = 86_400


@lru_cache
def get_settings() -> Settings:
    return Settings()
