import pytest
from sqlalchemy.orm import Session

from stockpilot.domain.errors import ConflictError, NotFoundError
from stockpilot.domain.geo import Coordinates
from stockpilot.models import Dealer
from stockpilot.services.distance import DistanceService

pytestmark = pytest.mark.integration

CORDOBA = Coordinates(-31.4168, -64.1836)
ROSARIO = Coordinates(-32.9472, -60.6332)
CENTER = Coordinates(-33.4, -61.5)


def _dealer(session: Session, code: str, coordinates: Coordinates) -> Dealer:
    dealer = Dealer(
        code=code,
        name=code,
        province="X",
        city="X",
        yard_capacity=5,
        latitude=coordinates.latitude,
        longitude=coordinates.longitude,
    )
    session.add(dealer)
    session.flush()
    return dealer


@pytest.fixture
def cordoba(db_session: Session) -> Dealer:
    return _dealer(db_session, "D001", CORDOBA)


@pytest.fixture
def rosario(db_session: Session) -> Dealer:
    return _dealer(db_session, "D002", ROSARIO)


@pytest.fixture
def service(db_session: Session) -> DistanceService:
    return DistanceService(db_session, CENTER)


def test_distance_between_dealers(
    service: DistanceService, cordoba: Dealer, rosario: Dealer
) -> None:
    distance = service.between_km(cordoba.id, rosario.id)

    assert distance == pytest.approx(374, abs=2)
    assert distance == round(distance, 1)
    assert service.between_km(cordoba.id, cordoba.id) == 0


def test_no_dealer_is_the_distribution_center(service: DistanceService, rosario: Dealer) -> None:
    distance = service.between_km(None, rosario.id)

    assert distance == pytest.approx(95, abs=2)
    assert service.between_km(rosario.id, None) == distance
    assert service.between_km(None, None) == 0


@pytest.mark.parametrize("missing", ["latitude", "longitude"])
def test_distance_needs_both_coordinates(
    db_session: Session, service: DistanceService, rosario: Dealer, missing: str
) -> None:
    setattr(rosario, missing, None)
    db_session.flush()

    with pytest.raises(ConflictError, match="Dealer D002 has no coordinates yet"):
        service.between_km(None, rosario.id)


def test_distance_to_an_unknown_dealer(service: DistanceService) -> None:
    with pytest.raises(NotFoundError):
        service.between_km(None, 999999)
