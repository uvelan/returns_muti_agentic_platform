"""Durable Temporal wrapper for Order Discovery conversations.

Owns no business state -- the conversation document and the LangGraph
checkpoint (both Mongo-backed, see `dynamic_knowledge/order_agent/coordinator.py`)
are the durable record of everything that matters. This workflow exists only
so one conversation's turns are serialized through a single durable mutex and
so `platform/reasoning/abandonment.py`'s "active Temporal workflow"
precondition has a real workflow execution to find (via the `workflow_id`
`coordinator.process_turn` now threads through to `reasoning_runs`).

Mirrors `workflows/return_workflow.py`'s established shape (dataclass I/O,
`wait_condition`-based mutual exclusion around `execute_activity`, a
`@workflow.query` for read-only observability) as closely as the two
workflows' different natures allow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy

__all__ = [
    "AgentTurnResultPayload",
    "GraphGenerationChangedNotice",
    "OrderDiscoveryTurnError",
    "OrderDiscoveryTurnOutcome",
    "OrderDiscoveryWorkflow",
    "OrderDiscoveryWorkflowInput",
    "OrderDiscoveryWorkflowState",
    "RunOrderDiscoveryTurnActivityInput",
    "SubmitOrderDiscoveryTurnCommand",
]

# Worst case: max_reasoning_steps (schema.py, up to 32) each potentially invoking the
# model gateway once, each bounded by ai_global_timeout_seconds (settings.py, up to
# 900s), plus Neo4j/Mongo margin. Deliberately not ReturnWorkflow's 10-second constant
# -- that workflow's activities are persistence-only; this one drives a live,
# multi-step AI reasoning loop.
_TURN_ACTIVITY_TIMEOUT: Final = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class OrderDiscoveryWorkflowInput:
    """Business-data-free input for one Order Discovery workflow execution."""

    conversation_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class SubmitOrderDiscoveryTurnCommand:
    """One turn submission. conversation_id/agent_id are deliberately absent --
    they are already the workflow's own identity, so a caller cannot submit a
    turn against a conversation_id mismatched from the workflow it addressed."""

    expected_conversation_version: int
    client_turn_id: str
    idempotency_key: str
    message_id: str
    message: str
    principal_id: str
    tenant_id: str
    roles: frozenset[str]
    branch_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class RunOrderDiscoveryTurnActivityInput:
    """Everything `run_order_discovery_turn` needs to reconstruct an
    AgentTurnRequest + GuardContext without ever carrying the full
    ActiveSchema (already held by the Activity) across the Temporal boundary."""

    conversation_id: str
    agent_id: str
    expected_conversation_version: int
    client_turn_id: str
    idempotency_key: str
    message_id: str
    message: str
    principal_id: str
    tenant_id: str
    roles: frozenset[str]
    branch_ids: frozenset[str]
    workflow_id: str


@dataclass(frozen=True, slots=True)
class AgentTurnResultPayload:
    """Carries the real AgentTurnResult (a pydantic model) across the Temporal
    boundary as a JSON string rather than configuring a pydantic data
    converter on the shared Temporal Client every other workflow in this
    codebase also uses -- see order_discovery_activities.py for the
    encode/decode side of this."""

    conversation_id: str
    conversation_version: int
    client_turn_id: str
    agent_turn_result_json: str


@dataclass(frozen=True, slots=True)
class OrderDiscoveryTurnError:
    """Mirrors OrderAgentFailure's exact code/message/retryable shape."""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class OrderDiscoveryTurnOutcome:
    """Exactly one of (result, error) is non-None -- enforced by the Activity,
    not re-validated here since the workflow never interprets this payload,
    only passes it through to the update's caller."""

    result: AgentTurnResultPayload | None
    error: OrderDiscoveryTurnError | None


@dataclass(frozen=True, slots=True)
class GraphGenerationChangedNotice:
    new_graph_generation_id: str
    changed_at: str


@dataclass(frozen=True, slots=True)
class OrderDiscoveryWorkflowState:
    conversation_id: str
    agent_id: str
    turn_in_progress: bool
    last_known_graph_generation_id: str | None


@workflow.defn(name="return-platform-order-discovery-v1")
class OrderDiscoveryWorkflow:
    """One durable workflow execution per Order Discovery conversation."""

    def __init__(self) -> None:
        self._conversation_id: str | None = None
        self._agent_id: str | None = None
        self._turn_in_progress = False
        self._last_known_graph_generation_id: str | None = None

    def _require_conversation_id(self) -> str:
        if self._conversation_id is None:
            raise RuntimeError("OrderDiscoveryWorkflow has not started")
        return self._conversation_id

    def _require_agent_id(self) -> str:
        if self._agent_id is None:
            raise RuntimeError("OrderDiscoveryWorkflow has not started")
        return self._agent_id

    @workflow.run
    async def run(self, workflow_input: OrderDiscoveryWorkflowInput) -> None:
        """A conversation has no defined end the way a Return's stage
        sequence reaches COMPLETED -- an associate can resume it days later --
        so this workflow waits forever for submit_turn updates/
        generation_changed signals rather than reaching a terminal state."""
        self._conversation_id = workflow_input.conversation_id
        self._agent_id = workflow_input.agent_id
        await workflow.wait_condition(lambda: False)

    @workflow.query(name="execution_state")
    def execution_state(self) -> OrderDiscoveryWorkflowState:
        """Read-only coordination state for observability."""
        return OrderDiscoveryWorkflowState(
            conversation_id=self._require_conversation_id(),
            agent_id=self._require_agent_id(),
            turn_in_progress=self._turn_in_progress,
            last_known_graph_generation_id=self._last_known_graph_generation_id,
        )

    @workflow.signal(name="generation_changed")
    def generation_changed(self, notice: GraphGenerationChangedNotice) -> None:
        """Observability only in this commit. A mid-turn generation change is
        already safe without any handling here: Neo4j's own generation
        fencing rejects a write against a stale fencing token
        (GenerationFencingError), and HallucinationGuard rejects response
        evidence gathered under a mismatched graph_generation_id -- so an
        in-flight turn either completes safely against the generation it
        started with, or is safely rejected by guards already in place. This
        signal records the new generation for `execution_state` to surface;
        it deliberately does not gate or short-circuit submit_turn. It is a
        real, working mechanism with no real emitter yet -- matching how
        CLARIFY/REPLAN were introduced in Commit 2 -- so a future caller can
        wire an early-exit optimization (skip starting an already-doomed
        queued turn) on top of this without any workflow topology change.
        """
        self._last_known_graph_generation_id = notice.new_graph_generation_id

    @workflow.update(name="submit_turn")
    async def submit_turn(
        self, command: SubmitOrderDiscoveryTurnCommand
    ) -> OrderDiscoveryTurnOutcome:
        """Serializes every turn for this conversation through one mutex --
        two concurrent submissions can never race each other's
        expected_conversation_version.

        `workflow.wait_condition` always registers a future and is resolved
        later in a single batch pass over every pending condition (see
        `_WorkflowInstanceImpl._run_once`/`_conditions` in the SDK) -- it
        never short-circuits synchronously even when the condition already
        holds. That means two concurrent `submit_turn` calls admitted into
        the same workflow task can both observe `not self._turn_in_progress`
        as true in the same batch (neither has mutated it yet) and both get
        released together; the first one to actually run then flips the
        flag, but the second's *resumption* does not re-observe that -- it
        would otherwise plough on regardless. The `while` re-check below is
        the fix: each release re-validates the guard before committing to
        it, so a waiter that lost the race loops back and registers a fresh
        wait instead of proceeding. A bare `if`/`await` here (the naive
        translation of ReturnWorkflow.complete_stage's identical-looking
        guard) is not actually safe against this and was caught by a
        concurrent-update test against a real Temporal server.
        """
        while self._conversation_id is None or self._turn_in_progress:
            await workflow.wait_condition(
                lambda: self._conversation_id is not None and not self._turn_in_progress
            )
        self._turn_in_progress = True
        try:
            outcome: OrderDiscoveryTurnOutcome = await workflow.execute_activity(
                "run_order_discovery_turn",
                RunOrderDiscoveryTurnActivityInput(
                    conversation_id=self._require_conversation_id(),
                    agent_id=self._require_agent_id(),
                    expected_conversation_version=command.expected_conversation_version,
                    client_turn_id=command.client_turn_id,
                    idempotency_key=command.idempotency_key,
                    message_id=command.message_id,
                    message=command.message,
                    principal_id=command.principal_id,
                    tenant_id=command.tenant_id,
                    roles=command.roles,
                    branch_ids=command.branch_ids,
                    workflow_id=workflow.info().workflow_id,
                ),
                result_type=OrderDiscoveryTurnOutcome,
                start_to_close_timeout=_TURN_ACTIVITY_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            return outcome
        finally:
            self._turn_in_progress = False
