"""A redelivered signal (crash between "signal sent" and "marked DELIVERED", or the
resume worker retrying after a lease expiry) must not be double-processed -- dedup is
workflow-side on command_id, proven against a real Temporal server."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from temporalio.client import Client as TemporalClient
from temporalio.worker import Worker

from return_platform.platform.reasoning.resume_worker import ReasoningResumeWorker
from tests.reasoning._throwaway_workflow import ThrowawayReasoningTestWorkflow
from tests.reasoning.conftest import ReasoningTestFixture


@pytest.mark.asyncio
async def test_redelivering_the_same_command_id_does_not_double_process(
    reasoning_store: ReasoningTestFixture, temporal_target: str
) -> None:
    temporal_client = await TemporalClient.connect(temporal_target)
    task_queue = f"reasoning-dup-test-{uuid.uuid4()}"
    worker = Worker(
        temporal_client, task_queue=task_queue, workflows=[ThrowawayReasoningTestWorkflow]
    )

    async with worker:
        workflow_id = f"reasoning-dup-workflow-{uuid.uuid4()}"
        handle = await temporal_client.start_workflow(
            ThrowawayReasoningTestWorkflow.run, id=workflow_id, task_queue=task_queue
        )

        now = datetime.now(UTC)
        command_id = str(uuid.uuid4())
        commands = reasoning_store.store.collection("reasoning_resume_commands")
        await commands.insert_one(
            {
                "_id": command_id,
                "command_id": command_id,
                "run_id": "run-dup-1",
                "workflow_id": workflow_id,
                "signal": "REASONING_ABANDONED",
                "status": "PENDING",
                "next_attempt_at": now,
                "attempt_count": 0,
                "lease_owner": None,
                "lease_until": None,
                "created_at": now,
                "updated_at": now,
                "delivered_at": None,
            }
        )

        resume_worker = ReasoningResumeWorker(reasoning_store.store, temporal_client)
        await resume_worker.deliver_once()

        # Redeliver the exact same command_id directly -- simulating a crash between
        # "Temporal accepted the signal" and "resume_command marked DELIVERED", which
        # would make a naive at-least-once worker redeliver on its next pass.
        await handle.signal("REASONING_ABANDONED", command_id)
        await asyncio.sleep(0.5)

        received = await handle.query(ThrowawayReasoningTestWorkflow.received_command_ids)
        assert received.count(command_id) == 1, (
            f"the workflow processed the same command_id more than once: {received}"
        )

        await handle.cancel()
