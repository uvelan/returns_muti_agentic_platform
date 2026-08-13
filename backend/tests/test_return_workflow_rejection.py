"""A rejected stage command must fail the *update*, not wedge the session.

Temporal only fails a workflow or update when the raised exception is a
`FailureError`. Anything else -- a bare `RuntimeError`, which is what
`ReturnWorkflowTransitionError` used to be -- is treated as a **workflow task**
failure, and the server retries workflow tasks indefinitely. So every
deterministic rejection (`STAGE_OUT_OF_ORDER`, `COMMAND_CONFLICT`,
`ALREADY_COMPLETED`, ...) used to put the whole return session into a permanent
retry loop: `execute_update` never returned, the calling orchestrator hung, and
no later command could be applied. The verdict was correct; the delivery
mechanism destroyed the workflow.

These are not exotic inputs. `orchestrator.py::_complete` derives `command_id`
deterministically from `(session_id, stage)`, so a duplicate is deduplicated
safely -- but an operator retrying a stale request, or two consoles acting on
one return, produces exactly the out-of-order command that wedged it.

The load-bearing assertion in each test below is the *last* one: after the
rejection, the workflow is still alive and still accepts a valid command. A
test that only asserted "the update raised" would pass against a workflow that
had been left permanently poisoned.

Runs against a real Temporal server: the behaviour under test is entirely the
server's task-failure-vs-update-failure policy, which no fake reproduces.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from temporalio import activity, worker
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from return_platform.canonical.operations import WorkflowStage
from return_platform.operations.orchestrator import ReturnOrchestrator
from return_platform.workflows.return_workflow import (
    ReturnSessionActivityResult,
    ReturnSessionInitializeActivityInput,
    ReturnSessionTransitionActivityInput,
    ReturnWorkflow,
    ReturnWorkflowAdvanceCommand,
    ReturnWorkflowConfigurationVersion,
    ReturnWorkflowErrorCode,
    ReturnWorkflowInput,
)
from return_platform.workflows.stage_results import (
    IntakeActivityResult,
    IntakeChannel,
    OrderDiscoveryActivityResult,
    bind_stage_activity_result,
)

# Live infrastructure: this module opens a real Temporal client. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = pytest.mark.live_infra

# Host runs reach Temporal on the published port; inside the compose network the
# real-infra runner sets PLATFORM_TEST_TEMPORAL_TARGET. Same convention as
# tests/conftest.py and tests/test_order_discovery_workflow.py.
_TEMPORAL_TARGET = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")
_SESSION_ID = "5a1c9f2e-7d84-4c31-9b60-2f8e13a4c507"
_CORRELATION_ID = "c41d0b77-1e93-49a6-8f52-6ab0d3e91c48"
_OBSERVED_AT = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)

# A wedged workflow shows up as a hang, not a failure, so every await that could
# be caught in the retry loop is bounded. This is a hang detector, not a latency
# assertion: the failure it guards against is *infinite*, so a generous bound
# discriminates just as well as a tight one and does not flake. An earlier 20s
# bound passed in isolation (the whole file runs in ~4s) but tripped once during
# a loaded 21-minute full-suite run, terminating the workflow mid-update.
_UPDATE_TIMEOUT = 120.0


def _workflow_input() -> ReturnWorkflowInput:
    return ReturnWorkflowInput(
        session_id=_SESSION_ID,
        correlation_id=_CORRELATION_ID,
        workflow_version="1.0",
        configuration_versions=(ReturnWorkflowConfigurationVersion("workflow", "return-v1"),),
    )


def _intake_command(command_id: str | None = None) -> ReturnWorkflowAdvanceCommand:
    return ReturnWorkflowAdvanceCommand(
        command_id=command_id or str(uuid.uuid4()),
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


def _discovery_command() -> ReturnWorkflowAdvanceCommand:
    return ReturnWorkflowAdvanceCommand(
        command_id=str(uuid.uuid4()),
        completed_stage=WorkflowStage.ORDER_DISCOVERY,
        context_binding=bind_stage_activity_result(
            WorkflowStage.ORDER_DISCOVERY,
            OrderDiscoveryActivityResult(
                schema_version="order-discovery-v1",
                request_reference="REQUEST-1",
                customer_reference="CUSTOMER-1",
                order_references=("ORDER-1",),
                source_asset_id="mongo_main.orders",
                source_document_references=("FIXTURE:DOC-1",),
                evidence_references=("FIXTURE:DISCOVERY-1",),
                observed_at=_OBSERVED_AT,
            ),
        ),
    )


class _ProbeActivities:
    """Stands in for ReturnSessionActivities.

    Initialization is fast here -- unlike the concurrency probe, this file is
    not trying to open a startup race, and a delay would only slow the suite.
    """

    @activity.defn(name="initialize_return_session")
    async def initialize_return_session(
        self, request: ReturnSessionInitializeActivityInput
    ) -> ReturnSessionActivityResult:
        return ReturnSessionActivityResult(
            revision=0,
            current_stage=request.execution_state.current_stage,
            state_digest="probe-digest",
        )

    @activity.defn(name="transition_return_session")
    async def transition_return_session(
        self, request: ReturnSessionTransitionActivityInput
    ) -> ReturnSessionActivityResult:
        return ReturnSessionActivityResult(
            revision=len(request.next_state.applied_commands),
            current_stage=request.next_state.current_stage,
            state_digest="probe-digest",
        )


def _worker(client: Client, task_queue: str) -> worker.Worker:
    probe = _ProbeActivities()
    return worker.Worker(
        client,
        task_queue=task_queue,
        workflows=(ReturnWorkflow,),
        activities=(probe.initialize_return_session, probe.transition_return_session),
    )


async def _start(client: Client, task_queue: str):  # noqa: ANN202 - SDK handle generic
    return await client.start_workflow(
        ReturnWorkflow.run,
        _workflow_input(),
        id=f"test-return-rejection-{uuid.uuid4().hex[:8]}",
        task_queue=task_queue,
    )


def test_the_orchestrator_records_the_verdict_not_the_transport() -> None:
    """`_fail` stamps a failure code onto the return record and onto the
    HIGH-priority support case an operator triages. Every rejection travels
    inside the same `WorkflowUpdateFailedError`, so naming the outer class
    would make all of them indistinguishable at exactly the moment a human
    needs to tell them apart."""
    wrapped = WorkflowUpdateFailedError(
        ApplicationError(
            "The stage-completion command is out of order.",
            type=ReturnWorkflowErrorCode.STAGE_OUT_OF_ORDER.value,
            non_retryable=True,
        )
    )
    assert ReturnOrchestrator._failure_code(wrapped) == "RETURN_WORKFLOW_STAGE_OUT_OF_ORDER"

    # Anything else keeps the previous behaviour.
    assert ReturnOrchestrator._failure_code(ValueError("ORDER_NOT_FOUND")) == "VALUEERROR"


@pytest.mark.asyncio
async def test_an_out_of_order_command_fails_the_update_and_leaves_the_session_usable() -> None:
    client = await Client.connect(_TEMPORAL_TARGET)
    task_queue = f"test-return-rejection-{uuid.uuid4().hex[:8]}"

    async with _worker(client, task_queue):
        handle = await _start(client, task_queue)
        try:
            await asyncio.wait_for(
                handle.execute_update(ReturnWorkflow.complete_stage, _intake_command()),
                timeout=_UPDATE_TIMEOUT,
            )

            # INTAKE is behind us now, so a second INTAKE from a different
            # command is a genuine out-of-order verdict rather than a dedupe.
            with pytest.raises(WorkflowUpdateFailedError) as caught:
                await asyncio.wait_for(
                    handle.execute_update(ReturnWorkflow.complete_stage, _intake_command()),
                    timeout=_UPDATE_TIMEOUT,
                )

            cause = caught.value.cause
            assert isinstance(cause, ApplicationError)
            assert cause.type == ReturnWorkflowErrorCode.STAGE_OUT_OF_ORDER.value
            assert cause.non_retryable is True
            # The safe message crosses the wire; raw business data must not.
            assert "ORDER-1" not in str(cause)
            assert "CUSTOMER-1" not in str(cause)

            # The point of the fix: the rejection cost us the command, not the
            # session. Both of these hang forever against the old code.
            state = await asyncio.wait_for(
                handle.query(ReturnWorkflow.execution_state), timeout=_UPDATE_TIMEOUT
            )
            assert state.current_stage is WorkflowStage.ORDER_DISCOVERY
            assert len(state.applied_commands) == 1

            advanced = await asyncio.wait_for(
                handle.execute_update(ReturnWorkflow.complete_stage, _discovery_command()),
                timeout=_UPDATE_TIMEOUT,
            )
            assert advanced.current_stage is WorkflowStage.ELIGIBILITY_EVALUATION
            assert len(advanced.applied_commands) == 2
        finally:
            await handle.terminate()


@pytest.mark.asyncio
async def test_a_conflicting_command_id_fails_the_update_and_leaves_the_session_usable() -> None:
    """The other rejection an operator can realistically trigger: reusing a
    command id -- which `orchestrator.py` derives deterministically -- while
    carrying different stage evidence."""
    client = await Client.connect(_TEMPORAL_TARGET)
    task_queue = f"test-return-rejection-{uuid.uuid4().hex[:8]}"

    async with _worker(client, task_queue):
        handle = await _start(client, task_queue)
        try:
            command_id = str(uuid.uuid4())
            await asyncio.wait_for(
                handle.execute_update(ReturnWorkflow.complete_stage, _intake_command(command_id)),
                timeout=_UPDATE_TIMEOUT,
            )

            conflicting = ReturnWorkflowAdvanceCommand(
                command_id=command_id,
                completed_stage=WorkflowStage.ORDER_DISCOVERY,
                context_binding=_discovery_command().context_binding,
            )
            with pytest.raises(WorkflowUpdateFailedError) as caught:
                await asyncio.wait_for(
                    handle.execute_update(ReturnWorkflow.complete_stage, conflicting),
                    timeout=_UPDATE_TIMEOUT,
                )

            cause = caught.value.cause
            assert isinstance(cause, ApplicationError)
            assert cause.type == ReturnWorkflowErrorCode.COMMAND_CONFLICT.value

            # Still usable: the same stage succeeds under a fresh command id.
            advanced = await asyncio.wait_for(
                handle.execute_update(ReturnWorkflow.complete_stage, _discovery_command()),
                timeout=_UPDATE_TIMEOUT,
            )
            assert advanced.current_stage is WorkflowStage.ELIGIBILITY_EVALUATION
        finally:
            await handle.terminate()
