"""DolarApi (https://dolarapi.com): US dollar quotes for Argentina."""

from decimal import Decimal
from typing import Annotated

import httpx
from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter

from stockpilot.core.config import Settings
from stockpilot.domain.exchange_rate import ExchangeRate
from stockpilot.integrations.cache import TtlCache
from stockpilot.integrations.http import JsonApi, build_json_api

Price = Annotated[Decimal, Field(gt=0)]


class _Quote(BaseModel):
    """Only the fields we use: new ones in the response don't break us."""

    buy: Price = Field(validation_alias="compra")
    sell: Price = Field(validation_alias="venta")
    updated_at: AwareDatetime = Field(validation_alias="fechaActualizacion")


_QUOTE = TypeAdapter(_Quote)


class DolarApiClient:
    def __init__(self, api: JsonApi, cache: TtlCache[str, ExchangeRate]) -> None:
        self._api = api
        self._cache = cache

    def official_rate(self) -> ExchangeRate:
        """The official rate. Raises ExternalServiceError."""
        return self._cache.get_or_load("oficial", self._fetch)

    def _fetch(self) -> ExchangeRate:
        quote = self._api.get("/v1/dolares/oficial", _QUOTE)
        return ExchangeRate(buy=quote.buy, sell=quote.sell, updated_at=quote.updated_at)


def build_dolar_api(settings: Settings, http: httpx.Client) -> DolarApiClient:
    api = build_json_api(settings, http, service="DolarApi", base_url=settings.dolar_api_url)
    return DolarApiClient(api, TtlCache(settings.exchange_rate_ttl_seconds))
