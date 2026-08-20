"""Which stage of the discovery conversation a turn is in, and whose prompt it gets.

**Why a turn has a stage at all.** The reasoning prompt reached 17,109
characters, and adherence degrades visibly at that size across the standard-tier
models this deployment routes to: an associate sent "Confirm the customer <name>
on account <account>" and the agent asked which location, twice, with the rule
that forbids exactly that present, correct and delivered. A rule the model does
not follow is indistinguishable from a rule that is not there, and the remaining
lever is sending fewer of them per turn.

Most of that prompt does not apply to most turns. Nothing can be narrowed before
a search has run; a page cannot be advanced when every match is already on the
table; the source-system escalation is only reachable when a search came back
empty. Those are not shades of one situation -- they are distinct states of the
conversation, each already recorded in the turn's own context, and each with a
different set of actions the agent can legitimately take.

**Read off state, never inferred from the message.** Every signal here is
written by the graph itself: `order_search_cache` by `make_order_search_node`,
`case_id` by `make_confirm_order_node`, and `REPLAN` clears the cache, which is
what puts a restarted conversation back at the beginning. Classifying on what
the associate *said* would need a model call to choose a prompt for a model
call, and would put untrusted text in charge of which rules apply to it.

**Stage selection is advisory.** `stage_task_id` names a task that may not exist
in the active configuration -- `runtime-configuration-init` in compose.yaml
still publishes with `--if-missing`, so a container deployment can be running a
release cut before these tasks existed. `ORDER_AGENT_REASONING_V1` remains the
complete prompt and every stage prompt is a subset of it, so an unrecognised
stage task degrades to exactly today's behaviour rather than failing to start.
`model_gateway` owns that fallback; this module only says which prompt would be
best.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Any

__all__ = [
    "BASE_REASONING_TASK_ID",
    "STAGE_TASK_IDS",
    "ReasoningStage",
    "reasoning_stage",
    "stage_task_id",
]

#: The complete prompt: all twenty-one sections, and the fallback for every
#: stage. Never itself a stage's task -- a turn either gets a stage prompt or
#: gets this one.
BASE_REASONING_TASK_ID = "ORDER_AGENT_REASONING_V1"


class ReasoningStage(StrEnum):
    """The states a discovery conversation is actually in, per the graph's state.

    Exhaustive and mutually exclusive over `(case_id, orderSearchCache)`, which
    is the whole of what the graph records about progress. The names describe the
    *situation*, not the action to take in it: what to do about being here is the
    stage prompt's business.
    """

    #: No order search has run. There is nothing to narrow, no page to advance
    #: and no candidate to confirm -- the turn's job is to search on whatever the
    #: associate has given, or to ask for a first identifying detail.
    OPENING = "OPENING"
    #: A search returned several candidates and all of them are on the table.
    #: The turn's job is to ask the one question that best splits them.
    NARROWING = "NARROWING"
    #: A search matched more records than it returned. Everything NARROWING can
    #: do, plus the two things only this state permits: measuring past the page
    #: with an aggregate, and serving the next page from the cache.
    NARROWING_TRUNCATED = "NARROWING_TRUNCATED"
    #: A search over every signal held came back empty. Not a narrowing problem:
    #: the graph is a periodic projection, so the turn's job is the source-system
    #: escalation, or an honest account of what was searched.
    UNRESOLVED = "UNRESOLVED"
    #: One candidate stands, or an order is already confirmed and a case exists.
    #: The turn's job is to show what is on it and finish.
    COMPLETING = "COMPLETING"


#: One task id per stage. Separate ids rather than one task with a switch,
#: because a task id is what the platform already routes, rate-limits, budgets,
#: intercepts and reports on -- a stage that shared an id would be invisible in
#: every one of those views.
STAGE_TASK_IDS = MappingProxyType(
    {
        ReasoningStage.OPENING: "ORDER_AGENT_REASONING_OPENING_V1",
        ReasoningStage.NARROWING: "ORDER_AGENT_REASONING_NARROWING_V1",
        ReasoningStage.NARROWING_TRUNCATED: "ORDER_AGENT_REASONING_WIDE_V1",
        ReasoningStage.UNRESOLVED: "ORDER_AGENT_REASONING_UNRESOLVED_V1",
        ReasoningStage.COMPLETING: "ORDER_AGENT_REASONING_COMPLETING_V1",
    }
)


def reasoning_stage(
    *, case_id: str | None, conversation_state: dict[str, Any]
) -> ReasoningStage | None:
    """Which stage this turn is in, or `None` when the state cannot say.

    Takes the two fields it reads rather than an `AgentTurnContext`, so the
    classification can be tested against a state shape without constructing a
    turn -- and so this module does not import the contracts module that the
    gateway importing *it* already depends on.

    `None` is a real answer and not an error: it means the cache is present but
    not in the shape this function understands, which is what a conversation
    document written by a future release would look like. The caller sends the
    complete prompt in that case, which is always correct and merely larger.
    """
    # Confirmed first. A case exists only after `make_confirm_order_node` has
    # committed one, and once it has, the search cache underneath it is history:
    # the associate has an order and the turn is about what is coming back off
    # it.
    if case_id:
        return ReasoningStage.COMPLETING

    cache = conversation_state.get("orderSearchCache")
    if cache is None:
        # No search has run, or REPLAN cleared the cache -- which is the same
        # situation and deliberately reads as such.
        return ReasoningStage.OPENING
    if not isinstance(cache, dict):
        return None

    total_found = cache.get("totalFound")
    if not isinstance(total_found, int) or isinstance(total_found, bool):
        return None
    if total_found == 0:
        return ReasoningStage.UNRESOLVED
    if total_found == 1:
        return ReasoningStage.COMPLETING

    shown = cache.get("shown")
    if isinstance(shown, int) and not isinstance(shown, bool) and shown < total_found:
        return ReasoningStage.NARROWING_TRUNCATED
    return ReasoningStage.NARROWING


def stage_task_id(stage: ReasoningStage | None) -> str:
    """The task id for a stage, or the complete prompt when there is no stage."""
    if stage is None:
        return BASE_REASONING_TASK_ID
    return STAGE_TASK_IDS[stage]
