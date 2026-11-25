from collections.abc import Callable, Sequence

import pytest
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from stockpilot.domain.errors import ExternalServiceError
from stockpilot.domain.geo import Coordinates, Location, Place
from stockpilot.models import Dealer
from stockpilot.services.dealer_locations import DealerLocationService, LocationSyncReport

pytestmark = pytest.mark.integration

CORDOBA = Location("Córdoba", "Córdoba", Coordinates(-31.4168, -64.1836))
ROSARIO = Location("Santa Fe", "Rosario", Coordinates(-32.9472, -60.6332))


class FakeGeocoder:
    def __init__(
        self,
        matches: dict[str, tuple[Location, ...]],
        *,
        meanwhile: Callable[[], None] = lambda: None,
    ) -> None:
        self._matches = matches
        # Runs while the API "answers", like another request would.
        self._meanwhile = meanwhile
        self.asked: list[Place] = []

    def locate(self, places: Sequence[Place]) -> list[tuple[Location, ...]]:
        self.asked.extend(places)
        self._meanwhile()
        return [self._matches.get(place.city, ()) for place in places]


def _dealer(
    session: Session, code: str, province: str, city: str, coordinates: Coordinates | None = None
) -> Dealer:
    dealer = Dealer(code=code, name=code, province=province, city=city, yard_capacity=5)
    if coordinates is not None:
        dealer.latitude, dealer.longitude = coordinates.latitude, coordinates.longitude
    session.add(dealer)
    session.flush()
    return dealer


def _saved(session: Session, dealer: Dealer) -> tuple[str, str, float | None, float | None]:
    session.refresh(dealer)
    return dealer.province, dealer.city, dealer.latitude, dealer.longitude


def test_saves_coordinates_and_official_names(db_session: Session) -> None:
    dealer = _dealer(db_session, "D001", "CORDOBA", "cordoba")

    report = DealerLocationService(db_session, FakeGeocoder({"cordoba": (CORDOBA,)})).sync()

    assert report == LocationSyncReport(located=["D001"])
    assert _saved(db_session, dealer) == ("Córdoba", "Córdoba", -31.4168, -64.1836)


@pytest.mark.parametrize(
    "matches",
    [pytest.param((), id="not-found"), pytest.param((CORDOBA, ROSARIO), id="ambiguous")],
)
def test_never_guesses_a_location(db_session: Session, matches: tuple[Location, ...]) -> None:
    unresolved = _dealer(db_session, "D001", "Santa Fe", "X")
    located = _dealer(db_session, "D002", "Santa Fe", "Rosario")
    geocoder = FakeGeocoder({"X": matches, "Rosario": (ROSARIO,)})

    with capture_logs() as logs:
        report = DealerLocationService(db_session, geocoder).sync()

    assert report == LocationSyncReport(located=["D002"], unresolved=["D001"])
    assert _saved(db_session, unresolved) == ("Santa Fe", "X", None, None)
    assert _saved(db_session, located) == ("Santa Fe", "Rosario", -32.9472, -60.6332)
    assert [(e["event"], e["dealer"], e["matches"]) for e in logs] == [
        ("dealer_not_located", "D001", len(matches))
    ]


def test_only_asks_for_dealers_without_coordinates(db_session: Session) -> None:
    _dealer(db_session, "D001", "Córdoba", "Córdoba", CORDOBA.coordinates)
    half = _dealer(db_session, "D002", "Santa Fe", "Rosario", CORDOBA.coordinates)
    half.longitude = None
    db_session.flush()
    geocoder = FakeGeocoder({"Rosario": (ROSARIO,)})

    DealerLocationService(db_session, geocoder).sync()

    assert geocoder.asked == [Place("Santa Fe", "Rosario")]
    assert _saved(db_session, half) == ("Santa Fe", "Rosario", -32.9472, -60.6332)


def test_does_not_call_the_api_when_nothing_is_pending(db_session: Session) -> None:
    def fail() -> None:
        raise AssertionError("the API was called")

    report = DealerLocationService(db_session, FakeGeocoder({}, meanwhile=fail)).sync()

    assert report == LocationSyncReport()


def test_no_transaction_is_open_while_the_api_answers(db_session: Session) -> None:
    _dealer(db_session, "D001", "Córdoba", "Córdoba")
    open_transactions: list[bool] = []

    def check() -> None:
        open_transactions.append(db_session.in_transaction())

    DealerLocationService(db_session, FakeGeocoder({}, meanwhile=check)).sync()

    assert open_transactions == [False]


def test_api_failure_changes_nothing(db_session: Session) -> None:
    dealer = _dealer(db_session, "D001", "Córdoba", "Córdoba")

    def fail() -> None:
        raise ExternalServiceError("Georef", "timeout")

    with pytest.raises(ExternalServiceError):
        DealerLocationService(db_session, FakeGeocoder({}, meanwhile=fail)).sync()

    assert _saved(db_session, dealer) == ("Córdoba", "Córdoba", None, None)


def test_dealer_that_moved_meanwhile_is_left_for_the_next_sync(db_session: Session) -> None:
    dealer = _dealer(db_session, "D001", "Córdoba", "Córdoba")

    def move() -> None:
        dealer.province, dealer.city = "Santa Fe", "Rosario"
        db_session.flush()

    geocoder = FakeGeocoder({"Córdoba": (CORDOBA,)}, meanwhile=move)
    report = DealerLocationService(db_session, geocoder).sync()

    assert report == LocationSyncReport()
    assert _saved(db_session, dealer) == ("Santa Fe", "Rosario", None, None)


def test_coordinates_set_by_a_person_meanwhile_win(db_session: Session) -> None:
    dealer = _dealer(db_session, "D001", "Córdoba", "Córdoba")

    def locate() -> None:
        dealer.latitude, dealer.longitude = -31.5, -64.5
        db_session.flush()

    geocoder = FakeGeocoder({"Córdoba": (CORDOBA,)}, meanwhile=locate)
    report = DealerLocationService(db_session, geocoder).sync()

    assert report == LocationSyncReport()
    assert _saved(db_session, dealer) == ("Córdoba", "Córdoba", -31.5, -64.5)


def test_dealer_deleted_meanwhile_is_skipped(db_session: Session) -> None:
    dealer = _dealer(db_session, "D001", "Córdoba", "Córdoba")

    def remove() -> None:
        db_session.delete(dealer)
        db_session.flush()

    geocoder = FakeGeocoder({"Córdoba": (CORDOBA,)}, meanwhile=remove)

    assert DealerLocationService(db_session, geocoder).sync() == LocationSyncReport()
