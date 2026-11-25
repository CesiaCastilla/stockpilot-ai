"""ArgentinaDatos (https://argentinadatos.com): national holidays of Argentina."""

from datetime import date
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from stockpilot.core.config import Settings
from stockpilot.domain.delivery import Holiday
from stockpilot.integrations.cache import TtlCache
from stockpilot.integrations.http import JsonApi, build_json_api


class _Holiday(BaseModel):
    """The type (fixed, movable or bridge day) is not read: none is a working day."""

    day: date = Field(validation_alias="fecha")
    name: str = Field(validation_alias="nombre")


# Every year has holidays: an empty list would silently mean "none at all".
_HOLIDAYS = TypeAdapter(Annotated[list[_Holiday], Field(min_length=1)])


class ArgentinaDatosClient:
    def __init__(self, api: JsonApi, cache: TtlCache[int, tuple[Holiday, ...]]) -> None:
        self._api = api
        self._cache = cache

    def holidays(self, year: int) -> tuple[Holiday, ...]:
        """Non-working days of a year. Raises ExternalServiceError.

        Also raised for a year that is not published yet (the API answers 404).
        """
        return self._cache.get_or_load(year, lambda: self._fetch(year))

    def _fetch(self, year: int) -> tuple[Holiday, ...]:
        response = self._api.get(f"/v1/feriados/{year}", _HOLIDAYS)
        return tuple(Holiday(day=h.day, name=h.name) for h in response)


def build_argentina_datos(settings: Settings, http: httpx.Client) -> ArgentinaDatosClient:
    api = build_json_api(
        settings, http, service="ArgentinaDatos", base_url=settings.argentina_datos_url
    )
    return ArgentinaDatosClient(api, TtlCache(settings.holidays_ttl_seconds))
