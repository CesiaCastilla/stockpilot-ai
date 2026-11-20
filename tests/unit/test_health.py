from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stockpilot.api.routes.health import check_database
from stockpilot.core.config import Settings
from stockpilot.main import create_app


@pytest.fixture
def app() -> FastAPI:
    # A fresh app per test, so dependency overrides never leak between tests.
    return create_app(Settings())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_ok_when_database_is_available(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[check_database] = lambda: True

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_database_is_unavailable(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[check_database] = lambda: False

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_health_reports_unreachable_database_without_mocks() -> None:
    # Port 1 on localhost refuses connections immediately: exercises the real
    # SQLAlchemy error path without needing Docker.
    settings = Settings(
        database_url="postgresql+psycopg://user:pass@127.0.0.1:1/none",
        database_connect_timeout=1,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
