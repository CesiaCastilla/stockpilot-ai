"""FastAPI dependencies. Tests replace them with app.dependency_overrides."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from langchain_core.language_models import BaseChatModel
from sqlalchemy.orm import Session

from stockpilot.agents.llm import build_chat_model
from stockpilot.core.config import Settings
from stockpilot.core.database import get_session
from stockpilot.domain.coverage import CoveragePolicy
from stockpilot.services.agent_runs import AgentRunService, ModelFactory
from stockpilot.services.catalog import CatalogService
from stockpilot.services.coverage import CoverageService
from stockpilot.services.dealers import DealerService
from stockpilot.services.transfers import TransferService
from stockpilot.services.units import UnitService

SessionDep = Annotated[Session, Depends(get_session)]


def get_dealer_service(session: SessionDep) -> DealerService:
    return DealerService(session)


DealerServiceDep = Annotated[DealerService, Depends(get_dealer_service)]


def get_catalog_service(session: SessionDep) -> CatalogService:
    return CatalogService(session)


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]


def get_unit_service(session: SessionDep) -> UnitService:
    return UnitService(session)


UnitServiceDep = Annotated[UnitService, Depends(get_unit_service)]


def get_transfer_service(session: SessionDep) -> TransferService:
    return TransferService(session)


TransferServiceDep = Annotated[TransferService, Depends(get_transfer_service)]


def get_now() -> datetime:
    """The clock is a dependency, so tests can freeze it."""
    return datetime.now(UTC)


NowDep = Annotated[datetime, Depends(get_now)]


def get_coverage_policy(request: Request) -> CoveragePolicy:
    # The settings the app was created with, not the global ones.
    settings: Settings = request.app.state.settings
    return settings.coverage_policy()


def get_coverage_service(
    session: SessionDep, policy: Annotated[CoveragePolicy, Depends(get_coverage_policy)]
) -> CoverageService:
    return CoverageService(session, policy)


CoverageServiceDep = Annotated[CoverageService, Depends(get_coverage_service)]


def get_settings_of_app(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings_of_app)]


def get_chat_model(settings: SettingsDep) -> BaseChatModel:
    """A new model per run. Raises LlmNotConfiguredError (503) without a key."""
    return build_chat_model(settings)


ChatModelDep = Annotated[BaseChatModel, Depends(get_chat_model)]


def get_model_factory(settings: SettingsDep) -> ModelFactory:
    """For resuming a run: the model is created only if the run needs it, so
    approving orders never fails because no AI model is configured."""
    return lambda: build_chat_model(settings)


ModelFactoryDep = Annotated[ModelFactory, Depends(get_model_factory)]


def get_agent_run_service(request: Request, settings: SettingsDep) -> AgentRunService:
    # The app's session factory, not the request session: a run outlives the request.
    return AgentRunService(
        request.app.state.session_factory,
        request.app.state.agent_tools,
        checkpointer=request.app.state.checkpoints.saver,
        clock=get_now,
        max_model_calls=settings.agent_max_model_calls,
    )


AgentRunServiceDep = Annotated[AgentRunService, Depends(get_agent_run_service)]
