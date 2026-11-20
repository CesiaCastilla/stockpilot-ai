"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "StockPilot"
    environment: str = "local"
    # Local default matches docker-compose.yml; any real deployment overrides it.
    database_url: str = "postgresql+psycopg://stockpilot:stockpilot@localhost:5432/stockpilot"
    # Seconds to wait for PostgreSQL before giving up, so /health never hangs.
    database_connect_timeout: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
