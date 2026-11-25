from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from stockpilot.domain.errors import ExternalServiceError, InvalidVinError
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.domain.vin_info import DecodedVin, VehicleInfo, VinSource
from stockpilot.models import Trim, Unit, VehicleModel
from stockpilot.services.vin import VinService

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 25)
VIN = "8ZZ84G7Y4TA000001"  # T: 2026
FULL = VehicleInfo("ACME", "Roadster", 2025)


class FakeDecoder:
    def __init__(self, info: VehicleInfo | None) -> None:
        self._info = info
        self.asked: list[str] = []

    def decode(self, vin: str) -> VehicleInfo:
        self.asked.append(vin)
        if self._info is None:
            raise ExternalServiceError("vPIC", "timeout")
        return self._info


@pytest.fixture
def unit(db_session: Session) -> Unit:
    unit = Unit(
        vin=VIN,
        trim=Trim(vehicle_model=VehicleModel(name="Aconcagua"), name="Base"),
        color="White",
        status=UnitStatus.IN_YARD,
        received_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    db_session.add(unit)
    db_session.flush()
    return unit


@pytest.mark.parametrize(
    ("vpic", "expected", "source"),
    [
        pytest.param(FULL, FULL, VinSource.VPIC, id="vpic-knows-everything"),
        pytest.param(
            VehicleInfo("ACME", None, 2025),
            VehicleInfo("ACME", "Aconcagua", 2025),
            VinSource.COMBINED,
            id="model-from-the-catalog",
        ),
        pytest.param(
            VehicleInfo("ACME", "Roadster", None),
            VehicleInfo("ACME", "Roadster", 2026),
            VinSource.COMBINED,
            id="year-from-the-vin",
        ),
        pytest.param(
            VehicleInfo(),
            VehicleInfo(None, "Aconcagua", 2026),
            VinSource.LOCAL,
            id="vpic-knows-nothing",
        ),
    ],
)
def test_vpic_first_completed_with_local_data(
    db_session: Session, unit: Unit, vpic: VehicleInfo, expected: VehicleInfo, source: VinSource
) -> None:
    decoded = VinService(db_session, FakeDecoder(vpic)).decode(VIN, today=TODAY)

    assert decoded == DecodedVin(VIN, expected, source, vpic_checked=True)


def test_works_without_vpic_and_says_so(db_session: Session, unit: Unit) -> None:
    with capture_logs() as logs:
        decoded = VinService(db_session, FakeDecoder(None)).decode(VIN, today=TODAY)

    expected = VehicleInfo(None, "Aconcagua", 2026)
    assert decoded == DecodedVin(VIN, expected, VinSource.LOCAL, vpic_checked=False)
    assert [(e["event"], e["vin"]) for e in logs] == [("vin_without_vpic", VIN)]


@pytest.mark.parametrize(
    ("vpic", "source"), [(FULL, VinSource.VPIC), (VehicleInfo(), VinSource.NONE)]
)
def test_vin_that_is_not_ours_has_only_vpic_data(
    db_session: Session, vpic: VehicleInfo, source: VinSource
) -> None:
    decoded = VinService(db_session, FakeDecoder(vpic)).decode(VIN, today=TODAY)

    assert decoded == DecodedVin(VIN, vpic, source, vpic_checked=True)


def test_vin_is_normalized(db_session: Session, unit: Unit) -> None:
    decoder = FakeDecoder(VehicleInfo())

    decoded = VinService(db_session, decoder).decode(f" {VIN.lower()} ", today=TODAY)

    assert (decoded.vin, decoded.info.model) == (VIN, "Aconcagua")
    assert decoder.asked == [VIN]


def test_invalid_vin_never_reaches_vpic(db_session: Session) -> None:
    decoder = FakeDecoder(FULL)

    with pytest.raises(InvalidVinError):
        VinService(db_session, decoder).decode("8ZZ-INVENTED", today=TODAY)

    assert decoder.asked == []
