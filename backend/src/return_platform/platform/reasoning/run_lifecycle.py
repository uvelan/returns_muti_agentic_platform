"""Write path for `reasoning_runs` -- the run's own lifecycle record.

Before Phase 7 / Wave C2, nothing in `src` ever created a `reasoning_runs`
document: `retention.py`'s `mark_terminal` only *transitions* an existing one
to a terminal state, and `abandonment.py` only reads/abandons existing ones.
Only test fixtures inserted a starting document. This module is the missing
piece: starting a run (RUNNING) and moving it between the non-terminal
states (RUNNING/INTERRUPTED/WAITING) a real caller needs before it ever
reaches `mark_terminal`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from return_platform.platform.reasoning.retention import (
    ACTIVE_LIFECYCLE_STATES,
    RunLifecycleState,
)
from return_platform.platform.system_store.repository import SystemStore

_RUNS_STRUCTURE = "reasoning_runs"


class RunBoundToDifferentThread(RuntimeError):
    """A run_id already exists, bound to a different thread_id than requested."""


class ReasoningRunLifecycle:
    """Backed by `reasoning_runs` -- not encrypted, so guarded raw-collection
    access (`SystemStore.collection()`) is available, matching
    `ReasoningActionReceipts`'s own justification for the same structure
    family."""

    def __init__(self, system_store: SystemStore, *, clock: Any = None) -> None:
        self._collection = system_store.collection(_RUNS_STRUCTURE)
        self._now = clock.now if clock is not None else (lambda: datetime.now(UTC))

    async def start_run(
        self, *, run_id: str, thread_id: str, workflow_id: str | None = None
    ) -> None:
        """Create the initial RUNNING record for a new reasoning attempt.

        Idempotent: retrying `start_run` for the same run_id (e.g. a Temporal
        activity retry before any node has run) is a silent no-op as long as
        the thread_id agrees -- it must never resurrect a run that already
        moved on, so no field is reset here.
        """
        now = self._now()
        document = {
            "_id": run_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "lifecycle_state": RunLifecycleState.RUNNING.value,
            "workflow_id": workflow_id,
            "terminal_at": None,
            "expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._collection.insert_one(document)
        except Exception:
            existing = await self._collection.find_one({"_id": run_id})
            if existing is None:
                raise
            if existing.get("thread_id") != thread_id:
                raise RunBoundToDifferentThread(
                    f"run {run_id!r} already exists bound to thread_id "
                    f"{existing.get('thread_id')!r}, not {thread_id!r}"
                ) from None

    async def transition_non_terminal(
        self, *, run_id: str, lifecycle_state: RunLifecycleState
    ) -> None:
        """Move a run between RUNNING/INTERRUPTED/WAITING. Terminal states
        (COMPLETED/FAILED/CANCELLED/ABANDONED) go through
        `CheckpointRetentionPolicy.mark_terminal` instead, which also stamps
        retention across checkpoints/writes/receipts/evidence together --
        never through this method, which intentionally cannot reach a
        terminal state."""
        if lifecycle_state not in ACTIVE_LIFECYCLE_STATES:
            raise ValueError(
                f"{lifecycle_state.value} is not a non-terminal lifecycle_state; "
                "use CheckpointRetentionPolicy.mark_terminal for terminal transitions"
            )
        await self._collection.update_one(
            {"_id": run_id},
            {"$set": {"lifecycle_state": lifecycle_state.value, "updated_at": self._now()}},
        )
