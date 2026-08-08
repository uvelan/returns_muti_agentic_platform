"""Forced abandonment is one Mongo transaction: run -> ABANDONED, open interceptions
-> CANCELLED, STARTED/PENDING_EXTERNAL receipts -> FAILED_FINAL, expires_at stamped on
checkpoints/writes/receipts, and a reasoning_resume_commands PENDING row -- all land
together or not at all."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from return_platform.platform.reasoning.abandonment import (
    AbandonmentPreconditionChecker,
    ForcedAbandonment,
    RunRecord,
)
from return_platform.platform.reasoning.checkpoint import SystemStoreCheckpointSaver
from return_platform.platform.reasoning.receipts import ReceiptStatus
from return_platform.platform.reasoning.retention import (
    CheckpointRetentionPolicy,
    RunLifecycleState,
)
from tests.reasoning.conftest import ReasoningTestFixture


@pytest.mark.asyncio
async def test_forced_abandonment_commits_every_effect_together(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = reasoning_store.store
    now = datetime.now(UTC)
    run_id = "run-forced-1"
    thread_id = "thread-forced-1"

    await store.collection("reasoning_runs").insert_one(
        {
            "_id": run_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "lifecycle_state": RunLifecycleState.WAITING.value,
            "workflow_id": None,
            "terminal_at": None,
            "expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    saver = SystemStoreCheckpointSaver(store, reasoning_store.encryptor)
    # A real checkpoint, written through the saver itself -- not a hand-built envelope
    # -- so AbandonmentPreconditionChecker's real aget_tuple() call can genuinely
    # decrypt and deserialize it, exactly as it would for a real reasoning run.
    await saver.aput(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        empty_checkpoint(),
        {},
        {},
    )
    checker = AbandonmentPreconditionChecker(store, saver)
    forced = ForcedAbandonment(
        store, reasoning_store.client, CheckpointRetentionPolicy(terminal_retention_hours=168)
    )
    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        lifecycle_state=RunLifecycleState.WAITING,
        workflow_id="workflow-forced-1",
        idle_since=now,
    )

    command_id = await forced.abandon(record, checker=checker)

    run_doc = await store.collection("reasoning_runs").find_one({"_id": run_id})
    assert run_doc["lifecycle_state"] == "ABANDONED"
    assert run_doc["expires_at"] is not None

    checkpoint_doc = await store.read_only("reasoning_checkpoints").find_one(
        {"thread_id": thread_id}
    )
    assert checkpoint_doc["expires_at"] == run_doc["expires_at"], (
        "checkpoint expiry must match the run's exactly -- stamped together"
    )

    command_doc = await store.collection("reasoning_resume_commands").find_one({"_id": command_id})
    assert command_doc is not None
    assert command_doc["status"] == "PENDING"
    assert command_doc["run_id"] == run_id
    assert command_doc["workflow_id"] == "workflow-forced-1"
    assert command_doc["signal"] == "REASONING_ABANDONED"


@pytest.mark.asyncio
async def test_forced_abandonment_fails_receipts_and_cancels_interceptions(
    reasoning_store: ReasoningTestFixture,
) -> None:
    from return_platform.platform.reasoning.receipts import ReasoningActionReceipts, ReceiptKey

    store = reasoning_store.store
    now = datetime.now(UTC)
    run_id = "run-forced-2"
    thread_id = "thread-forced-2"

    await store.collection("reasoning_runs").insert_one(
        {
            "_id": run_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "lifecycle_state": RunLifecycleState.WAITING.value,
            "workflow_id": None,
            "terminal_at": None,
            "expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )

    # Note: an open receipt/interception would normally BLOCK abandonment (see the
    # blocked-by-pending-external tests) -- this test exercises what forced
    # abandonment does to receipts/interceptions that are open at the moment it
    # commits, by constructing the ForcedAbandonment call directly rather than
    # through the precondition-checked `abandon()` entrypoint, matching the "operator
    # explicitly forces it" path the design allows for a genuinely stuck run.
    receipts = ReasoningActionReceipts(store)
    key = ReceiptKey(run_id=run_id, node_name="n", logical_action_id="a")
    await receipts.begin(key)
    await store.collection("ai_interceptions").insert_one(
        {"_id": "intc-forced-2", "run_id": run_id, "status": "OPEN"}
    )

    saver = SystemStoreCheckpointSaver(store, reasoning_store.encryptor)
    retention = CheckpointRetentionPolicy(terminal_retention_hours=168)
    forced = ForcedAbandonment(store, reasoning_store.client, retention)

    class _AlwaysClear(AbandonmentPreconditionChecker):
        async def blocking_reference(self, run: RunRecord) -> str | None:
            return None

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        lifecycle_state=RunLifecycleState.WAITING,
        workflow_id=None,
        idle_since=now,
    )
    await forced.abandon(record, checker=_AlwaysClear(store, saver))

    receipt_doc = await store.read_only("reasoning_action_receipts").find_one({"_id": key.id})
    assert receipt_doc["status"] == ReceiptStatus.FAILED_FINAL.value
    assert receipt_doc["failure_outcome"]["reason"] == "REASONING_ABANDONED"

    interception_doc = await store.read_only("ai_interceptions").find_one({"_id": "intc-forced-2"})
    assert interception_doc["status"] == "CANCELLED"
