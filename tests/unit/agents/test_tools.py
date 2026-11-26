"""Tools without a database: their schemas, argument errors and the delivery estimate."""

import json
from collections.abc import Collection
from datetime import UTC, date, datetime
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from langchain_google_genai._function_utils import convert_to_genai_function_declarations
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from stockpilot.agents.tools import ToolContext, build_tools
from stockpilot.core.config import Settings
from stockpilot.domain.delivery import Holiday
from stockpilot.domain.errors import ExternalServiceError
from stockpilot.domain.exchange_rate import ExchangeRate
from stockpilot.domain.vin_info import VehicleInfo

NOW = datetime(2026, 12, 4, 15, 0, tzinfo=UTC)  # a Friday
SETTINGS = Settings(_env_file=None)  # not the .env of the developer


class FakeCalendar:
    def __init__(self, *days: date, fail: Exception | None = None) -> None:
        self._days = days
        self._fail = fail

    def holidays(self, year: int) -> Collection[Holiday]:
        if self._fail is not None:
            raise self._fail
        return [Holiday(day, "Holiday") for day in self._days if day.year == year]


class Unused:
    """Stands for what a test must not reach: the database or an external API."""

    def __call__(self) -> Session:
        raise AssertionError("the database was used")

    def decode(self, vin: str) -> VehicleInfo:
        raise AssertionError("vPIC was called")

    def official_rate(self) -> ExchangeRate:
        raise AssertionError("DolarApi was called")


def _tools(calendar: FakeCalendar | None = None) -> dict[str, BaseTool]:
    unused = Unused()
    context = ToolContext(
        session_factory=unused,
        policy=SETTINGS.coverage_policy(),
        distribution_center=SETTINGS.distribution_center(),
        vin_decoder=unused,
        holidays=calendar or FakeCalendar(),
        rates=unused,
        clock=lambda: NOW,
    )
    return {tool.name: tool for tool in build_tools(context)}


def _call(tool: BaseTool, **args: Any) -> Any:
    answer = tool.invoke(args)
    assert isinstance(answer, str)
    return json.loads(answer)


def test_the_tools_of_the_proposal() -> None:
    assert list(_tools()) == [
        "get_dealers_with_shortage",
        "get_aged_units",
        "decode_vin",
        "get_distance",
        "get_aged_stock_value",
        "estimate_delivery_date",
    ]


def test_every_tool_has_a_description_for_the_model() -> None:
    for tool in _tools().values():
        assert len(tool.description) > 40, tool.name
        # What the model reads about each argument.
        for name, schema in tool.args.items():
            assert schema.get("description"), (tool.name, name)


def test_gemini_accepts_every_schema() -> None:
    """Gemini supports a subset of JSON Schema. Converting needs no network."""
    declarations = convert_to_genai_function_declarations(list(_tools().values()))

    assert len(declarations.function_declarations) == 6


@pytest.mark.parametrize(
    ("tool", "args", "problem"),
    [
        ("get_distance", {"origin_dealer_id": 2**31, "destination_dealer_id": 1}, "origin"),
        ("get_distance", {"origin_dealer_id": 0, "destination_dealer_id": 1}, "origin"),
        ("get_distance", {"origin_dealer_id": 1}, "destination_dealer_id"),
        ("get_aged_stock_value", {"dealer_id": -1}, "dealer_id"),
        ("get_aged_stock_value", {"dealer_id": "D001"}, "dealer_id"),
        ("get_dealers_with_shortage", {"trim_id": 10**30}, "trim_id"),
        ("get_dealers_with_shortage", {"limit": 0}, "limit"),
        ("get_aged_units", {"min_days": -1}, "min_days"),
        ("get_aged_units", {"min_days": 3651}, "min_days"),
        ("get_aged_units", {"limit": 101}, "limit"),
        ("decode_vin", {"vin": "8AF\x00ZZ12345670001"}, "NUL"),
        ("decode_vin", {"vin": "X" * 65}, "vin"),
        ("estimate_delivery_date", {"transit_days": 366}, "transit_days"),
        ("estimate_delivery_date", {"transit_days": -1}, "transit_days"),
        ("estimate_delivery_date", {"transit_days": 2, "start": "tomorrow"}, "start"),
    ],
)
def test_invalid_arguments_are_explained_to_the_model(
    tool: str, args: dict[str, Any], problem: str
) -> None:
    answer = _call(_tools()[tool], **args)

    assert answer["error"].startswith("Invalid arguments")
    assert problem in answer["error"]


def test_delivery_skips_weekends_and_holidays() -> None:
    tools = _tools(FakeCalendar(date(2026, 12, 8)))

    answer = _call(tools["estimate_delivery_date"], transit_days=2, start="2026-12-04")

    # Friday + 2 business days: Monday 7, (Tuesday 8 is a holiday), Wednesday 9.
    assert answer == {"arrives_on": "2026-12-09", "holidays_checked": True}


def test_delivery_starts_today_by_default() -> None:
    answer = _call(_tools()["estimate_delivery_date"], transit_days=1)

    assert answer["arrives_on"] == "2026-12-07"  # Friday 4 + 1 business day


def test_delivery_without_holidays_still_answers_and_says_so() -> None:
    calendar = FakeCalendar(fail=ExternalServiceError("ArgentinaDatos", "timeout"))

    answer = _call(_tools(calendar)["estimate_delivery_date"], transit_days=1)

    assert answer == {"arrives_on": "2026-12-07", "holidays_checked": False}


def test_a_bug_is_not_hidden_from_the_run() -> None:
    """Only domain errors go back to the model; anything else must fail loudly."""
    tools = _tools(FakeCalendar(fail=RuntimeError("bug")))

    with pytest.raises(RuntimeError, match="bug"):
        tools["estimate_delivery_date"].invoke({"transit_days": 1})


def test_every_call_is_logged() -> None:
    tools = _tools()

    with capture_logs() as logs:
        tools["estimate_delivery_date"].invoke({"transit_days": 1})
        tools["estimate_delivery_date"].invoke({"transit_days": 1, "start": "2026-12-05"})

    assert [(log["event"], log["tool"]) for log in logs] == [
        ("agent_tool_called", "estimate_delivery_date"),
        ("agent_tool_called", "estimate_delivery_date"),
    ]
    assert logs[1]["args"] == {"transit_days": 1, "start": date(2026, 12, 5)}
    assert isinstance(logs[0]["elapsed_ms"], int)
