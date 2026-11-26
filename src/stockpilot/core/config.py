"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, NonNegativeFloat, NonNegativeInt, PositiveFloat, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from stockpilot.domain.coverage import CoveragePolicy
from stockpilot.domain.geo import Coordinates


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

    # AI model of the agent. Gemini by default; Ollama runs locally without a key.
    llm_provider: Literal["google_genai", "ollama"] = "google_genai"
    llm_model: str = "gemini-3.5-flash-lite"
    # Only from .env or the environment. SecretStr keeps it out of logs and reprs.
    google_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"
    # 0: the same question gets the same answer as often as possible; evals rely on it.
    llm_temperature: float = Field(default=0, ge=0, le=2)
    llm_timeout_seconds: PositiveFloat = 60
    # Retries of the call itself (network, rate limits), not of a rejected proposal.
    llm_max_retries: NonNegativeInt = 2
    # Model calls per agent run: caps its cost and the free tier quota it uses.
    agent_max_model_calls: int = Field(default=10, ge=2, le=50)

    # Built here once, so the API, the agent and the jobs all use the same thresholds.
    def coverage_policy(self) -> CoveragePolicy:
        return CoveragePolicy(
            sales_window_days=self.sales_window_days,
            low_coverage_days=self.low_coverage_days,
            aged_after_days=self.aged_after_days,
        )

    def distribution_center(self) -> Coordinates:
        return Coordinates(self.distribution_center_latitude, self.distribution_center_longitude)


@lru_cache
def get_settings() -> Settings:
    return Settings()
