from itertools import product

import pytest

from stockpilot.domain.errors import InvalidStatusTransitionError
from stockpilot.domain.unit_status import ALLOWED_TRANSITIONS, UnitStatus, ensure_transition

S = UnitStatus

# Written out by hand on purpose: the test is the spec, not a copy of the code.
EXPECTED_ALLOWED = {
    (S.IN_TRANSIT, S.IN_YARD),
    (S.IN_YARD, S.RESERVED),
    (S.IN_YARD, S.SOLD),
    (S.IN_YARD, S.IN_TRANSIT),
    (S.RESERVED, S.IN_YARD),
    (S.RESERVED, S.SOLD),
    (S.SOLD, S.DELIVERED),
}
ALL_PAIRS = set(product(UnitStatus, repeat=2))


def _id(pair: tuple[UnitStatus, UnitStatus]) -> str:
    return f"{pair[0]}->{pair[1]}"


def test_every_status_has_a_transition_rule() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(UnitStatus)


@pytest.mark.parametrize("pair", sorted(EXPECTED_ALLOWED), ids=_id)
def test_allowed_transitions(pair: tuple[UnitStatus, UnitStatus]) -> None:
    ensure_transition(*pair)


@pytest.mark.parametrize("pair", sorted(ALL_PAIRS - EXPECTED_ALLOWED), ids=_id)
def test_every_other_transition_is_rejected(pair: tuple[UnitStatus, UnitStatus]) -> None:
    current, target = pair

    with pytest.raises(InvalidStatusTransitionError) as error:
        ensure_transition(current, target)

    assert (error.value.current, error.value.target) == (current.value, target.value)


def test_reserved_unit_cannot_be_transferred() -> None:
    # Business rule the agent's validator relies on (Phase 4).
    with pytest.raises(InvalidStatusTransitionError):
        ensure_transition(S.RESERVED, S.IN_TRANSIT)
