from datetime import date
from typing import Protocol

import structlog
from sqlalchemy.orm import Session

from stockpilot.domain.errors import ExternalServiceError
from stockpilot.domain.vin import model_year, parse_vin
from stockpilot.domain.vin_info import DecodedVin, VehicleInfo, VinSource
from stockpilot.repositories.units import UnitRepository

logger = structlog.get_logger(__name__)


class VinDecoder(Protocol):
    def decode(self, vin: str) -> VehicleInfo:
        """What the source knows about a valid VIN. Raises ExternalServiceError."""
        ...


class VinService:
    def __init__(self, session: Session, decoder: VinDecoder) -> None:
        self.units = UnitRepository(session)
        self._decoder = decoder

    def decode(self, raw_vin: str, *, today: date) -> DecodedVin:
        """Make, model and year of a VIN. Raises InvalidVinError.

        vPIC first; what it does not know is completed with our own data. It
        barely covers vehicles made in Argentina, so that is the usual path.
        """
        vin = parse_vin(raw_vin)  # before any request: an invented VIN stops here

        vpic, checked = VehicleInfo(), True
        try:
            vpic = self._decoder.decode(vin)
        except ExternalServiceError as error:
            logger.warning("vin_without_vpic", vin=vin, error=str(error))
            checked = False

        local = self._local_info(vin, today)
        # Field by field: vPIC can know the make and the year but not the model.
        info = VehicleInfo(
            make=vpic.make,  # the catalog has no makes: the network sells only one
            model=vpic.model if vpic.model is not None else local.model,
            year=vpic.year if vpic.year is not None else local.year,
        )
        return DecodedVin(vin=vin, info=info, source=_source(info, vpic), vpic_checked=checked)

    def _local_info(self, vin: str, today: date) -> VehicleInfo:
        unit = self.units.get_by_vin(vin)
        if unit is None:
            # Not one of ours: its year code may not even be a year.
            return VehicleInfo()
        return VehicleInfo(model=unit.trim.vehicle_model.name, year=model_year(vin, today=today))


def _source(info: VehicleInfo, vpic: VehicleInfo) -> VinSource:
    if info == VehicleInfo():
        return VinSource.NONE
    if info == vpic:
        return VinSource.VPIC
    return VinSource.LOCAL if vpic == VehicleInfo() else VinSource.COMBINED
