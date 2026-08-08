"""A PENDING_EXTERNAL receipt does not livelock: it resolves to COMPLETED once the
external reference closes, and re-`begin()`-ing while still pending never re-runs the
action or loses the external_ref -- the exact failure mode a naive
"cache-the-result" design produces on the interception path."""

from __future__ import annotations

import pytest

from return_platform.platform.reasoning.receipts import (
    ReasoningActionReceipts,
    ReceiptKey,
    ReceiptStatus,
)
from tests.reasoning.conftest import ReasoningTestFixture


@pytest.mark.asyncio
async def test_pending_external_receipt_resolves_to_completed(
    reasoning_store: ReasoningTestFixture,
) -> None:
    receipts = ReasoningActionReceipts(reasoning_store.store)
    key = ReceiptKey(run_id="run-1", node_name="order_analysis", logical_action_id="ai-call-1")

    started = await receipts.begin(key)
    assert started.status is ReceiptStatus.STARTED

    pending = await receipts.mark_pending_external(key, external_ref="interception-42")
    assert pending.status is ReceiptStatus.PENDING_EXTERNAL
    assert pending.external_ref == "interception-42"

    # Simulate a resume while the interception is still open: begin() must return the
    # SAME pending state, not a new attempt, and must not lose external_ref.
    resumed_while_pending = await receipts.begin(key)
    assert resumed_while_pending.status is ReceiptStatus.PENDING_EXTERNAL
    assert resumed_while_pending.external_ref == "interception-42"

    # The operator answers the interception; the resumed node resolves through
    # external_ref and marks the receipt COMPLETED.
    completed = await receipts.mark_completed(key, result_ref="result-42")
    assert completed.status is ReceiptStatus.COMPLETED
    assert completed.result_ref == "result-42"

    # Any further begin() call returns the cached result with no new side effect.
    final = await receipts.begin(key)
    assert final.status is ReceiptStatus.COMPLETED
    assert final.result_ref == "result-42"


@pytest.mark.asyncio
async def test_failed_retryable_receipt_gets_a_fresh_attempt_on_next_begin(
    reasoning_store: ReasoningTestFixture,
) -> None:
    receipts = ReasoningActionReceipts(reasoning_store.store)
    key = ReceiptKey(run_id="run-2", node_name="order_analysis", logical_action_id="ai-call-2")

    await receipts.begin(key)
    failed = await receipts.mark_failed_retryable(key, failure_outcome={"reason": "timeout"})
    assert failed.status is ReceiptStatus.FAILED_RETRYABLE
    assert failed.attempt == 1

    retried = await receipts.begin(key)
    assert retried.status is ReceiptStatus.STARTED
    assert retried.attempt == 2
    assert retried.external_ref is None
    assert retried.failure_outcome is None
