from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from stockpilot.core.config import Settings
from stockpilot.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    # Same major version as docker-compose.yml and Cloud SQL.
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


def test_health_is_ok_against_real_postgres(postgres_url: str) -> None:
    with TestClient(create_app(Settings(database_url=postgres_url))) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
