"""Vehicle Identification Number (VIN) rules, per ISO 3779.

The check digit (position 9) is only mandatory for vehicles made for North
America (VINs starting with 1-5). Vehicles made in Argentina (8A-8E) and most
other markets may use any character there, so validating it for every VIN would
reject real Argentine units.
"""

from datetime import date

from stockpilot.domain.errors import InvalidVinError

VIN_LENGTH = 17
VIN_ALPHABET = frozenset("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")  # never I, O or Q
NORTH_AMERICA_PREFIXES = frozenset("12345")

_VALUES = {
    **{str(digit): digit for digit in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
    **dict(zip("JKLMN", range(1, 6), strict=True)),
    "P": 7,
    "R": 9,
    **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def check_digit(vin: str) -> str:
    """Expected check digit of a 17-character VIN made of valid characters."""
    total = sum(_VALUES[char] * weight for char, weight in zip(vin, _WEIGHTS, strict=True))
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def parse_vin(raw: str) -> str:
    """Normalize a VIN (trim, uppercase) and validate it. Raises InvalidVinError."""
    vin = raw.strip().upper()
    if len(vin) != VIN_LENGTH:
        raise InvalidVinError(raw, f"must have {VIN_LENGTH} characters, got {len(vin)}")
    invalid = sorted(set(vin) - VIN_ALPHABET)
    if invalid:
        raise InvalidVinError(raw, f"invalid characters: {', '.join(invalid)}")
    if vin[0] in NORTH_AMERICA_PREFIXES and vin[8] != check_digit(vin):
        raise InvalidVinError(raw, f"check digit should be {check_digit(vin)!r}, got {vin[8]!r}")
    return vin


# Position 10: A is 2010 and so on, without I, O, Q, U, Z or 0. It repeats every 30 years.
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"
_FIRST_CODED_YEAR = 2010
# New vehicles: from a few years in stock to next year's model.
_OLDEST_MODEL_YEARS = 3


def model_year(vin: str, *, today: date) -> int | None:
    """Model year of a new vehicle, from position 10 of a valid VIN.

    Only North America requires the year there. Elsewhere it is common but
    optional, so a year that no new vehicle can have is taken as "not a year".
    """
    position = _YEAR_CODES.find(vin[9])  # -1, if it is not a year code, matches no year
    for year in range(today.year - _OLDEST_MODEL_YEARS, today.year + 2):
        if (year - _FIRST_CODED_YEAR) % len(_YEAR_CODES) == position:
            return year
    return None
