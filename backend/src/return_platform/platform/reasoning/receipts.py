"""Idempotency receipts: a state machine, not a cached value.

LangGraph re-executes an interrupted node **from its beginning** on resume, so any side
effect before the interrupt runs again. A naive "record the result, return it on a hit"
design livelocks the interception path: an intercepted AI call returns
`InterceptionPending`, that gets cached as the action's result, and every subsequent
resume replays the pending marker and interrupts again -- forever, even after the
operator has answered.

    STARTED ──► COMPLETED | PENDING_EXTERNAL | FAILED_RETRYABLE | FAILED_FINAL
    PENDING_EXTERNAL ──► COMPLETED | FAILED_FINAL     (resolved via external_ref)

`PENDING_EXTERNAL` is never terminal and never returned as a result. It records
`external_ref` -- the `interception_id` for an intercepted AI call, the `sync_run_id`
for a targeted sync -- and a resumed node resolves through that reference rather than
blindly re-acting.

`STARTED` is written before the action, keyed deterministically on
(run_id, node_name, logical_action_id), so a crash mid-action is resolvable (the next
resume finds STARTED and must resolve by `external_ref`) rather than ambiguous (was the
action performed or not?).

Required for: AI Gateway invocation, targeted sync requests, conversation updates,
checkpoint-adjacent persistence, and audit events -- any reasoning-node side effect.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

from return_platform.platform.reasoning.errors import (
    ReceiptFailedFinal,
    ReceiptUnresolvable,
)
from return_platform.platform.system_store.repository import SystemStore

_STRUCTURE = "reasoning_action_receipts"


class ReceiptStatus(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    PENDING_EXTERNAL = "PENDING_EXTERNAL"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"


@dataclass(frozen=True, slots=True)
class ReceiptKey:
    run_id: str
    node_name: str
    logical_action_id: str

    @property
    def id(self) -> str:
        return f"{self.run_id}:{self.node_name}:{self.logical_action_id}"


@dataclass(frozen=True, slots=True)
class Receipt:
    key: ReceiptKey
    status: ReceiptStatus
    attempt: int
    external_ref: str | None
    result_ref: str | None
    failure_outcome: Mapping[str, object] | None


class ActionAlreadyStarted(RuntimeError):
    """A concurrent caller already holds this receipt's current attempt; the caller
    must resolve through `external_ref`, never blindly re-act."""


def _from_document(doc: Mapping[str, Any]) -> Receipt:
    return Receipt(
        key=ReceiptKey(
            run_id=doc["run_id"],
            node_name=doc["node_name"],
            logical_action_id=doc["logical_action_id"],
        ),
        status=ReceiptStatus(doc["status"]),
        attempt=doc["attempt"],
        external_ref=doc.get("external_ref"),
        result_ref=doc.get("result_ref"),
        failure_outcome=doc.get("failure_outcome"),
    )


class ReasoningActionReceipts:
    """Backed by `reasoning_action_receipts` -- not encrypted, so guarded raw-collection
    access (`SystemStore.collection()`) is available for the atomic transitions this
    state machine needs."""

    def __init__(self, system_store: SystemStore, *, clock: Any = None) -> None:
        self._collection = system_store.collection(_STRUCTURE)
        self._now = clock.now if clock is not None else (lambda: datetime.now(UTC))

    async def begin(self, key: ReceiptKey) -> Receipt:
        """Look up the receipt for `key`, creating it as a fresh STARTED attempt if
        none exists (or if the prior attempt ended FAILED_RETRYABLE). Never silently
        re-runs a STARTED/PENDING_EXTERNAL/COMPLETED/FAILED_FINAL receipt -- the caller
        must branch on the returned status (resolve by `external_ref`, return
        `result_ref`, or raise, respectively).
        """
        now = self._now()
        existing = await self._collection.find_one({"_id": key.id})
        if existing is None:
            document = {
                "_id": key.id,
                "run_id": key.run_id,
                "node_name": key.node_name,
                "logical_action_id": key.logical_action_id,
                "attempt": 1,
                "status": ReceiptStatus.STARTED.value,
                "external_ref": None,
                "result_ref": None,
                "failure_outcome": None,
                "started_at": now,
                "updated_at": now,
            }
            try:
                await self._collection.insert_one(document)
            except Exception:
                # Lost an insert race -- the winner's document is authoritative.
                existing = await self._collection.find_one({"_id": key.id})
                if existing is None:
                    raise
                return _from_document(existing)
            return _from_document(document)

        if ReceiptStatus(str(existing["status"])) is ReceiptStatus.FAILED_RETRYABLE:
            next_attempt = cast(int, existing["attempt"]) + 1
            updated = await self._collection.find_one_and_update(
                {"_id": key.id, "status": ReceiptStatus.FAILED_RETRYABLE.value},
                {
                    "$set": {
                        "attempt": next_attempt,
                        "status": ReceiptStatus.STARTED.value,
                        "external_ref": None,
                        "result_ref": None,
                        "failure_outcome": None,
                        "updated_at": now,
                    }
                },
                return_document=True,
            )
            if updated is not None:
                return _from_document(updated)
            # Another caller already advanced the attempt; read the current state.
            existing = await self._collection.find_one({"_id": key.id})
            assert existing is not None

        return _from_document(existing)

    async def mark_pending_external(self, key: ReceiptKey, *, external_ref: str) -> Receipt:
        return await self._transition(
            key,
            from_statuses=(ReceiptStatus.STARTED,),
            to_status=ReceiptStatus.PENDING_EXTERNAL,
            extra_fields={"external_ref": external_ref},
        )

    async def mark_completed(self, key: ReceiptKey, *, result_ref: str) -> Receipt:
        return await self._transition(
            key,
            from_statuses=(ReceiptStatus.STARTED, ReceiptStatus.PENDING_EXTERNAL),
            to_status=ReceiptStatus.COMPLETED,
            extra_fields={"result_ref": result_ref},
        )

    async def mark_failed_retryable(
        self, key: ReceiptKey, *, failure_outcome: Mapping[str, object]
    ) -> Receipt:
        return await self._transition(
            key,
            from_statuses=(ReceiptStatus.STARTED,),
            to_status=ReceiptStatus.FAILED_RETRYABLE,
            extra_fields={"failure_outcome": dict(failure_outcome)},
        )

    async def mark_failed_final(
        self, key: ReceiptKey, *, failure_outcome: Mapping[str, object]
    ) -> Receipt:
        return await self._transition(
            key,
            from_statuses=(ReceiptStatus.STARTED, ReceiptStatus.PENDING_EXTERNAL),
            to_status=ReceiptStatus.FAILED_FINAL,
            extra_fields={"failure_outcome": dict(failure_outcome)},
        )

    async def _transition(
        self,
        key: ReceiptKey,
        *,
        from_statuses: tuple[ReceiptStatus, ...],
        to_status: ReceiptStatus,
        extra_fields: Mapping[str, object],
    ) -> Receipt:
        updated = await self._collection.find_one_and_update(
            {"_id": key.id, "status": {"$in": [status.value for status in from_statuses]}},
            {
                "$set": {
                    "status": to_status.value,
                    "updated_at": self._now(),
                    **extra_fields,
                }
            },
            return_document=True,
        )
        if updated is None:
            current = await self._collection.find_one({"_id": key.id})
            observed = current["status"] if current else "<missing>"
            raise ActionAlreadyStarted(
                f"Receipt {key.id!r} is {observed!r}, not one of "
                f"{[status.value for status in from_statuses]}; cannot transition to "
                f"{to_status.value!r}"
            )
        return _from_document(updated)

    @staticmethod
    def raise_for_terminal_failure(receipt: Receipt) -> None:
        if receipt.status is ReceiptStatus.FAILED_FINAL:
            raise ReceiptFailedFinal(
                f"Reasoning action {receipt.key.id!r} failed permanently: {receipt.failure_outcome}"
            )

    @staticmethod
    def raise_unresolvable(receipt: Receipt) -> None:
        raise ReceiptUnresolvable(
            f"Receipt {receipt.key.id!r} is {receipt.status.value} with no resolvable "
            f"external_ref, and the target is not known to be idempotent -- refusing to "
            f"blind re-execute."
        )
