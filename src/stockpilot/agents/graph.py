"""The agent as a LangGraph graph: the model asks for data, then submits proposals
that the validator accepts or rejects. Rejected ones go back to the model once.
Accepted ones are pending orders: the graph pauses until people decide on them,
and if they reject one, the model may propose a replacement, once.

    START -> agent -> tools -> agent -> ... -> validate -> await_decisions -> END
                                                 |              |
                                 agent (one retry)              agent (replacements)

The graph knows nothing about the database: it receives the tools, a `propose`
function and a checkpointer. That is what lets tests run it with a fake model.
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
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field, ValidationError

from stockpilot.agents.prompts import decisions_feedback
from stockpilot.domain.transfers import InvalidTransferError, TransferOrderStatus
from stockpilot.schemas.agent_runs import MAX_PROPOSALS_PER_RUN
from stockpilot.schemas.transfers import TransferProposal

SUBMIT = "submit_transfer_proposals"
# The proposal allows the model one correction after the validator rejects it.
MAX_RETRIES = 1
# After people reject orders, the model may propose replacements this many times.
MAX_REPLACEMENT_ROUNDS = 1

# Creates a pending order and returns its id, or raises InvalidTransferError.
Propose = Callable[[TransferProposal], int]

Stop = Literal["answered", "submitted", "retries_exhausted", "step_limit", "decided"]


class SubmitArgs(BaseModel):
    proposals: list[TransferProposal] = Field(
        min_length=1, max_length=MAX_PROPOSALS_PER_RUN, description="Every transfer, at once"
    )


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    # All the model calls of the run, and those of the current round: the first
    # proposal and the replacements each get max_model_calls.
    model_calls: int
    round_calls: int
    input_tokens: int
    output_tokens: int
    # Times the validator rejected something. Each one costs a retry.
    rejections: int
    # Orders created, and how many of them people rejected: those free their place
    # in max_proposals, so a replacement can take it.
    accepted: int
    declined: int
    replacements: int
    # Orders created since the last decisions: what the graph waits for.
    waiting_for: list[int]
    # Appended by each node, for the audit in agent_runs.
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
    submissions: Annotated[list[dict[str, Any]], operator.add]
    decisions: Annotated[list[dict[str, Any]], operator.add]
    # Set by the node that decides the run is over.
    stop: Stop | None


def initial_state(task: str) -> AgentState:
    return AgentState(
        messages=[HumanMessage(task)],
        model_calls=0,
        round_calls=0,
        input_tokens=0,
        output_tokens=0,
        rejections=0,
        accepted=0,
        declined=0,
        replacements=0,
        waiting_for=[],
        tool_calls=[],
        submissions=[],
        decisions=[],
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
    model: Callable[[], BaseChatModel],
    tools: Sequence[BaseTool],
    propose: Propose,
    *,
    system_prompt: str,
    limits: AgentLimits,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """`model` is called only when the graph needs it: a run resumed after every
    order was approved ends without asking the model anything."""
    by_name = {tool.name: tool for tool in tools}
    bound: list[Runnable[Any, Any]] = []

    def model_with_tools() -> Runnable[Any, Any]:
        if not bound:
            bound.append(model().bind_tools([*tools, submit_tool()]))
        return bound[0]

    def agent(state: AgentState) -> dict[str, Any]:
        response = model_with_tools().invoke([SystemMessage(system_prompt), *state["messages"]])
        assert isinstance(response, AIMessage)
        usage = response.usage_metadata or {"input_tokens": 0, "output_tokens": 0}
        calls = state["round_calls"] + 1
        stop: Stop | None = None
        if not response.tool_calls:
            stop = "answered"
        elif calls >= limits.max_model_calls and not _submits(response):
            # Out of calls and still asking for data: it would never get to propose.
            stop = "step_limit"
        return {
            "messages": [response],
            "model_calls": state["model_calls"] + 1,
            "round_calls": calls,
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
        created: list[int] = []
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
                if accepted - state["declined"] >= limits.max_proposals:
                    result["skipped"] = f"Limit of {limits.max_proposals} proposals reached"
                else:
                    try:
                        result["order_id"] = propose(proposal)
                        created.append(result["order_id"])
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
            out_of_calls = state["round_calls"] >= limits.max_model_calls
            stop = "retries_exhausted" if rejections > MAX_RETRIES or out_of_calls else None
        return {
            "messages": answers,
            "submissions": submissions,
            "accepted": accepted,
            "rejections": rejections,
            "waiting_for": state["waiting_for"] + created,
            "stop": stop,
        }

    def await_decisions(state: AgentState) -> dict[str, Any]:
        # Pauses here: the checkpointer keeps the state until the run is resumed
        # with the decisions, maybe hours later and in another process.
        decided = {d["order_id"]: d for d in interrupt({"order_ids": state["waiting_for"]})}
        missing = set(state["waiting_for"]) - decided.keys()
        if missing:
            raise ValueError(f"Resumed before every order was decided: {sorted(missing)}")
        decisions = [decided[order_id] for order_id in state["waiting_for"]]
        rejected = [d for d in decisions if d["status"] == TransferOrderStatus.REJECTED]
        update: dict[str, Any] = {"decisions": decisions, "waiting_for": [], "stop": "decided"}
        if rejected and state["replacements"] < MAX_REPLACEMENT_ROUNDS:
            update |= {
                "round_calls": 0,
                "messages": [HumanMessage(decisions_feedback(decisions))],
                "declined": state["declined"] + len(rejected),
                "replacements": state["replacements"] + 1,
                "rejections": 0,  # the replacement gets its own correction
                "stop": None,
            }
        return update

    def finish(state: AgentState) -> str:
        # Before ending, wait for the orders this run created.
        return "await_decisions" if state["waiting_for"] else END

    def after_agent(state: AgentState) -> str:
        if state["stop"] is not None:
            return finish(state)
        return "validate" if _submits(_last_ai(state)) else "tools"

    def after_validate(state: AgentState) -> str:
        return finish(state) if state["stop"] is not None else "agent"

    def after_decisions(state: AgentState) -> str:
        return END if state["stop"] is not None else "agent"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_node("validate", validate)
    graph.add_node("await_decisions", await_decisions)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", "validate", "await_decisions", END])
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("validate", after_validate, ["agent", "await_decisions", END])
    graph.add_conditional_edges("await_decisions", after_decisions, ["agent", END])
    return graph.compile(checkpointer=checkpointer)


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
