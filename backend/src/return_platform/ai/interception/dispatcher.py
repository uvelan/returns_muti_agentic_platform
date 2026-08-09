"""Turns an answered interception into a durable resume delivery.

Phase 14 asks for an "at-least-once resume worker". One already exists and is
good: `platform/reasoning/resume_worker.py` claims `reasoning_resume_commands`
rows under a lease, delivers them as Temporal signals with exponential backoff,
and deduplicates workflow-side on `command_id`. Building a second worker beside
it would mean two lease disciplines, two backoff policies and two places to get
at-least-once wrong.

So this does not deliver anything. It **bridges**: an interception that a human
has answered becomes a `reasoning_resume_commands` row, and the existing worker
takes it from there.

**Why the interception record is its own outbox.** The answer and the resume
intent live in two different collections, so a single write cannot cover both,
and a cross-collection transaction would be a fourth thing to keep correct.
Instead nothing is written at answer time beyond the answer itself: `ANSWERED`
with no `resume_enqueued_at` *is* the queue. The dispatcher reads that queue and
enqueues, which means the only durable state is the interception itself.

**At-least-once, honestly.** A crash between "wrote the resume command" and
"stamped the interception" replays the enqueue on the next pass. That is safe
because `reasoning_resume_commands.command_id` carries a unique index and the
command id is derived from the interception id — so the second insert is
rejected by the database rather than producing a duplicate signal. The stamp is
an optimisation that keeps the queue short, not the correctness mechanism.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from return_platform.ai.interception.store import AI_INTERCEPTIONS, METADATA_FIELDS
from return_platform.platform.system_store.repository import SystemStore

logger = logging.getLogger(__name__)

RESUME_COMMANDS = "reasoning_resume_commands"

#: The signal the Order Discovery workflow listens for. Named here rather than
#: passed in because a resume that reached the right workflow with the wrong
#: signal name would silently do nothing.
DEFAULT_RESUME_SIGNAL = "resume_reasoning"


def resume_command_id(interception_id: str) -> str:
    """Derived, not random.

    This is what makes the replay-after-crash safe: the same interception always
    produces the same command id, so a duplicate enqueue collides with the
    unique index instead of delivering a second signal.
    """
    return f"interception:{interception_id}"


class InterceptionResumeDispatcher:
    def __init__(self, system_store: SystemStore, *, batch_size: int = 50) -> None:
        self._store = system_store
        self._interceptions = system_store.read_only(AI_INTERCEPTIONS)
        self._commands = system_store.collection(RESUME_COMMANDS)
        self._batch_size = batch_size

    async def dispatch_once(self) -> int:
        """Enqueue every answered-but-not-yet-enqueued interception.

        Returns how many were enqueued, so a caller can distinguish "idle" from
        "working" without inspecting the store.
        """
        cursor = (
            self._interceptions.find(
                {"status": "ANSWERED", "resume_enqueued_at": {"$exists": False}}
            )
            .sort("answered_at", 1)
            .limit(self._batch_size)
        )
        enqueued = 0
        async for document in cursor:
            if await self._enqueue(document):
                enqueued += 1
        return enqueued

    async def _enqueue(self, document: dict[str, Any]) -> bool:
        interception_id = str(document["interception_id"])
        resume = document.get("resume") or {}
        now = datetime.now(UTC)
        command_id = resume_command_id(interception_id)
        try:
            await self._commands.insert_one(
                {
                    "command_id": command_id,
                    "run_id": resume.get("run_id"),
                    "workflow_id": resume.get("workflow_id"),
                    "signal": DEFAULT_RESUME_SIGNAL,
                    "status": "PENDING",
                    "attempt_count": 0,
                    "next_attempt_at": now,
                    "lease_owner": None,
                    "lease_until": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except DuplicateKeyError:
            # Already enqueued by a previous pass that crashed before stamping.
            # Not an error: this is exactly the case the derived command id
            # exists to make safe. Fall through and stamp.
            logger.debug("interception_resume_already_enqueued", extra={"command_id": command_id})
        await self._stamp(document, now)
        return True

    async def _stamp(self, document: dict[str, Any], when: datetime) -> None:
        """Mark the interception as enqueued so it leaves the dispatcher's queue.

        Written through the guarded store, not the raw collection: this
        structure is `encrypted: true`, and a plaintext write to it must be
        refused even when the field being added is only a timestamp.
        """
        updated = dict(document)
        updated["resume_enqueued_at"] = when
        await self._store.replace_one(
            AI_INTERCEPTIONS,
            {"interception_id": document["interception_id"]},
            updated,
            allowed_metadata_fields=METADATA_FIELDS | {"resume_enqueued_at"},
        )
