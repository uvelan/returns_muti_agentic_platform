"""Delivers `reasoning_resume_commands` as Temporal workflow signals, at-least-once.

A Temporal signal cannot join the Mongo transaction that decides a run is ABANDONED
(`abandonment.py`), so the transaction durably records *intent to signal* (the
resume_command row) and this worker delivers the signal afterward, separately.

Lease/claim/backoff shape mirrors `operations.integrations.outbox.IntegrationOutboxDispatcher`
-- the same discipline for the same reason (a claimed-but-not-yet-delivered command must
be resumable by another worker instance after a crash, never silently dropped or
double-delivered without detection).

Idempotency: delivery is deduplicated workflow-side on `command_id` (the signal payload
carries it), so a redelivery after a crash between "signal sent" and "marked DELIVERED"
is safe -- the workflow itself must ignore a `command_id` it has already processed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from temporalio.client import Client as TemporalClient

from return_platform.platform.system_store.repository import SystemStore

logger = logging.getLogger(__name__)

_STRUCTURE = "reasoning_resume_commands"


@dataclass(frozen=True, slots=True)
class ResumeCommand:
    command_id: str
    run_id: str
    workflow_id: str | None
    signal: str
    attempt_count: int


class ReasoningResumeWorker:
    def __init__(
        self,
        system_store: SystemStore,
        temporal_client: TemporalClient,
        *,
        worker_id: str | None = None,
    ) -> None:
        self._collection = system_store.collection(_STRUCTURE)
        self._temporal = temporal_client
        self._worker_id = worker_id or f"reasoning-resume-{uuid.uuid4()}"

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("command_id", unique=True)
        await self._collection.create_index([("status", ASCENDING), ("next_attempt_at", ASCENDING)])
        await self._collection.create_index("lease_until")

    async def claim(self, *, lease_seconds: int = 60) -> ResumeCommand | None:
        now = datetime.now(UTC)
        document: dict[str, Any] | None = await self._collection.find_one_and_update(
            {
                "status": "PENDING",
                "next_attempt_at": {"$lte": now},
                "$or": [
                    {"lease_until": None},
                    {"lease_until": {"$lt": now}},
                    {"lease_owner": self._worker_id},
                ],
            },
            {
                "$set": {
                    "lease_owner": self._worker_id,
                    "lease_until": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                },
                "$inc": {"attempt_count": 1},
            },
            sort=[("next_attempt_at", ASCENDING), ("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return ResumeCommand(
            command_id=document["command_id"],
            run_id=document["run_id"],
            workflow_id=document.get("workflow_id"),
            signal=document["signal"],
            attempt_count=document["attempt_count"],
        )

    async def _mark_delivered(self, command: ResumeCommand) -> None:
        now = datetime.now(UTC)
        await self._collection.update_one(
            {"command_id": command.command_id, "lease_owner": self._worker_id},
            {
                "$set": {
                    "status": "DELIVERED",
                    "delivered_at": now,
                    "updated_at": now,
                    "lease_owner": None,
                    "lease_until": None,
                }
            },
        )

    async def _mark_retry(self, command: ResumeCommand) -> None:
        now = datetime.now(UTC)
        delay_seconds = min(3_600, 2 ** min(command.attempt_count, 10))
        await self._collection.update_one(
            {"command_id": command.command_id, "lease_owner": self._worker_id},
            {
                "$set": {
                    "status": "PENDING",
                    "next_attempt_at": now + timedelta(seconds=delay_seconds),
                    "updated_at": now,
                    "lease_owner": None,
                    "lease_until": None,
                }
            },
        )

    async def deliver_once(self) -> bool:
        command = await self.claim()
        if command is None:
            return False
        if command.workflow_id is None:
            # Nothing to signal -- e.g. a reasoning run abandoned before any Temporal
            # workflow was ever bound to it. Delivered is the correct terminal state:
            # there is no recipient, not a failure to retry.
            await self._mark_delivered(command)
            return True
        try:
            handle = self._temporal.get_workflow_handle(command.workflow_id)
            await handle.signal(command.signal, command.command_id)
        except Exception:
            logger.warning(
                "reasoning_resume_signal_retry",
                extra={"command_id": command.command_id, "workflow_id": command.workflow_id},
                exc_info=True,
            )
            await self._mark_retry(command)
        else:
            await self._mark_delivered(command)
        return True

    async def run_forever(self, *, idle_seconds: float = 1.0) -> None:
        await self.ensure_indexes()
        while True:
            processed = await self.deliver_once()
            if not processed:
                await asyncio.sleep(idle_seconds)
