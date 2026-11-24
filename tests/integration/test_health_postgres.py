import pytest
from fastapi.testclient import TestClient

from stockpilot.core.config import Settings
from stockpilot.main import create_app

pytestmark = pytest.mark.integration


def test_health_is_ok_against_real_postgres(postgres_url: str) -> None:
    with TestClient(create_app(Settings(database_url=postgres_url))) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
