"""Tests of the evals themselves, without the model. They run in CI.

A reference planner, written by hand, answers every scenario the way a perfect
agent would. If the checks fail its plan, the scenario or the check is wrong,
not the model. Then deliberately wrong plans must be caught.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from stockpilot.core.config import Settings
from stockpilot.domain.agent_runs import AgentRunOutcome, AgentRunStatus, AgentRunTrigger
from stockpilot.domain.geo import Coordinates, distance_km
from stockpilot.domain.transfers import InvalidTransferError, TransferOrderStatus
from stockpilot.models import AgentRun, TransferOrder, Unit
from stockpilot.repositories.transfer_orders import TransferOrderRepository
from stockpilot.schemas.agent_runs import AgentRunRequest
from stockpilot.schemas.coverage import AgedUnit, NetworkFilters
from stockpilot.schemas.transfers import TransferProposal
from stockpilot.services.coverage import CoverageService
from stockpilot.services.transfers import TransferService
from tests.evals.checks import SIMILAR_AGE_DAYS, After, Before, Check, check_run
from tests.evals.scenario import Scenario, load_scenarios
from tests.evals.world import NOW, World, build_world

pytestmark = pytest.mark.integration

SETTINGS = Settings(_env_file=None)
SCENARIOS = load_scenarios()


def test_there_are_15_to_20_scenarios() -> None:
    # What the proposal asks for: enough to cover the business, few enough to run often.
    assert 15 <= len(SCENARIOS) <= 20


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_a_perfect_agent_passes_every_scenario(
    scenario: Scenario, db_session: Session, session_factory: Callable[[], Session]
) -> None:
    world, before = _setup(db_session, scenario)

    checks = _run_plan(db_session, session_factory, scenario, world, before, _reference_plan)

    failed = [f"{c.name}: {c.detail}" for c in checks if c.passed is False]
    assert failed == []


# --- The checks catch mistakes ------------------------------------------------

BASIC = next(s for s in SCENARIOS if s.id == "sold_out_with_aged_units_elsewhere")


def _failed(
    db_session: Session,
    session_factory: Callable[[], Session],
    scenario: Scenario,
    plan: "Planner",
) -> set[str]:
    world, before = _setup(db_session, scenario)
    checks = _run_plan(db_session, session_factory, scenario, world, before, plan)
    return {c.name for c in checks if c.passed is False}


@pytest.mark.parametrize(
    "reason",
    [
        "La unidad lleva 400 días en stock.",
        "El concesionario tiene 9 ventas recientes.",
        "Está a 5 km del destino.",
    ],
    ids=["days", "sales", "km"],
)
def test_a_false_number_in_a_reason_is_caught(
    db_session: Session, session_factory: Callable[[], Session], reason: str
) -> None:
    def plan(world: World, before: Before, request: AgentRunRequest) -> list[Proposal]:
        return [
            Proposal(p.vin, p.destination_dealer_id, reason)
            for p in _reference_plan(world, before, request)
        ]

    assert _failed(db_session, session_factory, BASIC, plan) == {"reason_facts"}


def test_more_units_than_the_yard_takes_are_caught(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    """The validator never lets this happen; the check is there in case it ever does."""
    scenario = next(s for s in SCENARIOS if s.id == "sends_only_what_the_yard_takes")
    world, before = _setup(db_session, scenario)
    run = AgentRun(
        status=AgentRunStatus.COMPLETED,
        outcome=AgentRunOutcome.PROPOSED,
        trigger=AgentRunTrigger.API,
        request={},
        llm_provider="x",
        llm_model="x",
        tool_calls=[],
        submissions=[],
        finished_at=NOW,
    )
    destination = world.dealers["D001"]
    units = db_session.scalars(select(Unit).where(Unit.vin.in_(list(before.movable)[:2]))).all()
    db_session.add(run)
    db_session.flush()
    db_session.add_all(
        TransferOrder(
            unit=unit,
            origin_dealer=unit.dealer,
            destination_dealer=destination,
            status=TransferOrderStatus.PENDING,
            reason="Faltante",
            agent_run_id=run.id,
        )
        for unit in units
    )
    db_session.flush()

    checks = check_run(
        scenario, world, before, After(run, TransferOrderRepository(db_session).for_run(run.id))
    )

    assert {c.name for c in checks if c.passed is False} == {
        "within_free_places",
        "expected_order_count",
    }


def test_more_units_than_needed_are_caught(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    scenario = next(s for s in SCENARIOS if s.id == "sends_only_the_units_needed")

    def plan(world: World, before: Before, request: AgentRunRequest) -> list[Proposal]:
        destination = world.dealers["D001"].id
        return [Proposal(vin, destination, "Faltante") for vin in list(before.movable)[:2]]

    assert _failed(db_session, session_factory, scenario, plan) == {
        "within_units_needed",
        "expected_order_count",
    }


def test_taking_from_a_dealer_short_of_the_trim_is_caught(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    scenario = next(s for s in SCENARIOS if s.id == "does_not_take_from_a_dealer_short_of_it")

    def plan(world: World, before: Before, request: AgentRunRequest) -> list[Proposal]:
        rosario = world.dealers["D003"].id
        vin = next(u.vin for u in before.movable.values() if u.dealer_id == rosario)
        return [Proposal(vin, world.dealers["D001"].id, "Faltante")]

    assert _failed(db_session, session_factory, scenario, plan) == {
        "origin_not_short",
        "expected_origins",
        "avoided_units",
    }


def test_leaving_a_much_older_unit_behind_is_caught(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    scenario = next(s for s in SCENARIOS if s.id == "prefers_the_oldest_unit")

    def plan(world: World, before: Before, request: AgentRunRequest) -> list[Proposal]:
        young = next(vin for vin, label in world.labels.items() if label == "young")
        return [Proposal(young, world.dealers["D001"].id, "Faltante")]

    assert _failed(db_session, session_factory, scenario, plan) == {
        "oldest_first",
        "chose_expected_units",
        "avoided_units",
    }


def test_an_invented_vin_is_caught(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    def plan(world: World, before: Before, request: AgentRunRequest) -> list[Proposal]:
        return [Proposal("8AFZZZ99999999999", world.dealers["D001"].id, "Faltante")]

    assert {"no_invented_vins", "no_validator_rejections"} <= _failed(
        db_session, session_factory, BASIC, plan
    )


# --- The reference planner ----------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    vin: str
    destination_dealer_id: int
    reason: str


Planner = Callable[[World, Before, AgentRunRequest], list[Proposal]]


def _reference_plan(world: World, before: Before, request: AgentRunRequest) -> list[Proposal]:
    """What the system prompt asks, done by hand: most urgent shortage first; the
    oldest unit, or the closest among similar ones; never from a dealer short of
    it; within units_needed, free_places and max_proposals."""
    shortages = sorted(
        (
            s
            for s in before.shortages.values()
            if (request.trim_id is None or s.trim_id == request.trim_id)
            and (request.vehicle_model_id is None or s.vehicle_model_id == request.vehicle_model_id)
        ),
        key=lambda s: (s.coverage_days, -s.recent_sales, s.dealer_code),
    )
    free = {s.dealer_id: s.free_places for s in before.shortages.values()}
    used: set[str] = set()
    plan: list[Proposal] = []
    for shortage in shortages:
        wanted = min(shortage.units_needed, free[shortage.dealer_id])
        for _ in range(wanted):
            if len(plan) >= request.max_proposals:
                return plan
            candidates = [
                u
                for u in before.movable.values()
                if u.trim_id == shortage.trim_id
                and u.vin not in used
                and u.dealer_id != shortage.dealer_id
                and (u.dealer_id is None or (u.dealer_id, u.trim_id) not in before.shortages)
            ]
            if not candidates:
                break
            oldest = max(u.days_in_stock for u in candidates)
            similar = [u for u in candidates if u.days_in_stock >= oldest - SIMILAR_AGE_DAYS]
            unit = min(similar, key=lambda u: _km(world, u, shortage.dealer_id))
            km = _km(world, unit, shortage.dealer_id)
            used.add(unit.vin)
            free[shortage.dealer_id] -= 1
            plan.append(
                Proposal(
                    unit.vin,
                    shortage.dealer_id,
                    f"{shortage.recent_sales} ventas recientes; la unidad lleva "
                    f"{unit.days_in_stock} días en stock, a {km:.1f} km.",
                )
            )
    return plan


def _km(world: World, unit: AgedUnit, destination_id: int) -> float:
    def place(dealer_id: int | None) -> Coordinates:
        if dealer_id is None:
            return SETTINGS.distribution_center()
        dealer = next(d for d in world.dealers.values() if d.id == dealer_id)
        return Coordinates(dealer.latitude or 0, dealer.longitude or 0)

    return distance_km(place(unit.dealer_id), place(destination_id))


# --- Running a plan as if it were a run ---------------------------------------


def _setup(session: Session, scenario: Scenario) -> tuple[World, Before]:
    world = build_world(session, scenario)
    coverage = CoverageService(session, SETTINGS.coverage_policy())
    shortages = coverage.shortages(NetworkFilters(), now=NOW).items
    movable = coverage.aged_units(NetworkFilters(), now=NOW, min_days=0, limit=10_000).items
    before = Before(
        shortages={(s.dealer_id, s.trim_id): s for s in shortages},
        movable={u.vin: u for u in movable},
        existing_vins=frozenset(session.scalars(select(Unit.vin))),
    )
    return world, before


def _run_plan(
    session: Session,
    session_factory: Callable[[], Session],
    scenario: Scenario,
    world: World,
    before: Before,
    planner: Planner,
) -> list[Check]:
    request = world.request(scenario)
    run = AgentRun(
        status=AgentRunStatus.RUNNING,
        trigger=AgentRunTrigger.API,
        request=request.model_dump(mode="json"),
        llm_provider="reference",
        llm_model="reference",
        tool_calls=[],
        submissions=[],
    )
    session.add(run)
    session.flush()

    rejected: set[str] = set()
    plan = planner(world, before, request)
    submissions = _submit(session_factory, run.id, plan)
    if scenario.reject_with and plan:
        with session_factory() as decisions:
            for order in TransferOrderRepository(decisions).for_run(run.id):
                TransferService(decisions).reject(order.id, scenario.reject_with)
                rejected.add(order.unit.vin)
        retry = Before(
            shortages=before.shortages,
            movable={vin: u for vin, u in before.movable.items() if vin not in rejected},
            existing_vins=before.existing_vins,
        )
        submissions += _submit(session_factory, run.id, planner(world, retry, request))

    run.submissions = submissions
    run.status = AgentRunStatus.COMPLETED
    run.outcome = AgentRunOutcome.PROPOSED if submissions else AgentRunOutcome.NO_PROPOSAL
    run.finished_at = NOW
    session.flush()
    orders = TransferOrderRepository(session).for_run(run.id)
    return check_run(scenario, world, before, After(run, orders))


def _submit(
    session_factory: Callable[[], Session], run_id: int, plan: list[Proposal]
) -> list[dict[str, object]]:
    """Through the real validator, like the agent's proposals."""
    submissions: list[dict[str, object]] = []
    for proposal in plan:
        entry: dict[str, object] = {"vin": proposal.vin}
        with session_factory() as session:
            try:
                entry["order_id"] = (
                    TransferService(session)
                    .propose(
                        TransferProposal(
                            vin=proposal.vin,
                            destination_dealer_id=proposal.destination_dealer_id,
                            reason=proposal.reason,
                        ),
                        agent_run_id=run_id,
                    )
                    .id
                )
            except InvalidTransferError as error:
                entry["violations"] = [{"rule": v.rule.value} for v in error.violations]
        submissions.append(entry)
    return submissions
