"""The agent as a LangGraph graph: the model asks for data, then submits proposals
that the validator accepts or rejects. Rejected ones go back to the model once.

    START -> agent -> tools -> agent -> ... -> validate -> END
                                                  |
                                                  +-> agent (one retry) -> ...

The graph knows nothing about the database: it receives the tools and a
`propose` function. That is what lets tests run it with a fake model.
"""

import json
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field, ValidationError

from stockpilot.domain.transfers import InvalidTransferError
from stockpilot.schemas.agent_runs import MAX_PROPOSALS_PER_RUN
from stockpilot.schemas.transfers import TransferProposal

SUBMIT = "submit_transfer_proposals"
# The proposal allows the model one correction after the validator rejects it.
MAX_RETRIES = 1

# Creates a pending order and returns its id, or raises InvalidTransferError.
Propose = Callable[[TransferProposal], int]

Stop = Literal["answered", "submitted", "retries_exhausted", "step_limit"]


class SubmitArgs(BaseModel):
    proposals: list[TransferProposal] = Field(
        min_length=1, max_length=MAX_PROPOSALS_PER_RUN, description="Every transfer, at once"
    )


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    model_calls: int
    input_tokens: int
    output_tokens: int
    # Times the validator rejected something. Each one costs a retry.
    rejections: int
    accepted: int
    # Appended by each node, for the audit in agent_runs.
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
    submissions: Annotated[list[dict[str, Any]], operator.add]
    # Set by the node that decides the run is over.
    stop: Stop | None


def initial_state(task: str) -> AgentState:
    return AgentState(
        messages=[HumanMessage(task)],
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
        rejections=0,
        accepted=0,
        tool_calls=[],
        submissions=[],
        stop=None,
    )


@dataclass(frozen=True)
class AgentLimits:
    max_proposals: int
    # Caps the cost of a run: a model that keeps asking for data is stopped.
    max_model_calls: int


def submit_tool() -> BaseTool:
    """Only its schema reaches the model: the validate node handles the call."""

    def not_called_directly(proposals: list[TransferProposal]) -> str:  # pragma: no cover
        raise AssertionError("handled by the validate node")

    return StructuredTool.from_function(
        func=not_called_directly,
        name=SUBMIT,
        description="Submit the final transfer proposals. Each one is checked by the "
        "validator: valid ones become pending orders for a person to approve; the "
        "answer explains the rejected ones.",
        args_schema=SubmitArgs,
    )


def build_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    propose: Propose,
    *,
    system_prompt: str,
    limits: AgentLimits,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    by_name = {tool.name: tool for tool in tools}
    model_with_tools = model.bind_tools([*tools, submit_tool()])

    def agent(state: AgentState) -> dict[str, Any]:
        response = model_with_tools.invoke([SystemMessage(system_prompt), *state["messages"]])
        assert isinstance(response, AIMessage)
        usage = response.usage_metadata or {"input_tokens": 0, "output_tokens": 0}
        calls = state["model_calls"] + 1
        stop: Stop | None = None
        if not response.tool_calls:
            stop = "answered"
        elif calls >= limits.max_model_calls and not _submits(response):
            # Out of calls and still asking for data: it would never get to propose.
            stop = "step_limit"
        return {
            "messages": [response],
            "model_calls": calls,
            "input_tokens": state["input_tokens"] + usage["input_tokens"],
            "output_tokens": state["output_tokens"] + usage["output_tokens"],
            "stop": stop,
        }

    def run_tools(state: AgentState) -> dict[str, Any]:
        answers: list[ToolMessage] = []
        audit: list[dict[str, Any]] = []
        for call in _last_ai(state).tool_calls:
            tool = by_name.get(call["name"])
            if tool is None:
                # A tool the model made up: tell it which ones exist.
                content = _json(error=f"Unknown tool {call['name']}. Available: {sorted(by_name)}")
            else:
                # Bugs raise and stop the run: only domain errors come back as JSON.
                content = str(tool.invoke(call["args"]))
            answers.append(_answer(call, content))
            audit.append({"name": call["name"], "args": call["args"], **_error_of(content)})
        return {"messages": answers, "tool_calls": audit}

    def validate(state: AgentState) -> dict[str, Any]:
        answers: list[ToolMessage] = []
        submissions: list[dict[str, Any]] = []
        accepted, rejected = state["accepted"], False
        for call in _last_ai(state).tool_calls:
            if call["name"] != SUBMIT:
                answers.append(_answer(call, _json(error=f"Not run: call {SUBMIT} alone.")))
                continue
            try:
                proposals = SubmitArgs.model_validate(call["args"]).proposals
            except ValidationError as error:
                rejected = True
                submissions.append({"attempt": state["rejections"] + 1, "error": str(error)})
                answers.append(_answer(call, _json(error=f"Invalid proposals: {error}")))
                continue
            results: list[dict[str, Any]] = []
            for proposal in proposals:
                result = {**proposal.model_dump(), "attempt": state["rejections"] + 1}
                if accepted >= limits.max_proposals:
                    result["skipped"] = f"Limit of {limits.max_proposals} proposals reached"
                else:
                    try:
                        result["order_id"] = propose(proposal)
                        accepted += 1
                    except InvalidTransferError as error:
                        rejected = True
                        result["violations"] = [
                            {"rule": v.rule.value, "message": v.message} for v in error.violations
                        ]
                results.append(result)
            submissions.extend(results)
            answers.append(_answer(call, json.dumps({"results": results}, ensure_ascii=False)))

        stop: Stop | None = "submitted"
        rejections = state["rejections"] + rejected
        if rejected:
            out_of_calls = state["model_calls"] >= limits.max_model_calls
            stop = "retries_exhausted" if rejections > MAX_RETRIES or out_of_calls else None
        return {
            "messages": answers,
            "submissions": submissions,
            "accepted": accepted,
            "rejections": rejections,
            "stop": stop,
        }

    def after_agent(state: AgentState) -> str:
        if state["stop"] is not None:
            return END
        return "validate" if _submits(_last_ai(state)) else "tools"

    def after_validate(state: AgentState) -> str:
        return END if state["stop"] is not None else "agent"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_node("validate", validate)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", "validate", END])
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("validate", after_validate, ["agent", END])
    return graph.compile()


def _last_ai(state: AgentState) -> AIMessage:
    message = state["messages"][-1]
    assert isinstance(message, AIMessage)
    return message


def _submits(message: AIMessage) -> bool:
    return any(call["name"] == SUBMIT for call in message.tool_calls)


def _answer(call: ToolCall, content: str) -> ToolMessage:
    # Every tool call needs its answer, or the next request to the model is invalid.
    return ToolMessage(content=content, tool_call_id=call["id"] or "", name=call["name"])


def _json(**content: Any) -> str:
    return json.dumps(content, ensure_ascii=False)


def _error_of(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except ValueError:
        return {}
    return {"error": parsed["error"]} if isinstance(parsed, dict) and "error" in parsed else {}
