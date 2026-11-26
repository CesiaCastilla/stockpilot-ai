"""Test doubles shared by several test modules."""

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import Field


class ScriptedChatModel(GenericFakeChatModel):
    """LangChain's fake chat model, answering a fixed script.

    It records what it was sent and which tools were offered, so tests can
    check what the agent told the model, not only what the model answered.
    """

    received: list[list[BaseMessage]] = Field(default_factory=list)
    offered: list[str] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        self.offered = [t.name for t in tools if isinstance(t, BaseTool)]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


def scripted(*answers: AIMessage) -> ScriptedChatModel:
    return ScriptedChatModel(messages=iter(answers))


def calls(*tool_calls: tuple[str, dict[str, Any]], tokens: tuple[int, int] = (0, 0)) -> AIMessage:
    """An answer of the model that asks for tools."""
    return AIMessage(
        content="",
        tool_calls=[
            ToolCall(name=name, args=args, id=f"call-{index}")
            for index, (name, args) in enumerate(tool_calls)
        ],
        usage_metadata={
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "total_tokens": sum(tokens),
        },
    )


def says(text: str) -> AIMessage:
    """An answer of the model in plain text: it is done."""
    return AIMessage(content=text)
