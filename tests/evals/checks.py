"""What a run must satisfy. Properties, never exact text: the model may word
things differently each time, but it must not break a business rule or state
a number that is not true.

A check is passed (True), failed (False) or not applicable (None).
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from stockpilot.core.config import Settings
from stockpilot.domain.geo import Coordinates, distance_km
from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.models import AgentRun, TransferOrder
from stockpilot.schemas.coverage import AgedUnit, Shortage
from tests.evals.scenario import Scenario
from tests.evals.world import World

# "Prefer the unit longest in stock; among similar ones, the closest": a unit
# this many days younger than one left behind still counts as similar.
SIMILAR_AGE_DAYS = 30


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool | None
    detail: str = ""


@dataclass(frozen=True)
class Before:
    """The network as the agent found it."""

    shortages: dict[tuple[int, int], Shortage]
    # Every unit that could be moved, by VIN.
    movable: dict[str, AgedUnit]
    existing_vins: frozenset[str]


@dataclass(frozen=True)
class After:
    run: AgentRun
    orders: Sequence[TransferOrder]


def check_run(scenario: Scenario, world: World, before: Before, after: After) -> list[Check]:
    return [*_invariants(world, before, after), *_expectations(scenario, world, after)]


# --- Invariants: every scenario ------------------------------------------------


def _invariants(world: World, before: Before, after: After) -> list[Check]:
    orders = after.orders
    live = [o for o in orders if o.status is not TransferOrderStatus.REJECTED]
    submitted = [s["vin"] for s in after.run.submissions if "vin" in s]

    invented = sorted({vin for vin in submitted if vin not in before.existing_vins})
    rejected = [s for s in after.run.submissions if s.get("violations") or s.get("error")]

    wrong = [o for o in orders if (o.destination_dealer_id, o.unit.trim_id) not in before.shortages]

    over_needed = [
        f"{world.code(dest)} needs {before.shortages[(dest, trim)].units_needed}, got {n}"
        for (dest, trim), n in Counter(
            (o.destination_dealer_id, o.unit.trim_id) for o in live
        ).items()
        if (dest, trim) in before.shortages and n > before.shortages[(dest, trim)].units_needed
    ]
    free = {dest: s.free_places for (dest, _), s in before.shortages.items()}
    over_free = [
        f"{world.code(dest)} has {free[dest]} free places, got {n}"
        for dest, n in Counter(o.destination_dealer_id for o in live).items()
        if dest in free and n > free[dest]
    ]
    from_short = [
        o.unit.vin
        for o in orders
        if o.origin_dealer_id is not None
        and (o.origin_dealer_id, o.unit.trim_id) in before.shortages
    ]
    return [
        Check("run_finished", after.run.status.value != "failed", after.run.error or ""),
        Check("no_invented_vins", not invented, ", ".join(invented)),
        Check(
            "no_validator_rejections",
            not rejected,
            "; ".join(_rejection(s) for s in rejected),
        ),
        Check(
            "right_trim_and_destination",
            not wrong if orders else None,
            ", ".join(f"{o.unit.vin} to {world.code(o.destination_dealer_id)}" for o in wrong),
        ),
        Check("within_units_needed", not over_needed if live else None, "; ".join(over_needed)),
        Check("within_free_places", not over_free if live else None, "; ".join(over_free)),
        Check("origin_not_short", not from_short if orders else None, ", ".join(from_short)),
        _oldest_first(world, before, orders),
        _reason_facts(world, before, orders),
    ]


def _rejection(submission: dict[str, object]) -> str:
    if "error" in submission:
        return "malformed proposal"
    violations = submission.get("violations")
    assert isinstance(violations, list)
    return f"{submission.get('vin')}: {', '.join(v['rule'] for v in violations)}"


def _oldest_first(world: World, before: Before, orders: Sequence[TransferOrder]) -> Check:
    if not orders:
        return Check("oldest_first", None)
    chosen = {o.unit.vin for o in orders}
    problems = []
    for order in orders:
        unit = before.movable.get(order.unit.vin)
        if unit is None:
            continue  # not a movable unit: another check reports it
        left_behind = [
            c
            for c in before.movable.values()
            if c.vin not in chosen
            and c.trim_id == unit.trim_id
            and c.dealer_id != order.destination_dealer_id
            and (c.dealer_id is None or (c.dealer_id, c.trim_id) not in before.shortages)
            and c.days_in_stock > unit.days_in_stock + SIMILAR_AGE_DAYS
        ]
        if left_behind:
            oldest = max(left_behind, key=lambda c: c.days_in_stock)
            problems.append(
                f"{unit.vin} ({unit.days_in_stock} days) over {oldest.vin} "
                f"({oldest.days_in_stock} days, at {world.code(oldest.dealer_id)})"
            )
    return Check("oldest_first", not problems, "; ".join(problems))


# Numbers a reason may state, in Spanish. Only these are judged; others are ignored.
_DAYS = re.compile(r"(\d+)\s*d[ií]as\s+(?:en\s+(?:stock|playa|inventario)|de\s+antig)", re.I)
_SALES = re.compile(r"(\d+)\s+(?:ventas|unidades\s+vendidas)|ventas\s+recientes\s+de\s+(\d+)", re.I)
_KM = re.compile(r"(\d+(?:[.,]\d+)?)\s*km\b", re.I)


def _reason_facts(world: World, before: Before, orders: Sequence[TransferOrder]) -> Check:
    center = Settings(_env_file=None).distribution_center()
    stated = 0
    wrong = []
    for order in orders:
        unit = before.movable.get(order.unit.vin)
        shortage = before.shortages.get((order.destination_dealer_id, order.unit.trim_id))
        destination = order.destination_dealer
        for match in _DAYS.finditer(order.reason):
            stated += 1
            if unit is not None and abs(int(match[1]) - unit.days_in_stock) > 1:
                wrong.append(f"says {match[1]} days, it has {unit.days_in_stock}")
        for match in _SALES.finditer(order.reason):
            stated += 1
            said = int(match[1] or match[2])
            if shortage is not None and said != shortage.recent_sales:
                wrong.append(f"says {said} sales, there were {shortage.recent_sales}")
        origin = order.origin_dealer
        start = (
            center if origin is None else Coordinates(origin.latitude or 0, origin.longitude or 0)
        )
        end = Coordinates(destination.latitude or 0, destination.longitude or 0)
        km = distance_km(start, end)
        for match in _KM.finditer(order.reason):
            stated += 1
            said_km = float(match[1].replace(",", "."))
            if abs(said_km - km) > max(1.0, km * 0.02):
                wrong.append(f"says {said_km:g} km, it is {km:.1f}")
    if not stated:
        return Check("reason_facts", None, "no number to verify")
    return Check("reason_facts", not wrong, "; ".join(wrong) or f"{stated} numbers verified")


# --- Expectations: per scenario -------------------------------------------------


def _expectations(scenario: Scenario, world: World, after: After) -> list[Check]:
    expect = scenario.expect
    orders = after.orders
    codes = [world.code(o.destination_dealer_id) for o in orders]
    origins = [world.code(o.origin_dealer_id) for o in orders]
    chosen_labels = {world.labels.get(o.unit.vin) for o in orders}
    low, high = expect.orders
    checks = [
        Check(
            "expected_order_count",
            low <= len(orders) <= high,
            f"{len(orders)} orders, expected {low} to {high}",
        )
    ]
    if expect.outcome is not None:
        outcome = after.run.outcome.value if after.run.outcome else after.run.status.value
        # A run waiting for decisions has proposed: that is its outcome so far.
        outcome = "proposed" if outcome == "waiting" else outcome
        checks.append(Check("expected_outcome", outcome in expect.outcome, outcome))
    if expect.destinations is not None:
        bad = sorted(set(codes) - set(expect.destinations))
        checks.append(Check("expected_destinations", not bad, ", ".join(bad)))
    if expect.origins is not None:
        bad = sorted(set(origins) - set(expect.origins))
        checks.append(Check("expected_origins", not bad, ", ".join(bad)))
    if expect.chosen:
        missing = sorted(set(expect.chosen) - chosen_labels)
        checks.append(Check("chose_expected_units", not missing, ", ".join(missing)))
    if expect.not_chosen:
        bad = sorted(set(expect.not_chosen) & chosen_labels)
        checks.append(Check("avoided_units", not bad, ", ".join(bad)))
    if expect.replacement:
        rejected = {o.unit.vin for o in orders if o.status is TransferOrderStatus.REJECTED}
        replaced = [o for o in orders if o.status is TransferOrderStatus.PENDING]
        ok = bool(rejected) and bool(replaced) and not {o.unit.vin for o in replaced} & rejected
        checks.append(Check("proposed_a_replacement", ok, f"{len(replaced)} replacement orders"))
    return checks
