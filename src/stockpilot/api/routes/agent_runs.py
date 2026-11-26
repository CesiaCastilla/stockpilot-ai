"""Agent runs. Starting one answers at once; the agent works in the background."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, status

from stockpilot.api.dependencies import AgentRunServiceDep, ChatModelDep, SettingsDep
from stockpilot.api.params import Limit, Offset
from stockpilot.domain.agent_runs import AgentRunTrigger
from stockpilot.schemas.agent_runs import AgentRunRead, AgentRunRequest
from stockpilot.schemas.common import DatabaseId, ErrorResponse, Page

router = APIRouter(prefix="/agent/runs", tags=["agent"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Run not found"},
}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "No AI model configured",
        }
    },
)
def start_agent_run(
    payload: AgentRunRequest,
    background: BackgroundTasks,
    service: AgentRunServiceDep,
    model: ChatModelDep,
    settings: SettingsDep,
) -> AgentRunRead:
    """Start a run and answer with it, still running. Follow it with GET /agent/runs/{id};
    the transfers it proposes appear in /transfer-orders as pending."""
    run = service.start(
        payload,
        trigger=AgentRunTrigger.API,
        provider=settings.llm_provider,
        model=settings.llm_model,
    )
    # A model call can take many seconds: the response does not wait for it.
    background.add_task(service.execute, run.id, model)
    return AgentRunRead.model_validate(run)


@router.get("")
def list_agent_runs(
    service: AgentRunServiceDep, limit: Limit = 50, offset: Offset = 0
) -> Page[AgentRunRead]:
    """Newest first."""
    items, total = service.search(limit=limit, offset=offset)
    return Page(
        items=[AgentRunRead.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", responses=_NOT_FOUND)
def get_agent_run(run_id: DatabaseId, service: AgentRunServiceDep) -> AgentRunRead:
    return AgentRunRead.model_validate(service.get(run_id))
