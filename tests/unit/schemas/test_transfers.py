from typing import Any

import pytest
from pydantic import ValidationError

from stockpilot.schemas.transfers import TransferProposal

VALID: dict[str, Any] = {
    "vin": "8AFZZZ12345670001",
    "destination_dealer_id": 2,
    "reason": "Shortage",
}


def test_text_is_normalized() -> None:
    proposal = TransferProposal.model_validate(
        {**VALID, "vin": " 8afzzz12345670001 ", "reason": " Shortage "}
    )

    assert (proposal.vin, proposal.reason) == ("8AFZZZ12345670001", "Shortage")


def test_reason_can_have_500_characters() -> None:
    assert len(TransferProposal.model_validate({**VALID, "reason": "x" * 500}).reason) == 500


@pytest.mark.parametrize(
    "change",
    [
        {"reason": "   "},
        {"reason": "x" * 501},
        {"reason": "bad\x00text"},
        {"vin": "8AFZZZ\x0012345670001"},
        {"destination_dealer_id": "Córdoba"},
        {"destination_dealer_id": 0},
        {"destination_dealer_id": 2**31},
        {"origin_dealer_id": 1},
    ],
    ids=[
        "blank",
        "too-long",
        "nul-reason",
        "nul-vin",
        "not-an-id",
        "zero-id",
        "huge-id",
        "unknown-field",
    ],
)
def test_malformed_proposal_is_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TransferProposal.model_validate({**VALID, **change})


@pytest.mark.parametrize("field", sorted(VALID))
def test_every_field_is_required(field: str) -> None:
    with pytest.raises(ValidationError):
        TransferProposal.model_validate({k: v for k, v in VALID.items() if k != field})
