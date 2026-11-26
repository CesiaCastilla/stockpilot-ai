from datetime import date

from stockpilot.agents.prompts import system_prompt, task
from stockpilot.schemas.agent_runs import AgentRunRequest


def test_the_model_knows_today() -> None:
    assert "Today is 2026-11-26." in system_prompt(date(2026, 11, 26))


def test_task_without_filters() -> None:
    assert task(AgentRunRequest()) == "Propose at most 5 transfers."


def test_task_with_filters() -> None:
    request = AgentRunRequest(vehicle_model_id=2, trim_id=9, max_proposals=3)

    assert task(request) == (
        "Propose at most 3 transfers. Only shortages of vehicle_model_id 2. "
        "Only shortages of trim_id 9."
    )
