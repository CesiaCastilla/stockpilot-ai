"""Agent regression evals: every scenario of scenarios.yaml with the real model.

Not part of CI and never run by `uv run pytest`. Run them on purpose:

    uv run pytest -m eval                      # every scenario
    uv run pytest -m eval -k oldest            # some of them

Each run writes a report to tests/evals/reports/.
"""

import time
from collections.abc import Callable

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from stockpilot.agents.llm import build_chat_model
from stockpilot.agents.tools import ToolContext, build_tools
from stockpilot.core.config import Settings
from stockpilot.domain.agent_runs import AgentRunStatus, AgentRunTrigger
from stockpilot.models import TransferOrder, Unit
from stockpilot.repositories.transfer_orders import TransferOrderRepository
from stockpilot.schemas.coverage import NetworkFilters
from stockpilot.services.agent_runs import AgentRunService
from stockpilot.services.coverage import CoverageService
from stockpilot.services.transfers import TransferService
from tests.evals.checks import After, Before, check_run
from tests.evals.conftest import FixedExternalApis
from tests.evals.report import Report, ScenarioResult
from tests.evals.scenario import Scenario, load_scenarios
from tests.evals.world import NOW, World, build_world

pytestmark = pytest.mark.eval

SCENARIOS = load_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
@pytest.mark.usefixtures("pace")
def test_scenario(
    scenario: Scenario,
    db_session: Session,
    session_factory: Callable[[], Session],
    eval_settings: Settings,
    report: Report,
) -> None:
    world = build_world(db_session, scenario)
    before = _before(db_session, eval_settings)
    service = _service(session_factory, eval_settings)
    model = build_chat_model(eval_settings)

    started = time.perf_counter()
    run = service.start(
        world.request(scenario),
        trigger=AgentRunTrigger.API,
        provider=eval_settings.llm_provider,
        model=eval_settings.llm_model,
    )
    run = service.execute(run.id, lambda: model)
    if scenario.reject_with and run.status is AgentRunStatus.WAITING:
        _reject_every_order(session_factory, run.id, scenario.reject_with)
        run = service.resume_if_decided(run.id, lambda: model) or service.get(run.id)
    seconds = time.perf_counter() - started

    orders = TransferOrderRepository(db_session).for_run(run.id)
    result = ScenarioResult(
        id=scenario.id,
        title=scenario.title,
        checks=check_run(scenario, world, before, After(run, orders)),
        orders=[_describe(world, o) for o in orders],
        model_calls=run.model_calls,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        seconds=round(seconds, 1),
        stop_reason=run.stop_reason,
        summary=run.summary,
    )
    report.results.append(result)

    assert result.passed, "\n".join(f"{c.name}: {c.detail}" for c in result.failures)


def _before(session: Session, settings: Settings) -> Before:
    coverage = CoverageService(session, settings.coverage_policy())
    shortages = coverage.shortages(NetworkFilters(), now=NOW).items
    movable = coverage.aged_units(NetworkFilters(), now=NOW, min_days=0, limit=10_000).items
    return Before(
        shortages={(s.dealer_id, s.trim_id): s for s in shortages},
        movable={u.vin: u for u in movable},
        existing_vins=frozenset(session.scalars(select(Unit.vin))),
    )


def _service(session_factory: Callable[[], Session], settings: Settings) -> AgentRunService:
    apis = FixedExternalApis()
    context = ToolContext(
        session_factory=session_factory,
        policy=settings.coverage_policy(),
        distribution_center=settings.distribution_center(),
        vin_decoder=apis,
        holidays=apis,
        rates=apis,
        clock=lambda: NOW,
    )
    checkpointer = InMemorySaver()
    return AgentRunService(
        session_factory,
        build_tools(context),
        checkpointer=lambda: checkpointer,
        clock=lambda: NOW,
        max_model_calls=settings.agent_max_model_calls,
    )


def _reject_every_order(session_factory: Callable[[], Session], run_id: int, note: str) -> None:
    with session_factory() as session:
        orders = TransferOrderRepository(session).for_run(run_id)
        for order in orders:
            TransferService(session).reject(order.id, note)


def _describe(world: World, order: TransferOrder) -> str:
    label = world.labels.get(order.unit.vin)
    return (
        f"{order.unit.vin}{f' ({label})' if label else ''} "
        f"{world.code(order.origin_dealer_id)} -> {world.code(order.destination_dealer_id)}, "
        f"{order.status.value}: {order.reason}"
    )
