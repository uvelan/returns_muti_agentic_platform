"""Abandonment must not race pending external work: a run with an open receipt,
interception, or resume command is reported ABANDONMENT_BLOCKED with the specific
blocking reference, and its state is never mutated -- an operator answering on day 31
must never find their answer delivered to a run already heading for deletion."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from return_platform.platform.reasoning.abandonment import (
    AbandonmentPreconditionChecker,
    ForcedAbandonment,
    RunRecord,
)
from return_platform.platform.reasoning.checkpoint import SystemStoreCheckpointSaver
from return_platform.platform.reasoning.errors import AbandonmentBlocked
from return_platform.platform.reasoning.receipts import ReasoningActionReceipts, ReceiptKey
from return_platform.platform.reasoning.retention import (
    CheckpointRetentionPolicy,
    RunLifecycleState,
)
from tests.reasoning.conftest import ReasoningTestFixture


async def _insert_run(
    reasoning_store: ReasoningTestFixture, *, run_id: str, thread_id: str
) -> None:
    now = datetime.now(UTC)
    await reasoning_store.store.collection("reasoning_runs").insert_one(
        {
            "_id": run_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "lifecycle_state": RunLifecycleState.INTERRUPTED.value,
            "workflow_id": None,
            "terminal_at": None,
            "expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )


@pytest.mark.asyncio
async def test_open_receipt_blocks_abandonment(reasoning_store: ReasoningTestFixture) -> None:
    await _insert_run(reasoning_store, run_id="run-blocked-1", thread_id="thread-blocked-1")
    receipts = ReasoningActionReceipts(reasoning_store.store)
    await receipts.begin(ReceiptKey(run_id="run-blocked-1", node_name="n", logical_action_id="a"))

    saver = SystemStoreCheckpointSaver(reasoning_store.store, reasoning_store.encryptor)
    checker = AbandonmentPreconditionChecker(reasoning_store.store, saver)
    forced = ForcedAbandonment(
        reasoning_store.store,
        reasoning_store.client,
        CheckpointRetentionPolicy(terminal_retention_hours=168),
    )
    record = RunRecord(
        run_id="run-blocked-1",
        thread_id="thread-blocked-1",
        lifecycle_state=RunLifecycleState.INTERRUPTED,
        workflow_id=None,
        idle_since=datetime.now(UTC),
    )

    with pytest.raises(AbandonmentBlocked) as excinfo:
        await forced.abandon(record, checker=checker)
    assert "receipt:" in excinfo.value.blocking_reference

    run_doc = await reasoning_store.store.collection("reasoning_runs").find_one(
        {"_id": "run-blocked-1"}
    )
    assert run_doc["lifecycle_state"] == "INTERRUPTED", "a blocked run must not be mutated"


@pytest.mark.asyncio
async def test_open_interception_blocks_abandonment(
    reasoning_store: ReasoningTestFixture,
) -> None:
    await _insert_run(reasoning_store, run_id="run-blocked-2", thread_id="thread-blocked-2")
    await reasoning_store.store.collection("ai_interceptions").insert_one(
        {"_id": "intc-1", "run_id": "run-blocked-2", "status": "OPEN"}
    )

    saver = SystemStoreCheckpointSaver(reasoning_store.store, reasoning_store.encryptor)
    checker = AbandonmentPreconditionChecker(reasoning_store.store, saver)
    forced = ForcedAbandonment(
        reasoning_store.store,
        reasoning_store.client,
        CheckpointRetentionPolicy(terminal_retention_hours=168),
    )
    record = RunRecord(
        run_id="run-blocked-2",
        thread_id="thread-blocked-2",
        lifecycle_state=RunLifecycleState.INTERRUPTED,
        workflow_id=None,
        idle_since=datetime.now(UTC),
    )

    with pytest.raises(AbandonmentBlocked) as excinfo:
        await forced.abandon(record, checker=checker)
    assert "interception:" in excinfo.value.blocking_reference


@pytest.mark.asyncio
async def test_pending_resume_command_blocks_abandonment(
    reasoning_store: ReasoningTestFixture,
) -> None:
    await _insert_run(reasoning_store, run_id="run-blocked-3", thread_id="thread-blocked-3")
    now = datetime.now(UTC)
    await reasoning_store.store.collection("reasoning_resume_commands").insert_one(
        {
            "_id": "cmd-1",
            "command_id": "cmd-1",
            "run_id": "run-blocked-3",
            "workflow_id": None,
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

    saver = SystemStoreCheckpointSaver(reasoning_store.store, reasoning_store.encryptor)
    checker = AbandonmentPreconditionChecker(reasoning_store.store, saver)
    forced = ForcedAbandonment(
        reasoning_store.store,
        reasoning_store.client,
        CheckpointRetentionPolicy(terminal_retention_hours=168),
    )
    record = RunRecord(
        run_id="run-blocked-3",
        thread_id="thread-blocked-3",
        lifecycle_state=RunLifecycleState.INTERRUPTED,
        workflow_id=None,
        idle_since=datetime.now(UTC),
    )

    with pytest.raises(AbandonmentBlocked) as excinfo:
        await forced.abandon(record, checker=checker)
    assert "resume_command:" in excinfo.value.blocking_reference
