from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

_EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Place:
    """A location as a person wrote it."""

    province: str
    city: str


@dataclass(frozen=True)
class Location:
    """A location with its official names."""

    province: str
    city: str
    coordinates: Coordinates


def distance_km(a: Coordinates, b: Coordinates) -> float:
    """Great-circle distance (haversine formula). Roads are always longer."""
    lat_a, lat_b = radians(a.latitude), radians(b.latitude)
    half_lat = (lat_b - lat_a) / 2
    half_lon = radians(b.longitude - a.longitude) / 2
    h = sin(half_lat) ** 2 + cos(lat_a) * cos(lat_b) * sin(half_lon) ** 2
    return 2 * _EARTH_RADIUS_KM * asin(sqrt(h))
