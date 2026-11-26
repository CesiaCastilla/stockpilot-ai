"""Check that the AI model answers and asks for the agent's tools.

Run it with: uv run python scripts/check_llm.py

One request to the model. No tool runs and the database is not touched: it
only prints which tool the model chose and with which arguments.
"""

import json
import sys
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolCall
from langchain_core.tools import BaseTool

from stockpilot.agents.llm import LlmNotConfiguredError, build_chat_model
from stockpilot.agents.tools import build_tool_context, build_tools
from stockpilot.core.config import Settings, get_settings
from stockpilot.core.database import build_engine, build_session_factory
from stockpilot.integrations.http import build_http_client

QUESTION = "Which dealers are about to run out of units? Start by looking at the data."


def ask(
    model: BaseChatModel, tools: Sequence[BaseTool], question: str = QUESTION
) -> list[ToolCall]:
    response = model.bind_tools(tools).invoke(question)
    return list(getattr(response, "tool_calls", []))


def main(settings: Settings, model: BaseChatModel | None = None) -> int:
    try:
        model = model or build_chat_model(settings)
    except LlmNotConfiguredError as error:
        print(error, file=sys.stderr)
        return 1

    # Nothing connects here: the tools are only described to the model.
    engine = build_engine(settings)
    try:
        with build_http_client(settings) as http:
            tools = build_tools(build_tool_context(settings, build_session_factory(engine), http))
            calls = ask(model, tools)
    except Exception as error:  # wrong key, unknown model, no quota left, Ollama not running
        print(
            f"{settings.llm_provider} / {settings.llm_model} did not answer: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        engine.dispose()

    if not calls:
        print(
            f"{settings.llm_provider} / {settings.llm_model} answered without asking for a "
            "tool. The agent needs a model that supports tool calling.",
            file=sys.stderr,
        )
        return 2
    print(f"{settings.llm_provider} / {settings.llm_model} works and asked for:")
    for call in calls:
        print(f"  {call['name']}({json.dumps(call['args'], ensure_ascii=False)})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(get_settings()))
