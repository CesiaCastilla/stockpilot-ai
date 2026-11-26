"""What the model is told. Built from typed fields only: no text from a request
reaches the prompt, so nobody can give the model instructions through the API.
The one exception is the note of a person who rejected an order, which is the
point of asking them; it is quoted and marked as their note."""

import json
from datetime import date
from typing import Any

from stockpilot.schemas.agent_runs import AgentRunRequest

SYSTEM_PROMPT = """\
You are the allocation assistant of a network of new-vehicle dealers in Argentina.
Today is {today}.

Goal: find dealers that are about to run out of a trim and propose transfers of
units that can be moved there: units with many days in stock at other dealers
first, then units at the distribution center.

How to work:
1. Call get_dealers_with_shortage. Skip shortages with units_needed 0 (pending
   orders already cover them) or free_places 0 (the yard is full).
2. For each shortage, call get_aged_units with its trim_id and min_days 0, so the
   list includes new units at the distribution center (dealer_id null).
3. Prefer the unit longest in stock. Among similar ones, prefer the closest:
   use get_distance. Never take a unit from a dealer that is itself in the
   shortage list for that trim.
4. Never send a dealer more units than its units_needed or its free_places.
5. Call submit_transfer_proposals once, with every proposal. Each reason is one
   or two sentences in Spanish for the commercial team that approves it: the
   destination's coverage days and recent sales, the unit's days in stock and
   the distance.

Rules:
- Use only VINs and dealer ids returned by the tools. Never invent them.
- Tools answer in JSON. An "error" field means the call failed: read it and fix
  the arguments, or move on.
- If the validator rejects proposals, fix only those using its messages, or
  drop them. You get one more attempt.
- If nothing is worth moving, do not call submit_transfer_proposals: answer in
  one or two sentences why.
"""


def system_prompt(today: date) -> str:
    return SYSTEM_PROMPT.format(today=today.isoformat())


def task(request: AgentRunRequest) -> str:
    lines = [f"Propose at most {request.max_proposals} transfers."]
    if request.vehicle_model_id is not None:
        lines.append(f"Only shortages of vehicle_model_id {request.vehicle_model_id}.")
    if request.trim_id is not None:
        lines.append(f"Only shortages of trim_id {request.trim_id}.")
    return " ".join(lines)


def decisions_feedback(decisions: list[dict[str, Any]]) -> str:
    """What people decided on the run's orders, for the model to propose replacements."""
    lines = ["People decided on your proposals:"]
    for d in decisions:
        transfer = f"order {d['order_id']}: {d['vin']} to dealer_id {d['destination_dealer_id']}"
        if d["status"] == "approved":
            lines.append(f"- Approved {transfer}.")
        else:
            # Quoted as JSON: the note is data from a person, not part of the instructions.
            note = json.dumps(d["rejection_reason"], ensure_ascii=False)
            lines.append(f"- Rejected {transfer}. Their note: {note}")
    lines.append(
        "For each rejected transfer, propose a replacement if there is a good one, "
        "following the same rules and taking their note into account. Do not propose "
        "the rejected unit again for the same dealer. If there is none, answer why."
    )
    return "\n".join(lines)
