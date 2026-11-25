import pytest

from stockpilot.domain.geo import Coordinates, distance_km

BUENOS_AIRES = Coordinates(-34.6084, -58.3721)
CORDOBA = Coordinates(-31.4168, -64.1836)


def test_distance_between_cities() -> None:
    # About 646 km in a straight line, 700 by road.
    assert distance_km(BUENOS_AIRES, CORDOBA) == pytest.approx(646, abs=2)


def test_distance_is_symmetric_and_zero_to_itself() -> None:
    assert distance_km(BUENOS_AIRES, CORDOBA) == pytest.approx(distance_km(CORDOBA, BUENOS_AIRES))
    assert distance_km(CORDOBA, CORDOBA) == 0


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # A quarter and a half of the Earth: 2 * pi * 6371 / 4 and / 2.
        pytest.param(Coordinates(0, 0), Coordinates(0, 90), 10007.5, id="along-the-equator"),
        pytest.param(Coordinates(0, 0), Coordinates(-90, 0), 10007.5, id="to-the-pole"),
        pytest.param(Coordinates(0, 0), Coordinates(0, 180), 20015.1, id="opposite-side"),
        pytest.param(Coordinates(0, 179), Coordinates(0, -179), 222.4, id="across-the-date-line"),
        # At 60 degrees a degree of longitude is half as long.
        pytest.param(Coordinates(-60, 0), Coordinates(-60, 1), 55.6, id="far-from-the-equator"),
    ],
)
def test_known_distances(a: Coordinates, b: Coordinates, expected: float) -> None:
    assert distance_km(a, b) == pytest.approx(expected, abs=0.1)
