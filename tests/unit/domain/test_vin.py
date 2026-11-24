import pytest

from stockpilot.domain.errors import InvalidVinError
from stockpilot.domain.vin import check_digit, parse_vin

# Standard worked example of the check digit algorithm: position 9 is "X".
NORTH_AMERICAN_VIN = "1M8GDM9AXKP042788"


def test_check_digit_matches_the_standard_example() -> None:
    assert check_digit(NORTH_AMERICAN_VIN) == "X"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(NORTH_AMERICAN_VIN, NORTH_AMERICAN_VIN, id="north-american-valid-check-digit"),
        pytest.param("8AFZZZ12345678901", "8AFZZZ12345678901", id="argentine-any-position-9"),
        pytest.param("  8afzzz12345678901 ", "8AFZZZ12345678901", id="trimmed-and-uppercased"),
    ],
)
def test_valid_vins_are_normalized(raw: str, expected: str) -> None:
    assert parse_vin(raw) == expected


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        pytest.param("8AFZZZ1234567890", "must have 17 characters, got 16", id="too-short"),
        pytest.param("8AFZZZ123456789012", "must have 17 characters, got 18", id="too-long"),
        pytest.param("", "must have 17 characters, got 0", id="empty"),
        pytest.param("8AFZZZ1234567890I", "invalid characters: I", id="letter-i"),
        pytest.param("8AFZZZ12O45678Q01", "invalid characters: O, Q", id="letters-o-and-q"),
        pytest.param("8AFZZZ-2345678901", "invalid characters: -", id="symbol"),
        pytest.param(
            "1M8GDM9A0KP042788", "check digit should be 'X', got '0'", id="wrong-check-digit"
        ),
    ],
)
def test_invalid_vins_are_rejected_with_a_reason(raw: str, reason: str) -> None:
    with pytest.raises(InvalidVinError) as error:
        parse_vin(raw)

    assert error.value.reason == reason
    assert error.value.vin == raw
