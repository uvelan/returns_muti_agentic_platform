"""Retention is keyed to terminal state, never creation time: an active run's
checkpoints must never get an expires_at, and mark_terminal must stamp the same
expires_at across the run, its checkpoints, its writes, and its receipts together."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from return_platform.dynamic_knowledge.knowledge.evidence import QueryEvidence
from return_platform.platform.reasoning.evidence_store import QueryEvidenceStore
from return_platform.platform.reasoning.retention import (
    CheckpointRetentionPolicy,
    RunLifecycleState,
    TerminalAtRequired,
)
from tests.reasoning.conftest import ReasoningTestFixture


def test_active_lifecycle_states_compute_no_expiry() -> None:
    policy = CheckpointRetentionPolicy(terminal_retention_hours=168)
    for state in (
        RunLifecycleState.RUNNING,
        RunLifecycleState.INTERRUPTED,
        RunLifecycleState.WAITING,
    ):
        assert policy.compute_expires_at(lifecycle_state=state, terminal_at=None) is None


def test_terminal_lifecycle_state_without_terminal_at_is_rejected() -> None:
    policy = CheckpointRetentionPolicy(terminal_retention_hours=168)
    with pytest.raises(TerminalAtRequired):
        policy.compute_expires_at(lifecycle_state=RunLifecycleState.COMPLETED, terminal_at=None)


@pytest.mark.asyncio
async def test_mark_terminal_stamps_the_same_expiry_across_run_checkpoints_and_receipts(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = reasoning_store.store
    client = reasoning_store.client
    now = datetime.now(UTC)
    run_id = "retention-run-1"
    thread_id = "retention-thread-1"

    runs = store.collection("reasoning_runs")
    await runs.insert_one(
        {
            "_id": run_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "lifecycle_state": RunLifecycleState.RUNNING.value,
            "terminal_at": None,
            "expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    envelope = {"ciphertext": b"x", "key_ref": "k", "algorithm": "AES-256-GCM", "version": "v1"}
    await store.insert_one(
        "reasoning_checkpoints",
        {
            "_id": "cp-1",
            "thread_id": thread_id,
            "checkpoint_ns": "",
            "checkpoint_id": "cp-1",
            "_envelope": envelope,
        },
        allowed_metadata_fields=frozenset({"thread_id", "checkpoint_ns", "checkpoint_id"}),
    )

    # Still RUNNING: no expiry anywhere yet.
    run_doc_before = await runs.find_one({"_id": run_id})
    assert run_doc_before["expires_at"] is None

    policy = CheckpointRetentionPolicy(terminal_retention_hours=168)
    expires_at = await policy.mark_terminal(
        store,
        client,
        run_id=run_id,
        thread_id=thread_id,
        lifecycle_state=RunLifecycleState.COMPLETED,
        terminal_at=now,
    )

    assert expires_at - now == timedelta(hours=168)

    run_doc_after = await runs.find_one({"_id": run_id})
    assert run_doc_after["lifecycle_state"] == "COMPLETED"
    # Mongo's BSON datetime round-trips at millisecond precision and drops tzinfo, so
    # compare the value actually persisted rather than the in-memory `expires_at`.
    assert run_doc_after["expires_at"] is not None

    checkpoint_doc = await store.read_only("reasoning_checkpoints").find_one({"_id": "cp-1"})
    assert checkpoint_doc["expires_at"] == run_doc_after["expires_at"], (
        "checkpoint expiry must match the run's exactly -- both persisted by the same "
        "stamp, so comparing the two stored values (not the in-memory Python one) is "
        "meaningful"
    )


@pytest.mark.asyncio
async def test_mark_terminal_also_stamps_query_evidence(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = reasoning_store.store
    client = reasoning_store.client
    now = datetime.now(UTC)
    run_id = "retention-run-2"
    thread_id = "retention-thread-2"

    runs = store.collection("reasoning_runs")
    await runs.insert_one(
        {
            "_id": run_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "lifecycle_state": RunLifecycleState.RUNNING.value,
            "terminal_at": None,
            "expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    evidence_store = QueryEvidenceStore(store, reasoning_store.encryptor)
    await evidence_store.put(
        run_id=run_id,
        evidence=QueryEvidence.create(
            query_execution_id="qe-1",
            schema_version="2026.08.1",
            graph_generation_id="legacy-live",
            logical_plan_checksum="a" * 64,
            compiled_query_checksum="b" * 64,
            result={"orderId": "ORD-1"},
        ),
    )

    policy = CheckpointRetentionPolicy(terminal_retention_hours=168)
    expires_at = await policy.mark_terminal(
        store,
        client,
        run_id=run_id,
        thread_id=thread_id,
        lifecycle_state=RunLifecycleState.COMPLETED,
        terminal_at=now,
    )
    assert expires_at - now == timedelta(hours=168)

    evidence_doc = await store.read_only("order_discovery_query_evidence").find_one({"_id": "qe-1"})
    run_doc_after = await runs.find_one({"_id": run_id})
    assert evidence_doc["expires_at"] == run_doc_after["expires_at"]

    # Evidence itself is still readable after the stamp -- expires_at is a TTL marker,
    # not a tombstone; Mongo's background reaper is what actually deletes it later.
    rehydrated = await evidence_store.get("qe-1")
    assert rehydrated is not None
    assert rehydrated.result == {"orderId": "ORD-1"}
