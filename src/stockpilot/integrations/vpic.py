"""NHTSA vPIC (https://vpic.nhtsa.dot.gov/api): VIN decoding, mostly for the US market."""

from typing import Annotated

import httpx
from pydantic import BaseModel, BeforeValidator, Field, TypeAdapter

from stockpilot.core.config import Settings
from stockpilot.domain.vin_info import VehicleInfo
from stockpilot.integrations.cache import TtlCache
from stockpilot.integrations.http import JsonApi

# vPIC sends every field, with "" for the ones it does not know. They have no
# default on purpose: a field that disappears is a contract change, not "unknown".
_EmptyIsNone = BeforeValidator(lambda value: None if value == "" else value)


class _Vehicle(BaseModel):
    make: Annotated[str | None, _EmptyIsNone, Field(validation_alias="Make")]
    model: Annotated[str | None, _EmptyIsNone, Field(validation_alias="Model")]
    year: Annotated[int | None, _EmptyIsNone, Field(validation_alias="ModelYear")]


class _Response(BaseModel):
    results: Annotated[
        list[_Vehicle], Field(validation_alias="Results", min_length=1, max_length=1)
    ]


_RESPONSE = TypeAdapter(_Response)


class VpicClient:
    def __init__(self, api: JsonApi, cache: TtlCache[str, VehicleInfo]) -> None:
        self._api = api
        self._cache = cache

    def decode(self, vin: str) -> VehicleInfo:
        """What vPIC knows about a valid VIN, maybe nothing. Raises ExternalServiceError.

        Its error codes are not read: a wrong check digit, for example, is
        normal outside North America and still decodes.
        """
        return self._cache.get_or_load(vin, lambda: self._fetch(vin))

    def _fetch(self, vin: str) -> VehicleInfo:
        response = self._api.get(f"/DecodeVinValues/{vin}", _RESPONSE, {"format": "json"})
        vehicle = response.results[0]
        return VehicleInfo(make=vehicle.make, model=vehicle.model, year=vehicle.year)


def build_vpic(settings: Settings, http: httpx.Client) -> VpicClient:
    api = JsonApi(
        http,
        service="vPIC",
        base_url=settings.vpic_url,
        max_attempts=settings.http_max_attempts,
    )
    return VpicClient(api, TtlCache(settings.vin_ttl_seconds))
