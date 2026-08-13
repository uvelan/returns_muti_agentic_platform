"""Crash after the abandonment transaction commits, before the Temporal signal is
delivered: the resume_command row is already durable, so a completely new
ReasoningResumeWorker instance (simulating a fresh process after the crash) finds it
and delivers it -- against a real Temporal server, no mock."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from temporalio.client import Client as TemporalClient
from temporalio.worker import Worker

from return_platform.platform.reasoning.resume_worker import ReasoningResumeWorker
from tests.reasoning._throwaway_workflow import ThrowawayReasoningTestWorkflow
from tests.reasoning.conftest import ReasoningTestFixture

# Live infrastructure: this module opens a real Temporal client. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = pytest.mark.live_infra


@pytest.mark.asyncio
async def test_a_new_worker_instance_delivers_a_command_left_by_a_crashed_predecessor(
    reasoning_store: ReasoningTestFixture, temporal_target: str
) -> None:
    temporal_client = await TemporalClient.connect(temporal_target)
    task_queue = f"reasoning-crash-test-{uuid.uuid4()}"
    worker = Worker(
        temporal_client, task_queue=task_queue, workflows=[ThrowawayReasoningTestWorkflow]
    )

    async with worker:
        workflow_id = f"reasoning-crash-workflow-{uuid.uuid4()}"
        handle = await temporal_client.start_workflow(
            ThrowawayReasoningTestWorkflow.run, id=workflow_id, task_queue=task_queue
        )

        # Simulate the "crashed after commit, before signal" state directly: the
        # transaction already committed this row (see
        # test_forced_abandonment_commits_resume_command_atomically.py for proof the
        # transaction itself is atomic); nothing has attempted delivery yet.
        now = datetime.now(UTC)
        command_id = str(uuid.uuid4())
        await reasoning_store.store.collection("reasoning_resume_commands").insert_one(
            {
                "_id": command_id,
                "command_id": command_id,
                "run_id": "run-crash-1",
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

        # A brand-new worker instance -- no shared Python state with whatever wrote
        # the row -- finds and delivers it.
        recovering_worker = ReasoningResumeWorker(reasoning_store.store, temporal_client)
        processed = await recovering_worker.deliver_once()
        assert processed

        received = await handle.query(ThrowawayReasoningTestWorkflow.received_command_ids)
        assert command_id in received

        command_doc = await reasoning_store.store.collection("reasoning_resume_commands").find_one(
            {"_id": command_id}
        )
        assert command_doc["status"] == "DELIVERED"

        await handle.cancel()
