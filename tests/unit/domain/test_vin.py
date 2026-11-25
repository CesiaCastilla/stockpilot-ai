from datetime import date

import pytest

from stockpilot.domain.errors import InvalidVinError
from stockpilot.domain.vin import check_digit, model_year, parse_vin

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


def _vin_with_year_code(code: str) -> str:
    return f"8ZZ84G7Y4{code}A000001"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        pytest.param("P", 2023, id="oldest-accepted"),
        pytest.param("R", 2024, id="2024"),
        pytest.param("S", 2025, id="2025"),
        pytest.param("T", 2026, id="this-year"),
        pytest.param("V", 2027, id="next-year-model"),
        pytest.param("W", None, id="too-far-ahead"),
        pytest.param("N", None, id="too-old-to-be-new"),
        pytest.param("4", None, id="2004-or-2034"),
        pytest.param("U", None, id="never-a-year-code"),
        pytest.param("0", None, id="zero-is-never-a-year-code"),
    ],
)
def test_model_year_of_a_new_vehicle(code: str, expected: int | None) -> None:
    assert model_year(_vin_with_year_code(code), today=date(2026, 11, 25)) == expected


def test_year_codes_repeat_every_30_years() -> None:
    assert model_year(_vin_with_year_code("9"), today=date(2039, 6, 1)) == 2039
    assert model_year(_vin_with_year_code("A"), today=date(2039, 6, 1)) == 2040
