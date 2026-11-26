from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import Field

from scripts.check_llm import main
from stockpilot.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    # Inside a fixture, so the environment is already cleaned (tests/conftest.py).
    return Settings(_env_file=None)


class FakeModel(GenericFakeChatModel):
    """Answers with fixed messages and accepts tools, like a real chat model."""

    bound: list[str] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        self.bound.extend(t.name for t in tools if isinstance(t, BaseTool))
        return self


def _model(*messages: AIMessage) -> FakeModel:
    answers: Iterator[AIMessage] = iter(messages)
    return FakeModel(messages=answers)


def test_prints_the_tool_the_model_asked_for(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    model = _model(
        AIMessage(
            content="",
            tool_calls=[{"name": "get_dealers_with_shortage", "args": {"limit": 5}, "id": "1"}],
        )
    )

    assert main(settings, model) == 0

    out = capsys.readouterr().out
    assert 'get_dealers_with_shortage({"limit": 5})' in out
    assert "get_aged_units" in model.bound  # every tool was offered


def test_an_answer_without_tools_is_a_failure(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(settings, _model(AIMessage(content="Hello"))) == 2

    assert "supports tool calling" in capsys.readouterr().err


def test_a_model_that_fails_is_reported(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    class Broken(FakeModel):
        def bind_tools(
            self,
            tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
            *,
            tool_choice: str | None = None,
            **kwargs: Any,
        ) -> Runnable[LanguageModelInput, AIMessage]:
            raise RuntimeError("API key not valid")

    assert main(settings, Broken(messages=iter([]))) == 1

    assert "did not answer: API key not valid" in capsys.readouterr().err


def test_without_a_key_it_says_how_to_get_one(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(settings) == 1

    assert "Google AI Studio" in capsys.readouterr().err
