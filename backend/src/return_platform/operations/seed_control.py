"""In-process control state for cancellable seed operations."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from return_platform.operations.models import (
    SeedOperationStatus,
    SeedOperationView,
    utc_now,
)


class SeedOperationCancelled(RuntimeError):
    """Raised at a safe cancellation boundary."""


class SeedOperationControl:
    """Serialize seed mutations and expose lightweight progress to the UI."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cancel_event = asyncio.Event()
        self._view = SeedOperationView()

    async def begin(
        self,
        *,
        kind: str,
        record_limit: int | None,
        total_records: int,
    ) -> str:
        async with self._lock:
            if self._view.status in {
                SeedOperationStatus.RUNNING,
                SeedOperationStatus.CANCELLING,
            }:
                raise RuntimeError("A seed operation is already running.")
            operation_id = str(uuid4())
            self._cancel_event = asyncio.Event()
            self._view = SeedOperationView(
                operationId=operation_id,
                kind=kind,
                status=SeedOperationStatus.RUNNING,
                requestedRecordLimit=record_limit,
                totalRecords=total_records,
                phase="Starting",
                startedAt=utc_now(),
            )
            return operation_id

    async def snapshot(self) -> SeedOperationView:
        async with self._lock:
            return self._view.model_copy(deep=True)

    async def update(
        self,
        operation_id: str,
        *,
        processed_delta: int = 0,
        phase: str | None = None,
    ) -> None:
        async with self._lock:
            if self._view.operationId != operation_id:
                return
            update: dict[str, object] = {
                "processedRecords": min(
                    self._view.totalRecords,
                    self._view.processedRecords + max(0, processed_delta),
                )
            }
            if phase is not None:
                update["phase"] = phase
            self._view = self._view.model_copy(update=update)

    async def request_cancel(self) -> SeedOperationView:
        async with self._lock:
            if self._view.status is SeedOperationStatus.RUNNING:
                self._cancel_event.set()
                self._view = self._view.model_copy(
                    update={
                        "status": SeedOperationStatus.CANCELLING,
                        "phase": "Stopping at a safe boundary",
                    }
                )
            return self._view.model_copy(deep=True)

    def raise_if_cancelled(self, operation_id: str) -> None:
        if self._view.operationId == operation_id and self._cancel_event.is_set():
            raise SeedOperationCancelled("Seed operation stopped by the user.")

    async def wait_for_cancel(self, operation_id: str) -> None:
        if self._view.operationId != operation_id:
            return
        await self._cancel_event.wait()

    async def finish(
        self,
        operation_id: str,
        status: SeedOperationStatus,
        *,
        phase: str,
        error: str | None = None,
    ) -> None:
        async with self._lock:
            if self._view.operationId != operation_id:
                return
            self._view = self._view.model_copy(
                update={
                    "status": status,
                    "phase": phase,
                    "finishedAt": utc_now(),
                    "error": error,
                }
            )
