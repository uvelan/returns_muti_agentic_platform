"""Abandonment: idle reasoning runs become a visible queue, never a silent hazard.

"Live runs never expire" (retention.py) would grow without bound when a user simply
never answers a clarification. A sweeper moves idle INTERRUPTED/WAITING runs to
ABANDONED, starting the retention clock -- but abandonment must not race pending
external work: PENDING_EXTERNAL means a run is waiting on something that can still
complete, so abandoning on a bare idle timer would let an operator answer an
interception on day 31 and have the resume worker deliver to a run already heading for
deletion. The sweeper therefore skips a run unless ALL five preconditions hold; a run
idle past the threshold but failing one is flagged ABANDONMENT_BLOCKED with the
blocking reference listed, for operator action.

Forced abandonment is one Mongo transaction (state changes + the resume_command
outbox row, together) followed by a separate, at-least-once, deduplicated Temporal
signal delivery (resume_worker.py) -- a Temporal signal cannot join a Mongo
transaction, so claiming one atomic step across both would be false: a crash after
commit but before the signal would strand the workflow forever if the outbox row
weren't already durably recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pymongo import AsyncMongoClient
from temporalio.client import Client as TemporalClient

from return_platform.platform.reasoning.checkpoint import SystemStoreCheckpointSaver
from return_platform.platform.reasoning.errors import AbandonmentBlocked
from return_platform.platform.reasoning.receipts import ReceiptStatus
from return_platform.platform.reasoning.retention import (
    CheckpointRetentionPolicy,
    RunLifecycleState,
)
from return_platform.platform.system_store.repository import SystemStore

_RUNS_STRUCTURE = "reasoning_runs"
_RECEIPTS_STRUCTURE = "reasoning_action_receipts"
_INTERCEPTIONS_STRUCTURE = "ai_interceptions"
_RESUME_COMMANDS_STRUCTURE = "reasoning_resume_commands"

_INTERRUPT_CHANNEL = "__interrupt__"


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    thread_id: str
    lifecycle_state: RunLifecycleState
    workflow_id: str | None
    idle_since: datetime


class AbandonmentPreconditionChecker:
    """The five checks a sweeper (or an operator's forced-abandonment request) must
    pass before a run may move to ABANDONED. Each check queries the real collection it
    names -- `ai_interceptions` has no writer yet (a later AI Gateway phase adds one),
    so it correctly finds nothing today and will correctly start blocking once that
    phase wires real interception records."""

    def __init__(
        self,
        system_store: SystemStore,
        checkpoint_saver: SystemStoreCheckpointSaver,
        temporal_client: TemporalClient | None = None,
    ) -> None:
        self._store = system_store
        self._checkpoints = checkpoint_saver
        self._temporal = temporal_client

    async def blocking_reference(self, run: RunRecord) -> str | None:
        """Returns the first blocking reference found, or None if every precondition
        is clear."""
        if await self._has_unresolved_clarification_interrupt(run):
            return f"unresolved_interrupt:{run.thread_id}"
        if reference := await self._has_open_receipt(run):
            return reference
        if reference := await self._has_open_interception(run):
            return reference
        if reference := await self._has_pending_resume_command(run):
            return reference
        if reference := await self._has_active_temporal_wait(run):
            return reference
        return None

    async def _has_unresolved_clarification_interrupt(self, run: RunRecord) -> bool:
        latest = await self._checkpoints.aget_tuple({"configurable": {"thread_id": run.thread_id}})
        if latest is None or latest.pending_writes is None:
            return False
        return any(channel == _INTERRUPT_CHANNEL for _, channel, _ in latest.pending_writes)

    async def _has_open_receipt(self, run: RunRecord) -> str | None:
        receipts = self._store.read_only(_RECEIPTS_STRUCTURE)
        doc = await receipts.find_one(
            {
                "run_id": run.run_id,
                "status": {
                    "$in": [ReceiptStatus.STARTED.value, ReceiptStatus.PENDING_EXTERNAL.value]
                },
            }
        )
        return f"receipt:{doc['_id']}" if doc else None

    async def _has_open_interception(self, run: RunRecord) -> str | None:
        interceptions = self._store.read_only(_INTERCEPTIONS_STRUCTURE)
        doc = await interceptions.find_one(
            {"run_id": run.run_id, "status": {"$nin": ["RESOLVED", "CANCELLED"]}}
        )
        return f"interception:{doc['_id']}" if doc else None

    async def _has_pending_resume_command(self, run: RunRecord) -> str | None:
        commands = self._store.read_only(_RESUME_COMMANDS_STRUCTURE)
        doc = await commands.find_one({"run_id": run.run_id, "status": "PENDING"})
        return f"resume_command:{doc['_id']}" if doc else None

    async def _has_active_temporal_wait(self, run: RunRecord) -> str | None:
        if run.workflow_id is None or self._temporal is None:
            return None
        handle = self._temporal.get_workflow_handle(run.workflow_id)
        description = await handle.describe()
        if description.status is not None and description.status.name == "RUNNING":
            return f"temporal_workflow:{run.workflow_id}"
        return None


class ForcedAbandonment:
    """One Mongo transaction: run -> ABANDONED, open interceptions -> CANCELLED,
    STARTED/PENDING_EXTERNAL receipts -> FAILED_FINAL, expires_at stamped, and a
    reasoning_resume_commands row inserted PENDING -- all committed together. The
    Temporal signal itself is delivered afterward, separately, by resume_worker.py."""

    def __init__(
        self,
        system_store: SystemStore,
        client: AsyncMongoClient[dict[str, object]],
        retention: CheckpointRetentionPolicy,
    ) -> None:
        self._store = system_store
        self._client = client
        self._retention = retention

    async def abandon(self, run: RunRecord, *, checker: AbandonmentPreconditionChecker) -> str:
        """Returns the new `command_id` the resume worker will deliver. Raises
        `AbandonmentBlocked` if any precondition still fails -- callers (an operator
        action, or the sweeper after its own idle-threshold check) must not bypass
        this re-check, since state may have changed since the caller last looked."""
        blocking = await checker.blocking_reference(run)
        if blocking is not None:
            raise AbandonmentBlocked(run.run_id, blocking_reference=blocking)

        now = datetime.now(UTC)
        expires_at = self._retention.compute_expires_at(
            lifecycle_state=RunLifecycleState.ABANDONED, terminal_at=now
        )
        command_id = str(uuid.uuid4())

        runs = self._store.collection(_RUNS_STRUCTURE)
        interceptions = self._store.collection(_INTERCEPTIONS_STRUCTURE)
        receipts = self._store.collection(_RECEIPTS_STRUCTURE)
        resume_commands = self._store.collection(_RESUME_COMMANDS_STRUCTURE)

        async with self._client.start_session() as session:
            async with await session.start_transaction():
                await runs.update_one(
                    {"_id": run.run_id},
                    {
                        "$set": {
                            "lifecycle_state": RunLifecycleState.ABANDONED.value,
                            "terminal_at": now,
                            "expires_at": expires_at,
                            "updated_at": now,
                        }
                    },
                    session=session,
                )
                await interceptions.update_many(
                    {"run_id": run.run_id, "status": {"$nin": ["RESOLVED", "CANCELLED"]}},
                    {"$set": {"status": "CANCELLED", "updated_at": now}},
                    session=session,
                )
                await receipts.update_many(
                    {
                        "run_id": run.run_id,
                        "status": {
                            "$in": [
                                ReceiptStatus.STARTED.value,
                                ReceiptStatus.PENDING_EXTERNAL.value,
                            ]
                        },
                    },
                    {
                        "$set": {
                            "status": ReceiptStatus.FAILED_FINAL.value,
                            "failure_outcome": {"reason": "REASONING_ABANDONED"},
                            "updated_at": now,
                        }
                    },
                    session=session,
                )
                await self._store.stamp_expiry(
                    "reasoning_checkpoints",
                    {"thread_id": run.thread_id},
                    expires_at=expires_at,
                    session=session,
                )
                await self._store.stamp_expiry(
                    "reasoning_checkpoint_writes",
                    {"thread_id": run.thread_id},
                    expires_at=expires_at,
                    session=session,
                )
                await self._store.stamp_expiry(
                    _RECEIPTS_STRUCTURE,
                    {"run_id": run.run_id},
                    expires_at=expires_at,
                    session=session,
                )
                await resume_commands.insert_one(
                    {
                        "_id": command_id,
                        "command_id": command_id,
                        "run_id": run.run_id,
                        "workflow_id": run.workflow_id,
                        "signal": "REASONING_ABANDONED",
                        "status": "PENDING",
                        "next_attempt_at": now,
                        "attempt_count": 0,
                        "lease_owner": None,
                        "lease_until": None,
                        "created_at": now,
                        "updated_at": now,
                        "delivered_at": None,
                    },
                    session=session,
                )
        return command_id


class AbandonmentSweeper:
    def __init__(
        self,
        system_store: SystemStore,
        checker: AbandonmentPreconditionChecker,
        forced_abandonment: ForcedAbandonment,
        *,
        abandon_after: timedelta,
    ) -> None:
        self._store = system_store
        self._checker = checker
        self._forced_abandonment = forced_abandonment
        self._abandon_after = abandon_after

    async def sweep(self) -> tuple[Sequence[str], Sequence[tuple[str, str]]]:
        """Returns (abandoned_run_ids, [(run_id, blocking_reference), ...])."""
        cutoff = datetime.now(UTC) - self._abandon_after
        runs = self._store.read_only(_RUNS_STRUCTURE)
        abandoned: list[str] = []
        blocked: list[tuple[str, str]] = []
        async for doc in runs.find(
            {
                "lifecycle_state": {
                    "$in": [
                        RunLifecycleState.INTERRUPTED.value,
                        RunLifecycleState.WAITING.value,
                    ]
                },
                "updated_at": {"$lt": cutoff},
            }
        ):
            run = RunRecord(
                run_id=doc["run_id"],
                thread_id=doc["thread_id"],
                lifecycle_state=RunLifecycleState(doc["lifecycle_state"]),
                workflow_id=doc.get("workflow_id"),
                idle_since=doc["updated_at"],
            )
            blocking = await self._checker.blocking_reference(run)
            if blocking is not None:
                blocked.append((run.run_id, blocking))
                continue
            await self._forced_abandonment.abandon(run, checker=self._checker)
            abandoned.append(run.run_id)
        return abandoned, blocked
