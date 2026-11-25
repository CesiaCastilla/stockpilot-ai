"""FastAPI application factory and ASGI entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from stockpilot.api.errors import register_error_handlers
from stockpilot.api.middleware import RequestLogMiddleware
from stockpilot.api.routes import catalog, dealers, health, units
from stockpilot.core.config import Settings, get_settings
from stockpilot.core.database import build_engine, build_session_factory
from stockpilot.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own Settings (e.g. a Testcontainers database URL)."""
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(settings)
        app.state.session_factory = build_session_factory(engine)
        yield
        engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(RequestLogMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    # Versioned prefix: a breaking change can live at /api/v2 without breaking clients.
    app.include_router(dealers.router, prefix="/api/v1")
    app.include_router(catalog.router, prefix="/api/v1")
    app.include_router(units.router, prefix="/api/v1")
    return app


app = create_app()
