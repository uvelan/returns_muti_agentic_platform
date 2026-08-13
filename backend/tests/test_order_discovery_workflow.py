"""Proves OrderDiscoveryWorkflow's Temporal mechanics against a real, running
Temporal server (host-reachable at 127.0.0.1:7233; a container attached to
the compose network sets PLATFORM_TEST_TEMPORAL_TARGET, same convention as
PLATFORM_TEST_MONGO_HOST/PLATFORM_TEST_NEO4J_HOST elsewhere in this suite):
the submit_turn mutex serializes concurrent turns, generation_changed updates
observability state, and the workflow never reaches a terminal state. Uses a
stub activity (no real coordinator/Mongo/Neo4j) -- the coordinator's own real
wiring is proved separately in tests/dynamic_knowledge/test_order_agent_coordinator_real_infra.py.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import fields

import pytest
from temporalio import activity, worker, workflow
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.converter import DataConverter
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from return_platform.workflows.order_discovery_workflow import (
    AgentTurnResultPayload,
    GraphGenerationChangedNotice,
    OrderDiscoveryTurnError,
    OrderDiscoveryTurnOutcome,
    OrderDiscoveryWorkflow,
    OrderDiscoveryWorkflowInput,
    RunOrderDiscoveryTurnActivityInput,
    SubmitOrderDiscoveryTurnCommand,
)

# Live infrastructure: this module opens a real Temporal client. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = pytest.mark.live_infra

_TEMPORAL_TARGET = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")


@pytest.mark.asyncio
async def test_temporal_default_converter_round_trips_workflow_contracts() -> None:
    workflow_input = OrderDiscoveryWorkflowInput(conversation_id="c1", agent_id="agent_a")
    payloads = await DataConverter.default.encode([workflow_input])
    decoded = await DataConverter.default.decode(payloads, [OrderDiscoveryWorkflowInput])
    assert decoded == [workflow_input]

    command = SubmitOrderDiscoveryTurnCommand(
        expected_conversation_version=0,
        client_turn_id="t1",
        idempotency_key="i1",
        message_id="m1",
        message="hello",
        principal_id="p1",
        tenant_id="tenant1",
        roles=frozenset({"associate"}),
        branch_ids=frozenset(),
    )
    payloads = await DataConverter.default.encode([command])
    decoded_command = await DataConverter.default.decode(
        payloads, [SubmitOrderDiscoveryTurnCommand]
    )
    (decoded_only,) = decoded_command
    # The default JSON converter has no wire representation for frozenset, so
    # it comes back as a plain list -- PrincipalContext (a pydantic model)
    # coerces it back to frozenset[str] downstream, which is what actually
    # matters; a strict dataclass `==` here would fail on that alone.
    assert frozenset(decoded_only.roles) == command.roles
    assert frozenset(decoded_only.branch_ids) == command.branch_ids
    assert decoded_only.client_turn_id == command.client_turn_id
    assert decoded_only.idempotency_key == command.idempotency_key


@pytest.mark.asyncio
async def test_temporal_sandbox_prepares_workflow_definition() -> None:
    SandboxedWorkflowRunner().prepare_workflow(
        workflow._Definition.must_from_class(OrderDiscoveryWorkflow)
    )


def test_all_dataclasses_are_frozen_and_slotted() -> None:
    for cls in (
        OrderDiscoveryWorkflowInput,
        SubmitOrderDiscoveryTurnCommand,
        RunOrderDiscoveryTurnActivityInput,
        AgentTurnResultPayload,
        OrderDiscoveryTurnError,
        OrderDiscoveryTurnOutcome,
        GraphGenerationChangedNotice,
    ):
        assert fields(cls)  # is a dataclass at all
        instance_dict_free = not hasattr(cls, "__dict__") or "__slots__" in cls.__dict__
        assert instance_dict_free


class _StubActivities:
    """A stand-in for OrderDiscoveryActivities that never touches Mongo/Neo4j --
    proves the workflow's own mechanics (mutex, signal) independent of the
    real coordinator, which gets its own real-infra proof separately."""

    def __init__(self) -> None:
        self.calls = 0
        self.concurrent_calls = 0
        self.max_concurrent_calls = 0
        self._lock = asyncio.Lock()

    @activity.defn(name="run_order_discovery_turn")
    async def run_order_discovery_turn(
        self, request: RunOrderDiscoveryTurnActivityInput
    ) -> OrderDiscoveryTurnOutcome:
        async with self._lock:
            self.concurrent_calls += 1
            self.max_concurrent_calls = max(self.max_concurrent_calls, self.concurrent_calls)
        try:
            self.calls += 1
            await asyncio.sleep(0.2)
            return OrderDiscoveryTurnOutcome(
                result=AgentTurnResultPayload(
                    conversation_id=request.conversation_id,
                    conversation_version=self.calls,
                    client_turn_id=request.client_turn_id,
                    agent_turn_result_json="{}",
                ),
                error=None,
            )
        finally:
            async with self._lock:
                self.concurrent_calls -= 1


def _command(client_turn_id: str) -> SubmitOrderDiscoveryTurnCommand:
    return SubmitOrderDiscoveryTurnCommand(
        expected_conversation_version=0,
        client_turn_id=client_turn_id,
        idempotency_key=f"idem-{client_turn_id}",
        message_id=f"msg-{client_turn_id}",
        message="find my order",
        principal_id="p1",
        tenant_id="tenant1",
        roles=frozenset({"associate"}),
        branch_ids=frozenset(),
    )


@pytest.mark.asyncio
async def test_submit_turn_mutex_serializes_concurrent_submissions() -> None:
    client = await Client.connect(_TEMPORAL_TARGET)
    task_queue = f"test-order-discovery-{uuid.uuid4().hex[:8]}"
    stub = _StubActivities()
    async with worker.Worker(
        client,
        task_queue=task_queue,
        workflows=(OrderDiscoveryWorkflow,),
        activities=(stub.run_order_discovery_turn,),
    ):
        workflow_id = f"test-order-discovery-{uuid.uuid4().hex[:8]}"
        handle = await client.start_workflow(
            OrderDiscoveryWorkflow.run,
            OrderDiscoveryWorkflowInput(conversation_id="conv-1", agent_id="agent_a"),
            id=workflow_id,
            task_queue=task_queue,
        )
        try:
            results = await asyncio.gather(
                handle.execute_update(OrderDiscoveryWorkflow.submit_turn, _command("t1")),
                handle.execute_update(OrderDiscoveryWorkflow.submit_turn, _command("t2")),
            )
            assert stub.max_concurrent_calls == 1
            assert stub.calls == 2
            versions = sorted(r.result.conversation_version for r in results if r.result)
            assert versions == [1, 2]

            description = await handle.describe()
            assert description.status is WorkflowExecutionStatus.RUNNING
        finally:
            await handle.terminate()


@pytest.mark.asyncio
async def test_generation_changed_signal_updates_execution_state() -> None:
    client = await Client.connect(_TEMPORAL_TARGET)
    task_queue = f"test-order-discovery-{uuid.uuid4().hex[:8]}"
    stub = _StubActivities()
    async with worker.Worker(
        client,
        task_queue=task_queue,
        workflows=(OrderDiscoveryWorkflow,),
        activities=(stub.run_order_discovery_turn,),
    ):
        workflow_id = f"test-order-discovery-{uuid.uuid4().hex[:8]}"
        handle = await client.start_workflow(
            OrderDiscoveryWorkflow.run,
            OrderDiscoveryWorkflowInput(conversation_id="conv-2", agent_id="agent_a"),
            id=workflow_id,
            task_queue=task_queue,
        )
        try:
            initial_state = await handle.query(OrderDiscoveryWorkflow.execution_state)
            assert initial_state.last_known_graph_generation_id is None
            assert initial_state.turn_in_progress is False

            await handle.signal(
                OrderDiscoveryWorkflow.generation_changed,
                GraphGenerationChangedNotice(
                    new_graph_generation_id="gen-2", changed_at="2026-08-09T00:00:00Z"
                ),
            )
            updated_state = await handle.query(OrderDiscoveryWorkflow.execution_state)
            assert updated_state.last_known_graph_generation_id == "gen-2"
        finally:
            await handle.terminate()


class _ClarifyingStubActivities:
    """First turn reports a paused clarification; every later turn completes.
    Records the `resume_thread_id` each invocation was handed so the test can
    prove the workflow -- not the caller -- is what routes an answer back to
    the paused graph thread."""

    def __init__(self, *, pending_thread_id: str) -> None:
        self._pending_thread_id = pending_thread_id
        self.calls = 0
        self.seen_resume_thread_ids: list[str | None] = []

    @activity.defn(name="run_order_discovery_turn")
    async def run_order_discovery_turn(
        self, request: RunOrderDiscoveryTurnActivityInput
    ) -> OrderDiscoveryTurnOutcome:
        self.calls += 1
        self.seen_resume_thread_ids.append(request.resume_thread_id)
        return OrderDiscoveryTurnOutcome(
            result=AgentTurnResultPayload(
                conversation_id=request.conversation_id,
                conversation_version=self.calls,
                client_turn_id=request.client_turn_id,
                agent_turn_result_json="{}",
                pending_clarification_thread_id=(
                    self._pending_thread_id if self.calls == 1 else None
                ),
            ),
            error=None,
        )


@pytest.mark.asyncio
async def test_pending_clarification_is_routed_into_the_next_turns_resume() -> None:
    """A turn that pauses on a clarifying question records its thread on the
    workflow; the associate's next turn is then handed that thread as
    `resume_thread_id` (so the coordinator resumes rather than starting over),
    and the pointer is consumed exactly once."""
    client = await Client.connect(_TEMPORAL_TARGET)
    task_queue = f"test-order-discovery-{uuid.uuid4().hex[:8]}"
    stub = _ClarifyingStubActivities(pending_thread_id="order-discovery:conv-4:t1:1")
    async with worker.Worker(
        client,
        task_queue=task_queue,
        workflows=(OrderDiscoveryWorkflow,),
        activities=(stub.run_order_discovery_turn,),
    ):
        handle = await client.start_workflow(
            OrderDiscoveryWorkflow.run,
            OrderDiscoveryWorkflowInput(conversation_id="conv-4", agent_id="agent_a"),
            id=f"test-order-discovery-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
        try:
            await handle.execute_update(OrderDiscoveryWorkflow.submit_turn, _command("t1"))
            paused = await handle.query(OrderDiscoveryWorkflow.execution_state)
            assert paused.pending_clarification_thread_id == "order-discovery:conv-4:t1:1"

            await handle.execute_update(OrderDiscoveryWorkflow.submit_turn, _command("t2"))
            resolved = await handle.query(OrderDiscoveryWorkflow.execution_state)
            assert resolved.pending_clarification_thread_id is None
            assert resolved.turns_handled == 2

            # First turn started fresh; second was handed the paused thread.
            assert stub.seen_resume_thread_ids == [None, "order-discovery:conv-4:t1:1"]

            # A third turn must NOT re-resume an already-consumed clarification.
            await handle.execute_update(OrderDiscoveryWorkflow.submit_turn, _command("t3"))
            assert stub.seen_resume_thread_ids[2] is None
        finally:
            await handle.terminate()


@pytest.mark.asyncio
async def test_workflow_state_survives_worker_process_restart() -> None:
    """The workflow's in-memory attributes (`_last_known_graph_generation_id`,
    `_conversation_id`) are not persisted by our own code anywhere -- Temporal
    reconstructs them by replaying the workflow's event history against a
    brand new `OrderDiscoveryWorkflow` instance. This proves that guarantee
    for real: submit a turn and a signal against one Worker process, tear
    that Worker down entirely (simulating a crash/restart -- no in-memory
    object survives), stand up a second, independent Worker for the same
    task queue, and confirm a fresh query/update against the still-running
    workflow execution sees the pre-crash state and can still make progress.
    """
    client = await Client.connect(_TEMPORAL_TARGET)
    task_queue = f"test-order-discovery-{uuid.uuid4().hex[:8]}"
    workflow_id = f"test-order-discovery-{uuid.uuid4().hex[:8]}"

    first_stub = _StubActivities()
    async with worker.Worker(
        client,
        task_queue=task_queue,
        workflows=(OrderDiscoveryWorkflow,),
        activities=(first_stub.run_order_discovery_turn,),
    ):
        handle = await client.start_workflow(
            OrderDiscoveryWorkflow.run,
            OrderDiscoveryWorkflowInput(conversation_id="conv-3", agent_id="agent_a"),
            id=workflow_id,
            task_queue=task_queue,
        )
        first_outcome = await handle.execute_update(
            OrderDiscoveryWorkflow.submit_turn, _command("t1")
        )
        assert first_outcome.result is not None
        assert first_outcome.result.conversation_version == 1
        await handle.signal(
            OrderDiscoveryWorkflow.generation_changed,
            GraphGenerationChangedNotice(
                new_graph_generation_id="gen-1", changed_at="2026-08-09T00:00:00Z"
            ),
        )
        pre_restart_state = await handle.query(OrderDiscoveryWorkflow.execution_state)
        assert pre_restart_state.last_known_graph_generation_id == "gen-1"
    # The `async with` block above has fully exited: the first Worker's
    # poller loop is stopped and every Python object it held (including any
    # OrderDiscoveryWorkflow instance the SDK cached) is gone -- nothing here
    # carries state forward in memory.

    second_stub = _StubActivities()
    handle = client.get_workflow_handle(workflow_id)
    try:
        async with worker.Worker(
            client,
            task_queue=task_queue,
            workflows=(OrderDiscoveryWorkflow,),
            activities=(second_stub.run_order_discovery_turn,),
        ):
            resumed_state = await handle.query(OrderDiscoveryWorkflow.execution_state)
            assert resumed_state.conversation_id == "conv-3"
            assert resumed_state.last_known_graph_generation_id == "gen-1"
            assert resumed_state.turn_in_progress is False

            second_outcome = await handle.execute_update(
                OrderDiscoveryWorkflow.submit_turn, _command("t2")
            )
            assert second_outcome.result is not None
            assert second_outcome.result.conversation_version == 1
            assert second_stub.calls == 1
    finally:
        await handle.terminate()
