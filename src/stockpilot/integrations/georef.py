"""Georef (https://apis.datos.gob.ar/georef): official names and coordinates in Argentina."""

from collections.abc import Sequence
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from stockpilot.core.config import Settings
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.domain.geo import Coordinates, Location, Place
from stockpilot.integrations.http import JsonApi

SERVICE = "Georef"

# One is a match, two are enough to know that the name is ambiguous.
_MAX_MATCHES = 2


class _Province(BaseModel):
    name: str = Field(validation_alias="nombre")


class _Centroid(BaseModel):
    latitude: Annotated[float, Field(validation_alias="lat", ge=-90, le=90)]
    longitude: Annotated[float, Field(validation_alias="lon", ge=-180, le=180)]


class _City(BaseModel):
    name: str = Field(validation_alias="nombre")
    province: _Province = Field(validation_alias="provincia")
    centroid: _Centroid = Field(validation_alias="centroide")


class _Result(BaseModel):
    cities: list[_City] = Field(validation_alias="localidades_censales")


class _Response(BaseModel):
    results: list[_Result] = Field(validation_alias="resultados")


_RESPONSE = TypeAdapter(_Response)


class GeorefClient:
    """No cache: the answers are stored in the dealers table, the rest are asked again."""

    def __init__(self, api: JsonApi) -> None:
        self._api = api

    def locate(self, places: Sequence[Place]) -> list[tuple[Location, ...]]:
        """The matches of each place, in the same order. Raises ExternalServiceError.

        Exact names only, ignoring case and accents: the fuzzy search answers
        "Pila" for "Pilar". One request for all the places, because Georef
        limits how many requests per minute it accepts.
        """
        queries = [
            {
                "nombre": place.city,
                "provincia": place.province,
                "exacto": True,
                "max": _MAX_MATCHES,
                "campos": "nombre,provincia.nombre,centroide",
            }
            for place in places
        ]
        # "Localidades censales" and not "localidades": the latter has no Buenos Aires city.
        response = self._api.post(
            "/localidades-censales", _RESPONSE, {"localidades_censales": queries}
        )
        if len(response.results) != len(places):
            # The answers are matched by position: a missing one would shift the rest.
            raise ExternalServiceError(SERVICE, "unexpected response")
        return [tuple(_location(city) for city in result.cities) for result in response.results]


def _location(city: _City) -> Location:
    return Location(
        province=city.province.name,
        city=city.name,
        coordinates=Coordinates(city.centroid.latitude, city.centroid.longitude),
    )


def build_georef(settings: Settings, http: httpx.Client) -> GeorefClient:
    api = JsonApi(
        http,
        service=SERVICE,
        base_url=settings.georef_url,
        max_attempts=settings.http_max_attempts,
    )
    return GeorefClient(api)
