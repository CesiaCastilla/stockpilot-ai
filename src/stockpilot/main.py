"""FastAPI application factory and ASGI entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from stockpilot.agents.checkpoints import CheckpointStore
from stockpilot.agents.tools import build_tool_context, build_tools
from stockpilot.api.errors import register_error_handlers
from stockpilot.api.middleware import RequestLogMiddleware
from stockpilot.api.routes import (
    agent_runs,
    catalog,
    dealers,
    health,
    network,
    transfer_orders,
    units,
)
from stockpilot.core.config import Settings, get_settings
from stockpilot.core.database import build_engine, build_session_factory
from stockpilot.core.logging import configure_logging
from stockpilot.integrations.http import build_http_client


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own Settings (e.g. a Testcontainers database URL)."""
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(settings)
        app.state.session_factory = build_session_factory(engine)
        # One HTTP client and one set of tools for the whole app: the caches and
        # the failure cooldown of each external API are shared by every run.
        # Paused agent runs live here. It connects on first use, not at startup.
        app.state.checkpoints = CheckpointStore(settings.database_url)
        with build_http_client(settings) as http:
            context = build_tool_context(settings, app.state.session_factory, http)
            app.state.agent_tools = build_tools(context)
            yield
        app.state.checkpoints.close()
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
    app.include_router(transfer_orders.router, prefix="/api/v1")
    app.include_router(network.router, prefix="/api/v1")
    app.include_router(agent_runs.router, prefix="/api/v1")
    return app


app = create_app()
