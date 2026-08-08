"""Checkpoint retention: keyed to terminal state, never creation time.

A fixed TTL from creation would delete the checkpoints of a still-resumable run -- an
Analyzer clarification or a paused return can legitimately stay open longer than any
retention window, and losing its checkpoints makes the thread unresumable, defeating
the whole point of durable reasoning.

    RUNNING | INTERRUPTED | WAITING           -> expires_at = null (never expires)
    COMPLETED | FAILED | CANCELLED | ABANDONED -> expires_at = terminal_at + retention

A Mongo TTL index ignores documents with a null/absent `expires_at`, so this needs no
special-casing at the storage layer -- see the `expire_after_seconds: 0` indexes on
`reasoning_runs`/`reasoning_action_receipts`/`order_discovery_query_evidence` in
`config/platform/system_store.yaml`.

On the terminal transition, `mark_terminal` stamps `expires_at` across all four
structures together -- checkpoints, writes, receipts, and query evidence, keyed by
`thread_id` for the first two and `run_id` for the other two -- inside one Mongo
transaction, since none of them may outlive-or-predecease the execution they protect.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pymongo import AsyncMongoClient

from return_platform.platform.system_store.repository import SystemStore

_RUNS_STRUCTURE = "reasoning_runs"
_CHECKPOINTS_STRUCTURE = "reasoning_checkpoints"
_CHECKPOINT_WRITES_STRUCTURE = "reasoning_checkpoint_writes"
_RECEIPTS_STRUCTURE = "reasoning_action_receipts"
_QUERY_EVIDENCE_STRUCTURE = "order_discovery_query_evidence"


class RunLifecycleState(StrEnum):
    RUNNING = "RUNNING"
    INTERRUPTED = "INTERRUPTED"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"


ACTIVE_LIFECYCLE_STATES = frozenset(
    {RunLifecycleState.RUNNING, RunLifecycleState.INTERRUPTED, RunLifecycleState.WAITING}
)
TERMINAL_LIFECYCLE_STATES = frozenset(
    {
        RunLifecycleState.COMPLETED,
        RunLifecycleState.FAILED,
        RunLifecycleState.CANCELLED,
        RunLifecycleState.ABANDONED,
    }
)


class TerminalAtRequired(ValueError):
    """A terminal lifecycle_state was given with no terminal_at timestamp."""


class CheckpointRetentionPolicy:
    def __init__(self, *, terminal_retention_hours: float) -> None:
        if terminal_retention_hours <= 0:
            raise ValueError("terminal_retention_hours must be positive")
        self._terminal_retention = timedelta(hours=terminal_retention_hours)

    def compute_expires_at(
        self, *, lifecycle_state: RunLifecycleState, terminal_at: datetime | None
    ) -> datetime | None:
        if lifecycle_state in ACTIVE_LIFECYCLE_STATES:
            return None
        if terminal_at is None:
            raise TerminalAtRequired(
                f"terminal_at is required for terminal lifecycle_state {lifecycle_state.value}"
            )
        return terminal_at + self._terminal_retention

    async def mark_terminal(
        self,
        system_store: SystemStore,
        client: AsyncMongoClient[dict[str, object]],
        *,
        run_id: str,
        thread_id: str,
        lifecycle_state: RunLifecycleState,
        terminal_at: datetime,
    ) -> datetime:
        """Transition `reasoning_runs[run_id]` to a terminal state and stamp the same
        `expires_at` across it, its checkpoints, its checkpoint writes, its action
        receipts, and its query evidence -- in one Mongo transaction, so a crash
        mid-stamp never leaves the structures with mismatched expiries."""
        if lifecycle_state not in TERMINAL_LIFECYCLE_STATES:
            raise ValueError(f"{lifecycle_state.value} is not a terminal lifecycle_state")
        expires_at = self.compute_expires_at(
            lifecycle_state=lifecycle_state, terminal_at=terminal_at
        )
        assert expires_at is not None  # terminal states always compute a real expiry

        runs = system_store.collection(_RUNS_STRUCTURE)
        async with client.start_session() as session:
            async with await session.start_transaction():
                await runs.update_one(
                    {"_id": run_id},
                    {
                        "$set": {
                            "lifecycle_state": lifecycle_state.value,
                            "terminal_at": terminal_at,
                            "expires_at": expires_at,
                            "updated_at": terminal_at,
                        }
                    },
                    session=session,
                )
                await system_store.stamp_expiry(
                    _CHECKPOINTS_STRUCTURE,
                    {"thread_id": thread_id},
                    expires_at=expires_at,
                    session=session,
                )
                await system_store.stamp_expiry(
                    _CHECKPOINT_WRITES_STRUCTURE,
                    {"thread_id": thread_id},
                    expires_at=expires_at,
                    session=session,
                )
                await system_store.stamp_expiry(
                    _RECEIPTS_STRUCTURE,
                    {"run_id": run_id},
                    expires_at=expires_at,
                    session=session,
                )
                await system_store.stamp_expiry(
                    _QUERY_EVIDENCE_STRUCTURE,
                    {"run_id": run_id},
                    expires_at=expires_at,
                    session=session,
                )
        return expires_at
