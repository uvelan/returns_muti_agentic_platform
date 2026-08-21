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


def _utc(value: Any, *, fallback: datetime) -> datetime:
    """A stored instant as an aware UTC datetime.

    MongoDB has no time zone: a `datetime` written aware comes back naive, and
    handing a naive one to `.isoformat()` produces a stamp with no offset that
    every downstream reader then has to guess about. `fallback` covers a record
    written before this field was relied upon.
    """
    if not isinstance(value, datetime):
        return fallback
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _storable(value: datetime) -> datetime:
    """The instant at the resolution MongoDB will hand back.

    BSON dates are milliseconds. A `datetime` carrying microseconds is truncated
    on the way in, so the value the first caller holds and the value a retry
    reads back differ in their last three digits -- invisible in a log and fatal
    to anything that hashes the ISO string, which `as_of` is: it travels in the
    reasoning request and therefore in its digest. Truncating here makes the
    returned instant identical to the stored one by construction rather than by
    luck.
    """
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


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
    ) -> datetime:
        """Create the initial RUNNING record for a new reasoning attempt.

        Idempotent: retrying `start_run` for the same run_id (e.g. a Temporal
        activity retry before any node has run) is a silent no-op as long as
        the thread_id agrees -- it must never resurrect a run that already
        moved on, so no field is reset here.

        **Returns the instant this attempt began**, which is `created_at` on the
        first call and the *same* `created_at` on every retry. That makes it the
        one durable clock read a reasoning attempt has, which is what the caller
        needs: an attempt that re-reads the wall clock asks the model a
        materially different question the second time -- a different "today" for
        every date filter, and a different request digest, so a held request
        answered by an operator is never recognised on the way back in.
        """
        now = _storable(self._now())
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
            return _utc(existing.get("created_at"), fallback=now)
        return now

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
