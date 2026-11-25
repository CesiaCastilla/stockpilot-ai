"""Domain errors become the right HTTP status, without a database."""

import pytest
from fastapi.testclient import TestClient

from stockpilot.api.dependencies import get_dealer_service
from stockpilot.api.errors import status_for
from stockpilot.core.config import Settings
from stockpilot.domain.errors import (
    AlreadyExistsError,
    ConflictError,
    DomainError,
    ExternalServiceError,
    InvalidStatusTransitionError,
    InvalidVinError,
    NotFoundError,
)
from stockpilot.main import create_app


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("Dealer", 1), 404),
        (AlreadyExistsError("Dealer", "code", "D001"), 409),
        (ConflictError("busy"), 409),
        (InvalidStatusTransitionError("sold", "in_yard"), 409),
        (InvalidVinError("X", "too short"), 422),
        (ExternalServiceError("DolarApi", "timeout"), 502),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_each_domain_error_has_its_status(error: DomainError, status: int) -> None:
    assert status_for(error) == status


def test_unmapped_domain_error_is_a_server_error() -> None:
    class NewRuleBroken(DomainError):
        pass

    assert status_for(NewRuleBroken("forgot to map it")) == 500


class _FailingDealerService:
    def get(self, dealer_id: int) -> None:
        raise NotFoundError("Dealer", dealer_id)


def test_routes_return_domain_errors_as_json() -> None:
    # The service is replaced through dependency injection: no database involved.
    app = create_app(Settings())
    app.dependency_overrides[get_dealer_service] = _FailingDealerService

    with TestClient(app) as client:
        response = client.get("/api/v1/dealers/7")

    assert response.status_code == 404
    assert response.json() == {"detail": "Dealer 7 not found"}
