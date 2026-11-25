from dataclasses import dataclass
from enum import StrEnum


class VinSource(StrEnum):
    VPIC = "vpic"
    # Our catalog and the year code of the VIN itself.
    LOCAL = "local"
    COMBINED = "combined"
    NONE = "none"


@dataclass(frozen=True)
class VehicleInfo:
    """What a source knows about a VIN. None: that source has no data."""

    make: str | None = None
    model: str | None = None
    year: int | None = None


@dataclass(frozen=True)
class DecodedVin:
    vin: str
    info: VehicleInfo
    source: VinSource
    # False: vPIC did not answer, so it may know more than info says.
    vpic_checked: bool
