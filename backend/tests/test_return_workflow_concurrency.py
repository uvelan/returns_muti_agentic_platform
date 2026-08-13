"""`ReturnWorkflow.complete_stage`'s mutex, against a real Temporal server.

`workflow.wait_condition` never resolves synchronously — it registers a future
and the SDK later evaluates *all* pending conditions in one batch pass
(`_WorkflowInstanceImpl._run_once` / `_conditions`). So two update handlers
waiting on the same predicate are released **together** when it becomes true,
and a bare `await wait_condition(...)` followed by setting the flag does not
serialise them.

`complete_stage` waits on `self._persistence_ready and not
self._transition_in_progress`. Both parts flip during startup: `run()` sets
`_persistence_ready = True` right after the initialize activity, at which point
every queued `complete_stage` is released at once.

That is worse here than in `OrderDiscoveryWorkflow`, where the same bug was
found first: `self._state = next_state` happens only *after* the transition
activity returns, so two released handlers both read the same `previous_state`,
both compute a `next_state` from it, and both persist — a genuine double
transition against the authoritative session record, not merely wasted work.

The probe is deliberately not business-semantic: the stub transition activity
records how many invocations overlap. A correct mutex yields exactly one.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from temporalio import activity, worker
from temporalio.client import Client

from return_platform.canonical.operations import WorkflowStage
from return_platform.workflows.return_workflow import (
    ReturnSessionActivityResult,
    ReturnSessionInitializeActivityInput,
    ReturnSessionTransitionActivityInput,
    ReturnWorkflow,
    ReturnWorkflowAdvanceCommand,
    ReturnWorkflowConfigurationVersion,
    ReturnWorkflowInput,
)
from return_platform.workflows.stage_results import (
    IntakeActivityResult,
    IntakeChannel,
    bind_stage_activity_result,
)

# Live infrastructure: this module opens a real Temporal client. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = pytest.mark.live_infra

# Host runs reach Temporal on the published port; inside the compose network
# the real-infra runner sets PLATFORM_TEST_TEMPORAL_TARGET. Same convention as
# tests/conftest.py and tests/test_order_discovery_workflow.py.
_TEMPORAL_TARGET = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")
_SESSION_ID = "3e3d274b-229f-4f46-a7d1-f5d94bd9b53d"
_CORRELATION_ID = "894231ea-f972-4a26-8944-420e3abcbd67"
_OBSERVED_AT = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)


def _workflow_input() -> ReturnWorkflowInput:
    return ReturnWorkflowInput(
        session_id=_SESSION_ID,
        correlation_id=_CORRELATION_ID,
        workflow_version="1.0",
        configuration_versions=(ReturnWorkflowConfigurationVersion("workflow", "return-v1"),),
    )


def _intake_command() -> ReturnWorkflowAdvanceCommand:
    """One INTAKE completion.

    Both concurrent updates deliberately submit the *same* command. That is what
    makes the probe unambiguous: `advance_return_workflow` deduplicates an
    already-applied command_id by returning the state unchanged, and
    `complete_stage` short-circuits on `next_state is previous_state` before
    reaching the activity. So a correctly serialised pair performs exactly one
    transition, while a racing pair -- both reading the same pre-advance state --
    each compute a real transition and call the activity together.

    Two *different* commands would instead make the loser hit
    STAGE_OUT_OF_ORDER, and `ReturnWorkflowTransitionError` is a plain
    RuntimeError, which Temporal treats as a workflow-task failure and retries
    forever. That wedges the workflow and the test hangs -- a real defect in its
    own right (see the ledger), but not the one under test here.
    """
    return ReturnWorkflowAdvanceCommand(
        command_id=str(uuid.uuid4()),
        completed_stage=WorkflowStage.INTAKE,
        context_binding=bind_stage_activity_result(
            WorkflowStage.INTAKE,
            IntakeActivityResult(
                schema_version="intake-v1",
                request_reference="REQUEST-1",
                channel=IntakeChannel.ASSOCIATE,
                customer_reference="CUSTOMER-1",
                order_reference="ORDER-1",
                evidence_references=("FIXTURE:INTAKE-1",),
                observed_at=_OBSERVED_AT,
            ),
        ),
    )


class _ProbeActivities:
    """Stands in for ReturnSessionActivities.

    `initialize_return_session` is slow on purpose: it holds `run()` before
    `_persistence_ready = True`, which is the window in which concurrent
    `complete_stage` updates queue up on the shared condition. Without that
    window the race is not reachable.
    """

    def __init__(self, *, initialize_delay: float = 0.6) -> None:
        self._initialize_delay = initialize_delay
        self.transition_calls = 0
        self.concurrent_transitions = 0
        self.max_concurrent_transitions = 0
        self._lock = asyncio.Lock()

    @activity.defn(name="initialize_return_session")
    async def initialize_return_session(
        self, request: ReturnSessionInitializeActivityInput
    ) -> ReturnSessionActivityResult:
        await asyncio.sleep(self._initialize_delay)
        return ReturnSessionActivityResult(
            revision=0,
            current_stage=request.execution_state.current_stage,
            state_digest="probe-digest",
        )

    @activity.defn(name="transition_return_session")
    async def transition_return_session(
        self, request: ReturnSessionTransitionActivityInput
    ) -> ReturnSessionActivityResult:
        async with self._lock:
            self.transition_calls += 1
            self.concurrent_transitions += 1
            self.max_concurrent_transitions = max(
                self.max_concurrent_transitions, self.concurrent_transitions
            )
        try:
            await asyncio.sleep(0.2)
            # Echo what the workflow expects, so a correctly-serialised run
            # completes rather than failing on PERSISTENCE_MISMATCH and hiding
            # whatever the mutex actually did.
            return ReturnSessionActivityResult(
                revision=len(request.next_state.applied_commands),
                current_stage=request.next_state.current_stage,
                state_digest="probe-digest",
            )
        finally:
            async with self._lock:
                self.concurrent_transitions -= 1


@pytest.mark.asyncio
async def test_concurrent_stage_completions_are_serialised() -> None:
    """Two updates submitted while startup is still in flight are released
    together by the batch pass; the mutex must still admit only one."""
    client = await Client.connect(_TEMPORAL_TARGET)
    task_queue = f"test-return-concurrency-{uuid.uuid4().hex[:8]}"
    probe = _ProbeActivities()

    async with worker.Worker(
        client,
        task_queue=task_queue,
        workflows=(ReturnWorkflow,),
        activities=(probe.initialize_return_session, probe.transition_return_session),
    ):
        handle = await client.start_workflow(
            ReturnWorkflow.run,
            _workflow_input(),
            id=f"test-return-concurrency-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
        try:
            # Both land while initialize_return_session is still sleeping, so
            # both are parked on the same wait_condition and are released
            # together when it sets _persistence_ready.
            command = _intake_command()
            await asyncio.gather(
                handle.execute_update(ReturnWorkflow.complete_stage, command),
                handle.execute_update(ReturnWorkflow.complete_stage, command),
            )

            assert probe.max_concurrent_transitions == 1, (
                "two complete_stage handlers persisted a transition at the same time; "
                "the wait_condition mutex did not serialise them"
            )
            # The second handler must observe the state the first committed and
            # dedupe, rather than transitioning again from the same predecessor.
            assert probe.transition_calls == 1
        finally:
            await handle.terminate()


@pytest.mark.asyncio
async def test_a_second_completion_sees_the_first_ones_state() -> None:
    """The serialisation has to be *useful*, not just non-overlapping: the
    second handler must read the state the first one committed, or it would
    compute its transition from a stale predecessor."""
    client = await Client.connect(_TEMPORAL_TARGET)
    task_queue = f"test-return-concurrency-{uuid.uuid4().hex[:8]}"
    probe = _ProbeActivities()

    async with worker.Worker(
        client,
        task_queue=task_queue,
        workflows=(ReturnWorkflow,),
        activities=(probe.initialize_return_session, probe.transition_return_session),
    ):
        handle = await client.start_workflow(
            ReturnWorkflow.run,
            _workflow_input(),
            id=f"test-return-concurrency-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
        try:
            command = _intake_command()
            await asyncio.gather(
                handle.execute_update(ReturnWorkflow.complete_stage, command),
                handle.execute_update(ReturnWorkflow.complete_stage, command),
            )
            state = await handle.query(ReturnWorkflow.execution_state)
            # Exactly one application recorded, and the stage advanced once.
            assert len(state.applied_commands) == 1
            assert state.current_stage is WorkflowStage.ORDER_DISCOVERY
        finally:
            await handle.terminate()
