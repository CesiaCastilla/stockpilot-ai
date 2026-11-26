"""Tools the agent can call. Each one is a thin wrapper over a tested service:
the agent never writes SQL and never computes a business number itself.

Every tool opens its own session, returns JSON, and turns a domain error into
an {"error": ...} answer, so the model can read what went wrong and try again.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any

import httpx
import structlog
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, ValidationError
from pydantic_core import to_jsonable_python
from sqlalchemy.orm import Session, sessionmaker

from stockpilot.core.config import Settings
from stockpilot.domain.coverage import CoveragePolicy
from stockpilot.domain.delivery import MAX_TRANSIT_DAYS
from stockpilot.domain.errors import DomainError
from stockpilot.domain.geo import Coordinates
from stockpilot.integrations.argentinadatos import build_argentina_datos
from stockpilot.integrations.dolarapi import build_dolar_api
from stockpilot.integrations.vpic import build_vpic
from stockpilot.schemas.common import DatabaseId, NoNul
from stockpilot.schemas.coverage import NetworkFilters
from stockpilot.services.coverage import CoverageService
from stockpilot.services.delivery import DeliveryService, HolidayCalendar
from stockpilot.services.distance import DistanceService
from stockpilot.services.stock_value import ExchangeRateSource, StockValueService
from stockpilot.services.vin import VinDecoder, VinService

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """What the tools need. Tests build it with fakes: no network, no real clock."""

    # A new session per tool call: none stays open while the model thinks.
    session_factory: Callable[[], Session]
    policy: CoveragePolicy
    distribution_center: Coordinates
    vin_decoder: VinDecoder
    holidays: HolidayCalendar
    rates: ExchangeRateSource
    clock: Callable[[], datetime]


def build_tool_context(
    settings: Settings, session_factory: sessionmaker[Session], http: httpx.Client
) -> ToolContext:
    """The real context. One HTTP client for every tool, so caches and the failure
    cooldown of each external API are shared between calls."""
    return ToolContext(
        session_factory=session_factory,
        policy=settings.coverage_policy(),
        distribution_center=settings.distribution_center(),
        vin_decoder=build_vpic(settings, http),
        holidays=build_argentina_datos(settings, http),
        rates=build_dolar_api(settings, http),
        clock=lambda: datetime.now(UTC),
    )


# --- Arguments ----------------------------------------------------------------
# What the model sees as the schema of each tool. Ids are bounded like in the
# API: a number the database cannot hold is an argument error, not a crash.
# Gemini drops minimum and maximum from the schema it is given, so the model
# does not know the bounds: they are checked here and explained when broken.

ListLimit = Field(default=20, ge=1, le=100, description="At most this many results")


class ShortageArgs(BaseModel):
    vehicle_model_id: DatabaseId | None = Field(default=None, description="Only this model")
    trim_id: DatabaseId | None = Field(default=None, description="Only this trim (version)")
    limit: int = ListLimit


class AgedUnitsArgs(BaseModel):
    min_days: int | None = Field(
        default=None,
        ge=0,
        le=3650,
        description="More than this many days in stock. Default: the aging threshold. "
        "0: every unit that can be moved, including new ones at the distribution center.",
    )
    vehicle_model_id: DatabaseId | None = Field(default=None, description="Only this model")
    trim_id: DatabaseId | None = Field(default=None, description="Only this trim (version)")
    limit: int = ListLimit


class VinArgs(BaseModel):
    vin: Annotated[str, NoNul] = Field(max_length=64, description="17-character VIN")


class DistanceArgs(BaseModel):
    origin_dealer_id: DatabaseId | None = Field(
        description="null: the distribution center, as in dealer_id of a unit"
    )
    destination_dealer_id: DatabaseId | None = Field(
        description="null: the distribution center, as in dealer_id of a unit"
    )


class StockValueArgs(BaseModel):
    dealer_id: DatabaseId = Field(description="The dealer whose aged units are valued")


class DeliveryArgs(BaseModel):
    transit_days: int = Field(ge=0, le=MAX_TRANSIT_DAYS, description="Business days on the road")
    start: date | None = Field(default=None, description="Departure day. Default: today")


# --- Tools --------------------------------------------------------------------


def build_tools(ctx: ToolContext) -> list[BaseTool]:
    def get_dealers_with_shortage(args: ShortageArgs) -> Any:
        filters = NetworkFilters(vehicle_model_id=args.vehicle_model_id, trim_id=args.trim_id)
        with ctx.session_factory() as session:
            report = CoverageService(session, ctx.policy).shortages(filters, now=ctx.clock())
        # Decided here, not left to the model: the evals showed it sometimes
        # proposed transfers to a full yard. These go apart, with the reason.
        actionable = [s for s in report.items if s.units_needed and s.free_places]
        cannot_be_served = [
            {
                "dealer_code": s.dealer_code,
                "trim_id": s.trim_id,
                "trim_name": s.trim_name,
                "why": "the yard is full" if not s.free_places else "pending orders cover it",
            }
            for s in report.items
            if not (s.units_needed and s.free_places)
        ]
        return {
            **report.model_dump(exclude={"items"}),
            "total": len(actionable),
            "items": actionable[: args.limit],
            "cannot_be_served": cannot_be_served,
        }

    def get_aged_units(args: AgedUnitsArgs) -> Any:
        filters = NetworkFilters(vehicle_model_id=args.vehicle_model_id, trim_id=args.trim_id)
        with ctx.session_factory() as session:
            page = CoverageService(session, ctx.policy).aged_units(
                filters,
                now=ctx.clock(),
                min_days=args.min_days,
                limit=args.limit,
                # Same reason: the evals showed it took units from a dealer short of them.
                skip_short_dealers=True,
            )
        # No offset: the model asks for a narrower list instead of paging.
        return page.model_dump(exclude={"limit", "offset"})

    def decode_vin(args: VinArgs) -> Any:
        with ctx.session_factory() as session:
            return VinService(session, ctx.vin_decoder).decode(args.vin, today=ctx.clock().date())

    def get_distance(args: DistanceArgs) -> Any:
        with ctx.session_factory() as session:
            km = DistanceService(session, ctx.distribution_center).between_km(
                args.origin_dealer_id, args.destination_dealer_id
            )
        return {"km": km, "kind": "straight line"}

    def get_aged_stock_value(args: StockValueArgs) -> Any:
        with ctx.session_factory() as session:
            return StockValueService(session, ctx.policy, ctx.rates).aged_stock(
                args.dealer_id, now=ctx.clock()
            )

    def estimate_delivery_date(args: DeliveryArgs) -> Any:
        start = args.start if args.start is not None else ctx.clock().date()
        return DeliveryService(ctx.holidays).estimate(start, args.transit_days)

    return [
        _tool(
            get_dealers_with_shortage,
            ShortageArgs,
            "Shortages that transfers can fix, lowest coverage first. units_needed is "
            "how many more units each one needs, already counting pending transfer "
            "orders; free_places is how many its yard can still take. cannot_be_served "
            "lists the other shortages and why: there is nothing to propose for them.",
        ),
        _tool(
            get_aged_units,
            AgedUnitsArgs,
            "Units that can be transferred today (in a yard, not reserved, without a "
            "pending order, not at a dealer short of their trim), longest in stock "
            "first. dealer_id null means the distribution center.",
        ),
        _tool(
            decode_vin,
            VinArgs,
            "Make, model and model year of a VIN, from NHTSA vPIC and our own catalog.",
        ),
        _tool(
            get_distance,
            DistanceArgs,
            "Straight-line distance in km between two yards. Use null for the distribution center.",
        ),
        _tool(
            get_aged_stock_value,
            StockValueArgs,
            "List price of the aged units of a dealer, in USD and in pesos at the official "
            "exchange rate. Use it to say how much money a transfer frees up.",
        ),
        _tool(
            estimate_delivery_date,
            DeliveryArgs,
            "Arrival date after a number of business days, skipping weekends and "
            "Argentine national holidays.",
        ),
    ]


def _tool[A: BaseModel](func: Callable[[A], Any], schema: type[A], description: str) -> BaseTool:
    name = func.__name__

    def run(**kwargs: Any) -> str:
        # LangChain already checked kwargs against the schema; this fills the
        # defaults, which live only in the schema.
        args = schema.model_validate(kwargs)
        started = time.perf_counter()
        try:
            result = to_jsonable_python(func(args))
        except DomainError as error:
            # A wrong id, an unknown dealer, a VIN with a bad check digit: the
            # model can fix it. Anything else is a bug and must fail loudly.
            _log(name, kwargs, started, error=str(error))
            return _error(str(error))
        _log(name, kwargs, started)
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        func=run,
        name=name,
        description=description,
        args_schema=schema,
        handle_validation_error=_invalid_arguments,
    )


def _invalid_arguments(error: ValidationError) -> str:
    # One line per broken argument, so the model knows what to change.
    problems = "; ".join(
        f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in error.errors()
    )
    return _error(f"Invalid arguments: {problems}")


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _log(name: str, kwargs: dict[str, Any], started: float, error: str | None = None) -> None:
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if error is None:
        logger.info("agent_tool_called", tool=name, args=kwargs, elapsed_ms=elapsed_ms)
    else:
        logger.warning(
            "agent_tool_failed", tool=name, args=kwargs, elapsed_ms=elapsed_ms, error=error
        )
