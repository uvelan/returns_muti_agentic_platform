"""ReasoningRunLifecycle: the missing write path for reasoning_runs. Before
this, only mark_terminal (retention.py, terminal-only) and test fixtures ever
wrote a reasoning_runs document -- nothing started or non-terminally
transitioned one."""

from __future__ import annotations

import pytest

from return_platform.platform.reasoning.retention import RunLifecycleState
from return_platform.platform.reasoning.run_lifecycle import (
    ReasoningRunLifecycle,
    RunBoundToDifferentThread,
)
from tests.reasoning.conftest import ReasoningTestFixture


@pytest.mark.asyncio
async def test_start_run_creates_a_running_record(reasoning_store: ReasoningTestFixture) -> None:
    lifecycle = ReasoningRunLifecycle(reasoning_store.store)
    await lifecycle.start_run(run_id="run-1", thread_id="thread-1", workflow_id="wf-1")

    doc = await reasoning_store.store.collection("reasoning_runs").find_one({"_id": "run-1"})
    assert doc is not None
    assert doc["lifecycle_state"] == RunLifecycleState.RUNNING.value
    assert doc["thread_id"] == "thread-1"
    assert doc["workflow_id"] == "wf-1"
    assert doc["terminal_at"] is None
    assert doc["expires_at"] is None


@pytest.mark.asyncio
async def test_start_run_is_idempotent_for_the_same_thread(
    reasoning_store: ReasoningTestFixture,
) -> None:
    lifecycle = ReasoningRunLifecycle(reasoning_store.store)
    await lifecycle.start_run(run_id="run-2", thread_id="thread-2")
    # A retried Temporal activity attempt before any node ran -- must not raise.
    await lifecycle.start_run(run_id="run-2", thread_id="thread-2")

    doc = await reasoning_store.store.collection("reasoning_runs").find_one({"_id": "run-2"})
    assert doc is not None
    assert doc["lifecycle_state"] == RunLifecycleState.RUNNING.value


@pytest.mark.asyncio
async def test_start_run_rejects_reuse_under_a_different_thread(
    reasoning_store: ReasoningTestFixture,
) -> None:
    lifecycle = ReasoningRunLifecycle(reasoning_store.store)
    await lifecycle.start_run(run_id="run-3", thread_id="thread-3")
    with pytest.raises(RunBoundToDifferentThread):
        await lifecycle.start_run(run_id="run-3", thread_id="thread-other")


@pytest.mark.asyncio
async def test_transition_non_terminal_moves_between_active_states(
    reasoning_store: ReasoningTestFixture,
) -> None:
    lifecycle = ReasoningRunLifecycle(reasoning_store.store)
    await lifecycle.start_run(run_id="run-4", thread_id="thread-4")

    await lifecycle.transition_non_terminal(
        run_id="run-4", lifecycle_state=RunLifecycleState.WAITING
    )
    doc = await reasoning_store.store.collection("reasoning_runs").find_one({"_id": "run-4"})
    assert doc is not None
    assert doc["lifecycle_state"] == RunLifecycleState.WAITING.value

    await lifecycle.transition_non_terminal(
        run_id="run-4", lifecycle_state=RunLifecycleState.RUNNING
    )
    doc = await reasoning_store.store.collection("reasoning_runs").find_one({"_id": "run-4"})
    assert doc is not None
    assert doc["lifecycle_state"] == RunLifecycleState.RUNNING.value


@pytest.mark.asyncio
async def test_transition_non_terminal_rejects_a_terminal_state(
    reasoning_store: ReasoningTestFixture,
) -> None:
    lifecycle = ReasoningRunLifecycle(reasoning_store.store)
    await lifecycle.start_run(run_id="run-5", thread_id="thread-5")
    with pytest.raises(ValueError, match="not a non-terminal"):
        await lifecycle.transition_non_terminal(
            run_id="run-5", lifecycle_state=RunLifecycleState.COMPLETED
        )
