"""Locate the dealers without coordinates, using Georef.

Run it with: uv run python -m stockpilot.workflows.sync_dealer_locations
"""

import sys
from uuid import uuid4

import structlog

from stockpilot.core.config import Settings, get_settings
from stockpilot.core.database import build_engine, build_session_factory
from stockpilot.core.logging import configure_logging
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.integrations.georef import build_georef
from stockpilot.integrations.http import build_http_client
from stockpilot.services.dealer_locations import DealerLocationService

logger = structlog.get_logger(__name__)


def run(settings: Settings) -> int:
    """Exit code: 1 when Georef failed, so a scheduler can tell."""
    configure_logging(settings)
    # One id per run, like the API has one per request.
    with structlog.contextvars.bound_contextvars(correlation_id=uuid4().hex):
        return _run(settings)


def _run(settings: Settings) -> int:
    engine = build_engine(settings)
    try:
        with build_http_client(settings) as http, build_session_factory(engine)() as session:
            report = DealerLocationService(session, build_georef(settings, http)).sync()
    except ExternalServiceError as error:
        logger.error("dealer_location_sync_failed", error=str(error))
        return 1
    finally:
        engine.dispose()
    logger.info(
        "dealer_location_sync_done", located=len(report.located), unresolved=report.unresolved
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run(get_settings()))
