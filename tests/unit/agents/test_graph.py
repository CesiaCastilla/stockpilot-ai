"""The graph with a scripted model, fake tools and a fake validator: no database, no network."""

import json
from itertools import count
from typing import Any, cast

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from stockpilot.agents.graph import (
    MAX_REPLACEMENT_ROUNDS,
    MAX_RETRIES,
    SUBMIT,
    AgentLimits,
    AgentState,
    build_graph,
    initial_state,
)
from stockpilot.domain.transfers import InvalidTransferError, TransferRule, Violation
from stockpilot.schemas.transfers import TransferProposal
from tests.fakes import ScriptedChatModel, calls, says, scripted

GOOD = "8AFZZZ12345670001"
ALSO_GOOD = "8AFZZZ12345670002"
INVENTED = "8AFZZZ12345679999"
LIMITS = AgentLimits(max_proposals=5, max_model_calls=6)


def _proposal(vin: str, dealer_id: int = 7) -> dict[str, Any]:
    return {"vin": vin, "destination_dealer_id": dealer_id, "reason": "Faltante"}


def _submit(*vins: str) -> tuple[str, dict[str, Any]]:
    return SUBMIT, {"proposals": [_proposal(vin) for vin in vins]}


class FakeValidator:
    """Accepts known VINs once, like the real one: a second order for a unit is rejected."""

    def __init__(self) -> None:
        self.orders: list[TransferProposal] = []
        self.ids = count(101)

    def __call__(self, proposal: TransferProposal) -> int:
        if proposal.vin not in {GOOD, ALSO_GOOD} or proposal in self.orders:
            raise InvalidTransferError(
                [Violation(TransferRule.UNIT_EXISTS, f"Unit {proposal.vin} does not exist")]
            )
        self.orders.append(proposal)
        return next(self.ids)


def _tools() -> list[BaseTool]:
    def get_dealers_with_shortage(limit: int = 20) -> str:
        """Shortages."""
        return json.dumps({"items": [{"dealer_id": 7, "trim_id": 3}]})

    def get_distance(origin_dealer_id: int | None, destination_dealer_id: int | None) -> str:
        """Distance."""
        return json.dumps({"error": "Dealer 99 not found"})

    def plain_text() -> str:
        """A tool that does not answer JSON."""
        return "just text"

    def broken() -> str:
        """A tool with a bug."""
        raise RuntimeError("bug in a tool")

    return [
        StructuredTool.from_function(f)
        for f in (get_dealers_with_shortage, get_distance, plain_text, broken)
    ]


class Run:
    """One graph run, kept by an in-memory checkpointer between pauses."""

    def __init__(
        self,
        model: ScriptedChatModel,
        validator: FakeValidator | None = None,
        limits: AgentLimits = LIMITS,
        checkpointer: InMemorySaver | None = None,
    ) -> None:
        self.model_requests = 0

        def model_factory() -> ScriptedChatModel:
            self.model_requests += 1
            return model

        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = build_graph(
            model_factory,
            _tools(),
            validator or FakeValidator(),
            system_prompt="You allocate units.",
            limits=limits,
            checkpointer=self.checkpointer,
        )
        self.config: RunnableConfig = {"configurable": {"thread_id": "run-1"}}

    def start(self) -> AgentState:
        self.graph.invoke(initial_state("Propose at most 5 transfers."), self.config)
        return self.state

    def decide(self, *decisions: dict[str, Any]) -> AgentState:
        self.graph.invoke(Command(resume=list(decisions)), self.config)
        return self.state

    @property
    def state(self) -> AgentState:
        return cast(AgentState, self.graph.get_state(self.config).values)

    @property
    def waiting_for(self) -> list[int] | None:
        """The orders it waits for, or None if it is not paused."""
        snapshot = self.graph.get_state(self.config)
        if not snapshot.next:
            return None
        value: dict[str, list[int]] = snapshot.interrupts[0].value
        return value["order_ids"]


def _run(
    model: ScriptedChatModel,
    validator: FakeValidator | None = None,
    limits: AgentLimits = LIMITS,
) -> AgentState:
    return Run(model, validator, limits).start()


def _decision(order_id: int, vin: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "vin": vin,
        "destination_dealer_id": 7,
        "status": "approved" if reason is None else "rejected",
        "rejection_reason": reason,
    }


def _tool_answer(model: ScriptedChatModel, call: int) -> Any:
    """What the tools answered, as the model saw it on its `call`-th request."""
    message = model.received[call][-1]
    assert isinstance(message, ToolMessage)
    return json.loads(str(message.content))


# --- The usual path -----------------------------------------------------------


def test_asks_for_data_then_proposes() -> None:
    model = scripted(
        calls(("get_dealers_with_shortage", {"limit": 5}), tokens=(100, 10)),
        calls(_submit(GOOD, ALSO_GOOD), tokens=(200, 30)),
    )

    state = _run(model)

    assert state["stop"] == "submitted"
    assert state["accepted"] == 2
    assert [(s["vin"], s["order_id"]) for s in state["submissions"]] == [
        (GOOD, 101),
        (ALSO_GOOD, 102),
    ]
    assert state["tool_calls"] == [{"name": "get_dealers_with_shortage", "args": {"limit": 5}}]
    assert (state["model_calls"], state["input_tokens"], state["output_tokens"]) == (2, 300, 40)
    # The model saw the tool's answer before proposing.
    assert _tool_answer(model, 1) == {"items": [{"dealer_id": 7, "trim_id": 3}]}


def test_the_model_gets_the_system_prompt_the_task_and_every_tool() -> None:
    model = scripted(says("Nothing to move."))

    _run(model)

    [first_request] = model.received
    assert first_request[0] == SystemMessage("You allocate units.")
    assert first_request[1].content == "Propose at most 5 transfers."
    assert model.offered == [
        "get_dealers_with_shortage",
        "get_distance",
        "plain_text",
        "broken",
        SUBMIT,
    ]


def test_an_answer_without_tools_ends_the_run() -> None:
    state = _run(scripted(says("Nothing to move: every yard is covered.")))

    assert (state["stop"], state["accepted"], state["submissions"]) == ("answered", 0, [])


# --- Validator and retry ------------------------------------------------------


def test_rejected_proposals_go_back_once_and_valid_ones_stay() -> None:
    model = scripted(calls(_submit(GOOD, INVENTED)), calls(_submit(ALSO_GOOD)))

    state = _run(model)

    assert state["stop"] == "submitted"
    assert (state["accepted"], state["rejections"]) == (2, 1)
    first, rejected, retried = state["submissions"]
    assert (first["order_id"], first["attempt"]) == (101, 1)
    assert rejected["violations"] == [
        {"rule": "unit_exists", "message": f"Unit {INVENTED} does not exist"}
    ]
    assert (retried["order_id"], retried["attempt"]) == (102, 2)
    # What the model read before its second attempt.
    answer = _tool_answer(model, 1)
    assert [r.get("order_id") for r in answer["results"]] == [101, None]
    assert answer["results"][1]["violations"][0]["rule"] == "unit_exists"


def test_a_second_rejection_ends_the_run() -> None:
    assert MAX_RETRIES == 1
    model = scripted(calls(_submit(INVENTED)), calls(_submit(INVENTED)), says("never asked"))

    state = _run(model)

    assert state["stop"] == "retries_exhausted"
    assert (state["accepted"], state["rejections"], state["model_calls"]) == (0, 2, 2)


def test_proposing_the_same_unit_twice_is_a_rejection() -> None:
    state = _run(scripted(calls(_submit(GOOD, GOOD)), calls(_submit(ALSO_GOOD))))

    assert [s.get("order_id") for s in state["submissions"]] == [101, None, 102]


@pytest.mark.parametrize(
    "arguments",
    [
        {"proposals": []},
        {"proposals": [{"vin": GOOD, "destination_dealer_id": 7}]},  # no reason
        {"proposals": [{**_proposal(GOOD), "origin_dealer_id": 3}]},  # no such field
        {"proposals": [_proposal(GOOD, dealer_id=2**31)]},
        {"wrong": "shape"},
    ],
    ids=["empty", "no-reason", "extra-field", "huge-id", "wrong-shape"],
)
def test_malformed_proposals_are_explained_and_cost_the_retry(arguments: dict[str, Any]) -> None:
    model = scripted(calls((SUBMIT, arguments)), calls(_submit(GOOD)))

    state = _run(model)

    assert "Invalid proposals" in _tool_answer(model, 1)["error"]
    assert (state["stop"], state["accepted"], state["rejections"]) == ("submitted", 1, 1)
    assert "error" in state["submissions"][0]


def test_no_more_than_max_proposals_become_orders() -> None:
    limits = AgentLimits(max_proposals=1, max_model_calls=6)

    state = _run(scripted(calls(_submit(GOOD, ALSO_GOOD))), limits=limits)

    assert state["accepted"] == 1
    assert state["submissions"][1]["skipped"] == "Limit of 1 proposals reached"
    assert "order_id" not in state["submissions"][1]


def test_other_tools_called_with_submit_are_not_run() -> None:
    model = scripted(calls(("get_dealers_with_shortage", {}), _submit(GOOD)))

    state = _run(model)

    assert state["accepted"] == 1
    assert state["tool_calls"] == []  # the data tool did not run
    answers = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert "Not run" in str(answers[0].content)
    assert len(answers) == 2  # every call got an answer


# --- Limits and errors --------------------------------------------------------


def test_a_model_that_never_proposes_is_stopped() -> None:
    limits = AgentLimits(max_proposals=5, max_model_calls=3)
    model = scripted(*(calls(("get_dealers_with_shortage", {})) for _ in range(10)))

    state = _run(model, limits=limits)

    assert state["stop"] == "step_limit"
    assert state["model_calls"] == 3
    assert len(state["tool_calls"]) == 2  # the third request was not run


def test_the_last_call_can_still_propose() -> None:
    limits = AgentLimits(max_proposals=5, max_model_calls=2)

    state = _run(
        scripted(calls(("get_dealers_with_shortage", {})), calls(_submit(GOOD))), limits=limits
    )

    assert (state["stop"], state["accepted"]) == ("submitted", 1)


def test_no_retry_without_model_calls_left() -> None:
    limits = AgentLimits(max_proposals=5, max_model_calls=2)
    model = scripted(
        calls(("get_dealers_with_shortage", {})), calls(_submit(INVENTED)), says("never")
    )

    state = _run(model, limits=limits)

    assert (state["stop"], state["model_calls"]) == ("retries_exhausted", 2)


def test_a_made_up_tool_gets_the_list_of_real_ones() -> None:
    model = scripted(calls(("get_all_the_stock", {})), says("Sorry."))

    state = _run(model)

    error = _tool_answer(model, 1)["error"]
    assert "Unknown tool get_all_the_stock" in error
    assert "get_distance" in error
    assert state["tool_calls"] == [{"name": "get_all_the_stock", "args": {}, "error": error}]


def test_tool_errors_are_recorded() -> None:
    args = {"origin_dealer_id": 1, "destination_dealer_id": 99}

    state = _run(scripted(calls(("get_distance", args)), says("Done.")))

    assert state["tool_calls"] == [
        {"name": "get_distance", "args": args, "error": "Dealer 99 not found"}
    ]


def test_a_bug_in_a_tool_stops_the_run() -> None:
    with pytest.raises(RuntimeError, match="bug in a tool"):
        _run(scripted(calls(("broken", {}))))


def test_a_tool_that_does_not_answer_json_is_not_an_error() -> None:
    model = scripted(calls(("plain_text", {})), says("Done."))

    state = _run(model)

    assert state["tool_calls"] == [{"name": "plain_text", "args": {}}]
    assert model.received[1][-1].content == "just text"


# --- Waiting for people -------------------------------------------------------


def test_pauses_until_people_decide_on_every_order() -> None:
    run = Run(scripted(calls(_submit(GOOD, ALSO_GOOD))))

    run.start()

    assert run.waiting_for == [101, 102]
    state = run.decide(_decision(101, GOOD), _decision(102, ALSO_GOOD))
    assert run.waiting_for is None
    assert state["stop"] == "decided"
    assert [d["status"] for d in state["decisions"]] == ["approved", "approved"]
    assert state["model_calls"] == 1  # approving needs nothing from the model


def test_after_a_restart_approvals_need_no_model() -> None:
    first = Run(scripted(calls(_submit(GOOD))))
    first.start()

    # Another process: a new graph over the same checkpointer, and no model at all.
    later = Run(scripted(), checkpointer=first.checkpointer)
    state = later.decide(_decision(101, GOOD))

    assert state["stop"] == "decided"
    assert later.model_requests == 0


def test_orders_from_the_retry_are_waited_for_too() -> None:
    run = Run(scripted(calls(_submit(GOOD, INVENTED)), calls(_submit(ALSO_GOOD))))

    run.start()

    assert run.waiting_for == [101, 102]


def test_a_run_without_orders_does_not_pause() -> None:
    run = Run(scripted(says("Nothing to move.")))

    run.start()

    assert run.waiting_for is None


def test_a_rejection_lets_the_model_propose_a_replacement() -> None:
    model = scripted(calls(_submit(GOOD)), calls(_submit(ALSO_GOOD)))
    run = Run(model, limits=AgentLimits(max_proposals=1, max_model_calls=6))
    run.start()

    run.decide(_decision(101, GOOD, reason="Promised to a customer"))

    # The model read the note and replaced the order, within the same limit of 1.
    feedback = model.received[1][-1]
    assert isinstance(feedback, HumanMessage)
    assert f"Rejected order 101: {GOOD} to dealer_id 7" in str(feedback.content)
    assert 'Their note: "Promised to a customer"' in str(feedback.content)
    assert run.waiting_for == [102]
    state = run.decide(_decision(102, ALSO_GOOD))
    assert (state["stop"], state["accepted"], state["declined"]) == ("decided", 2, 1)


def test_only_one_round_of_replacements() -> None:
    assert MAX_REPLACEMENT_ROUNDS == 1
    model = scripted(calls(_submit(GOOD)), calls(_submit(ALSO_GOOD)), says("never asked"))
    run = Run(model)
    run.start()
    run.decide(_decision(101, GOOD, reason="No"))

    state = run.decide(_decision(102, ALSO_GOOD, reason="No again"))

    assert (state["stop"], state["model_calls"]) == ("decided", 2)


def test_the_model_may_find_no_replacement() -> None:
    run = Run(scripted(calls(_submit(GOOD)), says("No other unit of that trim.")))
    run.start()

    state = run.decide(_decision(101, GOOD, reason="No"))

    assert state["stop"] == "answered"
    assert run.waiting_for is None


def test_a_replacement_gets_its_own_correction() -> None:
    model = scripted(
        calls(_submit(GOOD, INVENTED)),  # one rejected by the validator: the retry is used
        calls(_submit(ALSO_GOOD)),
        calls(_submit(INVENTED)),  # replacement, rejected by the validator
        calls(_submit(GOOD)),  # its correction: GOOD was rejected by people, so free again
    )
    validator = FakeValidator()
    run = Run(model, validator)
    run.start()
    validator.orders.remove(TransferProposal.model_validate(_proposal(GOOD)))

    run.decide(_decision(101, GOOD, reason="No"), _decision(102, ALSO_GOOD))

    assert run.waiting_for == [103]


def test_the_replacement_round_has_its_own_budget() -> None:
    # The first proposal used the whole budget of 2 model calls.
    limits = AgentLimits(max_proposals=5, max_model_calls=2)
    data: tuple[str, dict[str, Any]] = ("get_dealers_with_shortage", {})
    model = scripted(calls(data), calls(_submit(GOOD)), calls(data), calls(_submit(ALSO_GOOD)))
    run = Run(model, limits=limits)
    run.start()

    state = run.decide(_decision(101, GOOD, reason="No"))

    assert run.waiting_for == [102]
    assert (state["model_calls"], state["round_calls"]) == (4, 2)


def test_the_replacement_round_also_stops_at_the_limit() -> None:
    limits = AgentLimits(max_proposals=5, max_model_calls=2)
    data: tuple[str, dict[str, Any]] = ("get_dealers_with_shortage", {})
    model = scripted(calls(_submit(GOOD)), *(calls(data) for _ in range(5)))
    run = Run(model, limits=limits)
    run.start()

    state = run.decide(_decision(101, GOOD, reason="No"))

    assert (state["stop"], state["model_calls"]) == ("step_limit", 3)


def test_resuming_before_every_order_is_decided_is_a_bug() -> None:
    run = Run(scripted(calls(_submit(GOOD, ALSO_GOOD))))
    run.start()

    with pytest.raises(ValueError, match=r"before every order was decided: \[102\]"):
        run.decide(_decision(101, GOOD))


def test_orders_from_the_first_attempt_are_waited_for_even_if_the_model_gives_up() -> None:
    # One accepted, one rejected; instead of correcting it, the model answers.
    run = Run(scripted(calls(_submit(GOOD, INVENTED)), says("I cannot fix that one.")))

    state = run.start()

    assert state["stop"] == "answered"
    assert run.waiting_for == [101]


def test_the_model_is_created_once_per_run() -> None:
    run = Run(scripted(calls(("get_dealers_with_shortage", {})), calls(_submit(GOOD))))

    run.start()

    assert run.model_requests == 1


def test_the_replacement_can_be_corrected_within_its_own_budget() -> None:
    limits = AgentLimits(max_proposals=5, max_model_calls=2)
    model = scripted(calls(_submit(GOOD)), calls(_submit(INVENTED)), calls(_submit(ALSO_GOOD)))
    run = Run(model, limits=limits)
    run.start()

    # Two calls in the run when the validator rejects the replacement, one in its round.
    run.decide(_decision(101, GOOD, reason="No"))

    assert run.waiting_for == [102]
