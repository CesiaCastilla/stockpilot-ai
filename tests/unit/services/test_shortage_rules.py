import pytest

from stockpilot.schemas.coverage import Shortage
from stockpilot.services.coverage import can_be_served


def _shortage(units_needed: int, free_places: int) -> Shortage:
    return Shortage(
        dealer_id=1,
        dealer_code="D001",
        vehicle_model_id=1,
        vehicle_model_name="Aconcagua",
        trim_id=1,
        trim_name="SR",
        in_yard=0,
        in_transit=0,
        recent_sales=6,
        coverage_days=0,
        pending_arrivals=0,
        units_needed=units_needed,
        free_places=free_places,
    )


@pytest.mark.parametrize(
    ("units_needed", "free_places", "expected"),
    [
        (2, 5, True),
        (1, 1, True),
        # Pending orders already bring what it needs.
        (0, 5, False),
        # Its yard has no room for another unit.
        (2, 0, False),
        (0, 0, False),
    ],
)
def test_a_transfer_can_fix_a_shortage_only_if_units_are_needed_and_fit(
    units_needed: int, free_places: int, expected: bool
) -> None:
    assert can_be_served(_shortage(units_needed, free_places)) is expected
