"""Durable order-sync job lifecycle with leases, retries, and worker ownership."""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from return_platform.v2.models import (
    FullSyncRequest,
    PartialSyncRequest,
    SyncResult,
    V2Model,
)
from return_platform.v2.services import OrderSyncService, V2ConflictError, V2NotFoundError


class SyncJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SyncJob(V2Model):
    job_id: str = Field(alias="jobId")
    sync_type: Literal["PARTIAL_ORDER_SYNC", "FULL_ORDER_SYNC"] = Field(alias="syncType")
    request_payload: dict[str, Any] = Field(alias="requestPayload")
    idempotency_key: str = Field(alias="idempotencyKey")
    status: SyncJobStatus = SyncJobStatus.QUEUED
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, alias="maxAttempts", ge=1, le=10)
    lease_owner: str | None = Field(default=None, alias="leaseOwner")
    lease_expires_at: datetime | None = Field(default=None, alias="leaseExpiresAt")
    next_attempt_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), alias="nextAttemptAt"
    )
    result_request_id: str | None = Field(default=None, alias="resultRequestId")
    safe_error: str | None = Field(default=None, alias="safeError", max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="updatedAt")


class JobClaimRequest(V2Model):
    worker_id: str = Field(alias="workerId", min_length=1, max_length=200)
    lease_seconds: int = Field(default=30, alias="leaseSeconds", ge=5, le=300)


class DurableOrderSyncCoordinator:
    """Coordinates restart-safe jobs while delegating sync invariants to OrderSyncService."""

    def __init__(self, sync_service: OrderSyncService) -> None:
        self._sync_service = sync_service
        self._jobs: dict[str, SyncJob] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def use_sync_service(self, sync_service: OrderSyncService) -> None:
        self._sync_service = sync_service

    def snapshot(self) -> dict[str, Any]:
        return {
            "jobs": [item.model_dump(mode="json", by_alias=True) for item in self._jobs.values()],
            "idempotency": copy.deepcopy(self._idempotency),
        }

    def restore(self, payload: dict[str, Any]) -> None:
        self._jobs = {
            item.job_id: item
            for raw in payload.get("jobs", [])
            for item in (SyncJob.model_validate(raw),)
        }
        raw_keys = payload.get("idempotency", {})
        if not isinstance(raw_keys, dict):
            raise ValueError("Persisted sync-job idempotency state must be an object")
        self._idempotency = {str(key): str(value) for key, value in raw_keys.items()}

    async def enqueue_partial(
        self, request: PartialSyncRequest, max_attempts: int = 3
    ) -> SyncJob:
        return await self._enqueue(
            "PARTIAL_ORDER_SYNC",
            request.model_dump(mode="json", by_alias=True),
            request.idempotency_key,
            max_attempts,
        )

    async def enqueue_full(self, request: FullSyncRequest, max_attempts: int = 3) -> SyncJob:
        return await self._enqueue(
            "FULL_ORDER_SYNC",
            request.model_dump(mode="json", by_alias=True),
            request.idempotency_key,
            max_attempts,
        )

    async def _enqueue(
        self,
        sync_type: Literal["PARTIAL_ORDER_SYNC", "FULL_ORDER_SYNC"],
        request_payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int,
    ) -> SyncJob:
        async with self._lock:
            existing_id = self._idempotency.get(idempotency_key)
            if existing_id:
                return copy.deepcopy(self._jobs[existing_id])
            job = SyncJob(
                job_id=str(uuid.uuid4()),
                sync_type=sync_type,
                request_payload=request_payload,
                idempotency_key=idempotency_key,
                max_attempts=max_attempts,
            )
            self._jobs[job.job_id] = job
            self._idempotency[idempotency_key] = job.job_id
            return copy.deepcopy(job)

    async def get(self, job_id: str) -> SyncJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise V2NotFoundError(f"Order sync job {job_id} was not found")
        return copy.deepcopy(job)

    async def claim(self, request: JobClaimRequest) -> SyncJob | None:
        now = datetime.now(UTC)
        async with self._lock:
            eligible = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if (
                        job.status in {SyncJobStatus.QUEUED, SyncJobStatus.RETRY_SCHEDULED}
                        and job.next_attempt_at <= now
                    )
                    or (
                        job.status is SyncJobStatus.RUNNING
                        and job.lease_expires_at is not None
                        and job.lease_expires_at <= now
                    )
                ),
                key=lambda item: (item.next_attempt_at, item.created_at, item.job_id),
            )
            if not eligible:
                return None
            current = eligible[0]
            claimed = current.model_copy(
                update={
                    "status": SyncJobStatus.RUNNING,
                    "attempts": current.attempts + 1,
                    "lease_owner": request.worker_id,
                    "lease_expires_at": now + timedelta(seconds=request.lease_seconds),
                    "updated_at": now,
                    "safe_error": None,
                }
            )
            self._jobs[claimed.job_id] = claimed
            return copy.deepcopy(claimed)

    async def heartbeat(self, job_id: str, request: JobClaimRequest) -> SyncJob:
        now = datetime.now(UTC)
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise V2NotFoundError(f"Order sync job {job_id} was not found")
            self._require_owner(current, request.worker_id, now)
            updated = current.model_copy(
                update={
                    "lease_expires_at": now + timedelta(seconds=request.lease_seconds),
                    "updated_at": now,
                }
            )
            self._jobs[job_id] = updated
            return copy.deepcopy(updated)

    async def execute(self, job_id: str, worker_id: str) -> tuple[SyncJob, SyncResult | None]:
        now = datetime.now(UTC)
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise V2NotFoundError(f"Order sync job {job_id} was not found")
            self._require_owner(current, worker_id, now)
        try:
            result = (
                await self._sync_service.partial(
                    PartialSyncRequest.model_validate(current.request_payload)
                )
                if current.sync_type == "PARTIAL_ORDER_SYNC"
                else await self._sync_service.full(
                    FullSyncRequest.model_validate(current.request_payload)
                )
            )
        except Exception as exc:
            failed = await self._record_failure(current, type(exc).__name__)
            return failed, None
        completed = current.model_copy(
            update={
                "status": SyncJobStatus.COMPLETED,
                "result_request_id": result.request_id,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": datetime.now(UTC),
            }
        )
        async with self._lock:
            latest = self._jobs.get(job_id)
            if latest is None or latest.lease_owner != worker_id:
                raise V2ConflictError("Order sync lease changed during execution")
            self._jobs[job_id] = completed
        return copy.deepcopy(completed), result

    async def _record_failure(self, current: SyncJob, error_type: str) -> SyncJob:
        terminal = current.attempts >= current.max_attempts
        updated = current.model_copy(
            update={
                "status": SyncJobStatus.FAILED if terminal else SyncJobStatus.RETRY_SCHEDULED,
                "safe_error": f"Sync attempt failed: {error_type}",
                "lease_owner": None,
                "lease_expires_at": None,
                "next_attempt_at": datetime.now(UTC)
                + timedelta(seconds=min(60, 2 ** max(0, current.attempts - 1))),
                "updated_at": datetime.now(UTC),
            }
        )
        async with self._lock:
            self._jobs[current.job_id] = updated
        return copy.deepcopy(updated)

    @staticmethod
    def _require_owner(job: SyncJob, worker_id: str, now: datetime) -> None:
        if job.status is not SyncJobStatus.RUNNING:
            raise V2ConflictError("Order sync job is not running")
        if job.lease_owner != worker_id:
            raise V2ConflictError("Order sync job is leased by another worker")
        if job.lease_expires_at is None or job.lease_expires_at <= now:
            raise V2ConflictError("Order sync job lease has expired")
