"""A completion arriving for an already-ABANDONED run is refused, never resumed.

Every resume path must re-read lifecycle_state first (a later orchestration phase's
job); this package's own guarantee is narrower but load-bearing: once
`ForcedAbandonment` has moved a receipt to FAILED_FINAL, the receipt state machine
itself refuses any further transition on it -- a late "the interception actually
resolved" completion cannot silently mark it COMPLETED after the fact, which is
exactly what would let a superseded attempt's result leak into an abandoned run."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from return_platform.platform.reasoning.abandonment import (
    AbandonmentPreconditionChecker,
    ForcedAbandonment,
    RunRecord,
)
from return_platform.platform.reasoning.checkpoint import SystemStoreCheckpointSaver
from return_platform.platform.reasoning.receipts import (
    ActionAlreadyStarted,
    ReasoningActionReceipts,
    ReceiptKey,
    ReceiptStatus,
)
from return_platform.platform.reasoning.retention import (
    CheckpointRetentionPolicy,
    RunLifecycleState,
)
from tests.reasoning.conftest import ReasoningTestFixture


@pytest.mark.asyncio
async def test_a_late_completion_for_an_abandoned_runs_receipt_is_rejected(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = reasoning_store.store
    now = datetime.now(UTC)
    run_id = "run-late-1"
    thread_id = "thread-late-1"

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
    receipts = ReasoningActionReceipts(store)
    key = ReceiptKey(run_id=run_id, node_name="order_analysis", logical_action_id="ai-call-1")
    await receipts.begin(key)
    await receipts.mark_pending_external(key, external_ref="interception-late-1")

    # Force abandonment despite the open receipt (the operator's explicit override
    # path -- see test_forced_abandonment_commits_resume_command_atomically.py for
    # the ordinary, precondition-checked path that would normally block this).
    saver = SystemStoreCheckpointSaver(store, reasoning_store.encryptor)

    class _AlwaysClear(AbandonmentPreconditionChecker):
        async def blocking_reference(self, run: RunRecord) -> str | None:
            return None

    forced = ForcedAbandonment(
        store, reasoning_store.client, CheckpointRetentionPolicy(terminal_retention_hours=168)
    )
    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        lifecycle_state=RunLifecycleState.WAITING,
        workflow_id=None,
        idle_since=now,
    )
    await forced.abandon(record, checker=_AlwaysClear(store, saver))

    receipt_after_abandonment = await store.read_only("reasoning_action_receipts").find_one(
        {"_id": key.id}
    )
    assert receipt_after_abandonment["status"] == ReceiptStatus.FAILED_FINAL.value

    # The interception "resolves" late -- an operator answers it after the run was
    # already abandoned. The receipt state machine refuses to mark it COMPLETED.
    with pytest.raises(ActionAlreadyStarted):
        await receipts.mark_completed(key, result_ref="late-result")

    # The rejection did not mutate anything -- still FAILED_FINAL, not COMPLETED.
    receipt_final = await store.read_only("reasoning_action_receipts").find_one({"_id": key.id})
    assert receipt_final["status"] == ReceiptStatus.FAILED_FINAL.value
