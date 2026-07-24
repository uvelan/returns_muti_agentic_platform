"""Durable Data Console import/export jobs with bounded artifacts and worker leases."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/data-console/v1", tags=["Jobs"])
_MAX_CONTENT_BYTES: Final = 10 * 1024 * 1024
_MAX_RECORDS: Final = 10_000
_DEFAULT_MAX_ATTEMPTS: Final = 3
_PROGRESS_BATCH_SIZE: Final = 100


class JobMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    totalRecords: int = 0
    processedRecords: int = 0
    failedRecords: int = 0
    progressPercentage: int = Field(default=0, ge=0, le=100)


class JobIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["INFO", "WARNING", "ERROR"]
    message: str
    context: str | None = None
    recordIdentifier: str | None = None


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: Literal["IMPORT", "EXPORT", "GENERATION", "VALIDATION", "SYNCHRONIZATION"]
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    target: str
    owner: str
    createdAt: datetime
    startedAt: datetime | None = None
    completedAt: datetime | None = None
    metrics: JobMetrics
    issues: list[JobIssue] = Field(default_factory=list)
    attempts: int = 0
    maxAttempts: int = _DEFAULT_MAX_ATTEMPTS
    cancellationRequestedAt: datetime | None = None


class CreateImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1, max_length=128)
    format: Literal["CSV", "JSON", "JSONL"]
    duplicatePolicy: Literal["SKIP", "OVERWRITE", "FAIL"]
    fieldMapping: dict[str, str] = Field(default_factory=dict, max_length=256)
    content: str = Field(min_length=1, max_length=_MAX_CONTENT_BYTES)


class CreateExportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=128)
    format: Literal["CSV", "JSON", "JSONL"]
    fields: list[str] = Field(default_factory=list, max_length=256)


class JobService:
    """Persist commands separately from execution and expose lease-safe worker operations."""

    def __init__(self, client: AsyncMongoClient[dict[str, object]], database: str) -> None:
        self._client = client
        self._db = client[database]
        self._jobs = self._db["jobs"]
        self._commands = self._db["job_commands"]
        self._artifacts = self._db["job_artifacts"]
        self._workspaces = self._db["workspaces"]
        self._records = self._db["sandbox_records"]
        self._audit = self._db["audit"]

    @staticmethod
    def _view(document: dict[str, Any]) -> Job:
        return Job.model_validate(
            {
                "id": str(document["_id"]),
                "type": document["type"],
                "status": document["status"],
                "target": document.get("target", "unknown"),
                "owner": document.get("owner", "unknown"),
                "createdAt": document["createdAt"],
                "startedAt": document.get("startedAt"),
                "completedAt": document.get("completedAt"),
                "metrics": document.get("metrics", {}),
                "issues": document.get("issues", []),
                "attempts": document.get("attempts", 0),
                "maxAttempts": document.get("maxAttempts", _DEFAULT_MAX_ATTEMPTS),
                "cancellationRequestedAt": document.get("cancellationRequestedAt"),
            }
        )

    @staticmethod
    def _active_filter() -> dict[str, Any]:
        return {"$or": [{"deletedAt": None}, {"deletedAt": {"$exists": False}}]}

    async def ensure_indexes(self) -> None:
        await self._jobs.create_index([("createdAt", DESCENDING)])
        await self._jobs.create_index([("status", ASCENDING), ("leaseUntil", ASCENDING)])
        await self._jobs.create_index("idempotencyKey", unique=True, sparse=True)
        await self._records.create_index(
            [("workspaceId", ASCENDING), ("recordKey", ASCENDING)], unique=True
        )

    async def list_jobs(self, job_type: str | None, job_status: str | None) -> list[Job]:
        query: dict[str, Any] = {}
        if job_type:
            query["type"] = job_type
        if job_status:
            query["status"] = job_status
        cursor = self._jobs.find(query).sort("createdAt", DESCENDING).limit(500)
        return [self._view(cast(dict[str, Any], document)) async for document in cursor]

    async def get_job(self, job_id: str) -> Job | None:
        document = await self._jobs.find_one({"_id": job_id})
        return None if document is None else self._view(cast(dict[str, Any], document))

    async def _enqueue(
        self,
        *,
        job_type: Literal["IMPORT", "EXPORT"],
        target: str,
        owner: str,
        command: dict[str, Any],
        idempotency_key: str | None,
    ) -> Job:
        await self.ensure_indexes()
        now = datetime.now(UTC)
        job_id = f"job-{uuid.uuid4().hex}"
        document: dict[str, Any] = {
            "_id": job_id,
            "type": job_type,
            "status": "PENDING",
            "target": target,
            "owner": owner,
            "createdAt": now,
            "startedAt": None,
            "completedAt": None,
            "metrics": JobMetrics().model_dump(),
            "issues": [],
            "attempts": 0,
            "maxAttempts": _DEFAULT_MAX_ATTEMPTS,
            "cancellationRequestedAt": None,
            "leaseOwner": None,
            "leaseUntil": None,
            "idempotencyKey": idempotency_key,
        }

        async def transaction(session: Any) -> None:
            await self._jobs.insert_one(document, session=session)
            await self._commands.insert_one(
                {
                    "_id": job_id,
                    "type": job_type,
                    "payload": command,
                    "createdAt": now,
                },
                session=session,
            )
            await self._audit.insert_one(
                {
                    "_id": str(uuid.uuid4()),
                    "action": f"JOB_{job_type}_QUEUED",
                    "actor": owner,
                    "target": job_id,
                    "timestamp": now,
                    "details": {"target": target},
                },
                session=session,
            )

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(transaction)
        except DuplicateKeyError:
            if idempotency_key is None:
                raise
            existing = await self._jobs.find_one({"idempotencyKey": idempotency_key})
            if existing is None:
                raise
            return self._view(cast(dict[str, Any], existing))
        return self._view(document)

    async def enqueue_import(
        self,
        payload: CreateImportPayload,
        owner: str,
        idempotency_key: str | None,
    ) -> Job:
        if len(payload.content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise ValueError("Import exceeds the 10MB limit.")
        return await self._enqueue(
            job_type="IMPORT",
            target=payload.target,
            owner=owner,
            command=payload.model_dump(),
            idempotency_key=idempotency_key,
        )

    async def enqueue_export(
        self,
        payload: CreateExportPayload,
        owner: str,
        idempotency_key: str | None,
    ) -> Job:
        return await self._enqueue(
            job_type="EXPORT",
            target=payload.source,
            owner=owner,
            command=payload.model_dump(),
            idempotency_key=idempotency_key,
        )

    async def cancel(self, job_id: str, actor_id: str) -> Job:
        now = datetime.now(UTC)
        document = await self._jobs.find_one({"_id": job_id})
        if document is None:
            raise KeyError(job_id)
        job_status = str(document.get("status"))
        if job_status in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError(f"Job in {job_status} state cannot be cancelled.")
        updates: dict[str, Any] = {"cancellationRequestedAt": now}
        if job_status == "PENDING":
            updates.update(
                {
                    "status": "CANCELLED",
                    "completedAt": now,
                    "leaseOwner": None,
                    "leaseUntil": None,
                }
            )
        updated = await self._jobs.find_one_and_update(
            {"_id": job_id, "status": job_status},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ValueError("Job state changed; reload before cancelling.")
        await self._audit.insert_one(
            {
                "_id": str(uuid.uuid4()),
                "action": "JOB_CANCEL_REQUESTED",
                "actor": actor_id,
                "target": job_id,
                "timestamp": now,
                "details": {"previousStatus": job_status},
            }
        )
        return self._view(cast(dict[str, Any], updated))

    async def retry(self, job_id: str, actor_id: str) -> Job:
        now = datetime.now(UTC)
        document = await self._jobs.find_one({"_id": job_id})
        if document is None:
            raise KeyError(job_id)
        if document.get("status") not in {"FAILED", "CANCELLED"}:
            raise ValueError("Only failed or cancelled jobs can be retried.")
        if int(str(document.get("attempts", 0))) >= int(
            str(document.get("maxAttempts", _DEFAULT_MAX_ATTEMPTS))
        ):
            raise ValueError("Job retry limit has been reached.")
        updated = await self._jobs.find_one_and_update(
            {"_id": job_id, "status": document["status"], "attempts": document.get("attempts", 0)},
            {
                "$set": {
                    "status": "PENDING",
                    "startedAt": None,
                    "completedAt": None,
                    "metrics": JobMetrics().model_dump(),
                    "issues": [],
                    "cancellationRequestedAt": None,
                    "leaseOwner": None,
                    "leaseUntil": None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ValueError("Job state changed; reload before retrying.")
        await self._audit.insert_one(
            {
                "_id": str(uuid.uuid4()),
                "action": "JOB_RETRY_QUEUED",
                "actor": actor_id,
                "target": job_id,
                "timestamp": now,
                "details": {"attempts": document.get("attempts", 0)},
            }
        )
        return self._view(cast(dict[str, Any], updated))

    async def claim_next(self, worker_id: str, lease_seconds: int = 60) -> Job | None:
        now = datetime.now(UTC)
        document = await self._jobs.find_one_and_update(
            {
                "$or": [
                    {"status": "PENDING"},
                    {"status": "RUNNING", "leaseUntil": {"$lt": now}},
                ],
                "cancellationRequestedAt": None,
                "$expr": {"$lt": ["$attempts", "$maxAttempts"]},
            },
            {
                "$set": {
                    "status": "RUNNING",
                    "startedAt": now,
                    "completedAt": None,
                    "leaseOwner": worker_id,
                    "leaseUntil": now + timedelta(seconds=lease_seconds),
                    "issues": [],
                },
                "$inc": {"attempts": 1},
            },
            sort=[("createdAt", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else self._view(cast(dict[str, Any], document))

    async def renew_lease(self, job_id: str, worker_id: str, lease_seconds: int = 60) -> bool:
        result = await self._jobs.update_one(
            {"_id": job_id, "status": "RUNNING", "leaseOwner": worker_id},
            {"$set": {"leaseUntil": datetime.now(UTC) + timedelta(seconds=lease_seconds)}},
        )
        return result.matched_count == 1

    async def cancellation_requested(self, job_id: str, worker_id: str) -> bool:
        document = await self._jobs.find_one(
            {"_id": job_id, "leaseOwner": worker_id},
            {"cancellationRequestedAt": 1},
        )
        return bool(document and document.get("cancellationRequestedAt") is not None)

    async def _set_progress(
        self,
        job_id: str,
        worker_id: str,
        *,
        total: int,
        processed: int,
        failed: int = 0,
    ) -> None:
        percentage = 100 if total == 0 else min(99, int((processed + failed) * 100 / total))
        matched = await self._jobs.update_one(
            {"_id": job_id, "status": "RUNNING", "leaseOwner": worker_id},
            {
                "$set": {
                    "metrics": JobMetrics(
                        totalRecords=total,
                        processedRecords=processed,
                        failedRecords=failed,
                        progressPercentage=percentage,
                    ).model_dump(),
                    "leaseUntil": datetime.now(UTC) + timedelta(seconds=60),
                }
            },
        )
        if matched.matched_count != 1:
            raise RuntimeError("Job lease was lost.")

    async def _finish(
        self,
        job_id: str,
        worker_id: str,
        *,
        final_status: Literal["COMPLETED", "FAILED", "CANCELLED"],
        metrics: JobMetrics,
        issues: list[dict[str, Any]],
    ) -> None:
        result = await self._jobs.update_one(
            {"_id": job_id, "status": "RUNNING", "leaseOwner": worker_id},
            {
                "$set": {
                    "status": final_status,
                    "completedAt": datetime.now(UTC),
                    "metrics": metrics.model_dump(),
                    "issues": issues,
                    "leaseOwner": None,
                    "leaseUntil": None,
                }
            },
        )
        if result.matched_count != 1:
            raise RuntimeError("Job lease was lost before completion.")

    @staticmethod
    def _parse_import(payload: CreateImportPayload) -> list[dict[str, Any]]:
        if len(payload.content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise ValueError("Import exceeds the 10MB limit.")
        if payload.format == "JSON":
            parsed = json.loads(payload.content)
            if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
                raise ValueError("JSON import must be an array of objects.")
            records = cast(list[dict[str, Any]], parsed)
        elif payload.format == "JSONL":
            records = []
            for line in payload.content.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("Each JSONL line must contain an object.")
                records.append(cast(dict[str, Any], item))
        else:
            reader = csv.DictReader(io.StringIO(payload.content))
            if reader.fieldnames is None:
                raise ValueError("CSV import requires a header row.")
            records = [dict(row) for row in reader]
        if not records or len(records) > _MAX_RECORDS:
            raise ValueError("Import must contain 1 to 10,000 records.")
        return [
            {payload.fieldMapping.get(key, key): value for key, value in record.items()}
            for record in records
        ]

    async def _synchronize_workspace_count(self, workspace_id: object) -> None:
        record_count = await self._records.count_documents(
            {"workspaceId": workspace_id, **self._active_filter()}
        )
        await self._workspaces.update_one(
            {"_id": workspace_id},
            {
                "$set": {"recordCount": record_count, "updatedAt": datetime.now(UTC)},
                "$inc": {"version": 1},
            },
        )

    async def _execute_import(
        self,
        job: Job,
        worker_id: str,
        payload: CreateImportPayload,
    ) -> None:
        workspace = await self._workspaces.find_one(
            {
                "$and": [
                    {"$or": [{"_id": payload.target}, {"name": payload.target}]},
                    self._active_filter(),
                ]
            }
        )
        if workspace is None:
            raise ValueError("Target workspace does not exist.")
        records = self._parse_import(payload)
        keys = [
            str(record.get("id") or record.get("_id") or f"row-{index + 1}")
            for index, record in enumerate(records)
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("Import contains duplicate record identifiers.")
        existing_documents = self._records.find(
            {"workspaceId": workspace["_id"], "recordKey": {"$in": keys}},
            {"recordKey": 1},
        )
        existing_keys = {str(document["recordKey"]) async for document in existing_documents}
        if existing_keys and payload.duplicatePolicy == "FAIL":
            sample = sorted(existing_keys)[:5]
            raise ValueError(f"Duplicate records already exist: {', '.join(sample)}")

        issues: list[dict[str, Any]] = []
        processed = 0
        cancelled = False
        try:
            for index, (record_key, data) in enumerate(zip(keys, records, strict=True), start=1):
                if index == 1 or index % _PROGRESS_BATCH_SIZE == 0:
                    if await self.cancellation_requested(job.id, worker_id):
                        cancelled = True
                        break
                    await self._set_progress(
                        job.id, worker_id, total=len(records), processed=processed
                    )
                if record_key in existing_keys and payload.duplicatePolicy == "SKIP":
                    issues.append(
                        {
                            "severity": "WARNING",
                            "message": "Duplicate skipped.",
                            "recordIdentifier": record_key,
                        }
                    )
                    continue
                now = datetime.now(UTC)
                if record_key in existing_keys:
                    await self._records.update_one(
                        {"workspaceId": workspace["_id"], "recordKey": record_key},
                        {
                            "$set": {
                                "data": data,
                                "validationStatus": "VALID",
                                "issues": [],
                                "deletedAt": None,
                                "updatedAt": now,
                            },
                            "$inc": {"version": 1},
                        },
                    )
                else:
                    await self._records.insert_one(
                        {
                            "_id": str(uuid.uuid4()),
                            "workspaceId": workspace["_id"],
                            "recordKey": record_key,
                            "data": data,
                            "validationStatus": "VALID",
                            "issues": [],
                            "version": 0,
                            "createdAt": now,
                            "updatedAt": now,
                            "deletedAt": None,
                        }
                    )
                    existing_keys.add(record_key)
                processed += 1
        finally:
            await self._synchronize_workspace_count(workspace["_id"])

        if cancelled:
            await self._finish(
                job.id,
                worker_id,
                final_status="CANCELLED",
                metrics=JobMetrics(
                    totalRecords=len(records),
                    processedRecords=processed,
                    progressPercentage=int(processed * 100 / len(records)),
                ),
                issues=issues,
            )
            return
        await self._finish(
            job.id,
            worker_id,
            final_status="COMPLETED",
            metrics=JobMetrics(
                totalRecords=len(records),
                processedRecords=processed,
                progressPercentage=100,
            ),
            issues=issues,
        )

    async def _execute_export(
        self,
        job: Job,
        worker_id: str,
        payload: CreateExportPayload,
    ) -> None:
        workspace = await self._workspaces.find_one(
            {
                "$and": [
                    {"$or": [{"_id": payload.source}, {"name": payload.source}]},
                    self._active_filter(),
                ]
            }
        )
        if workspace is None:
            raise ValueError("Source workspace does not exist.")
        cursor = (
            self._records.find({"workspaceId": workspace["_id"], **self._active_filter()})
            .sort("createdAt", ASCENDING)
            .limit(_MAX_RECORDS)
        )
        rows = [cast(dict[str, Any], document).get("data", {}) async for document in cursor]
        rows = [cast(dict[str, Any], row) for row in rows if isinstance(row, dict)]
        if payload.fields:
            rows = [{field: row.get(field) for field in payload.fields} for row in rows]
        if await self.cancellation_requested(job.id, worker_id):
            await self._finish(
                job.id,
                worker_id,
                final_status="CANCELLED",
                metrics=JobMetrics(totalRecords=len(rows), progressPercentage=0),
                issues=[],
            )
            return
        if payload.format == "JSON":
            content = json.dumps(rows, default=str, indent=2)
            media_type = "application/json"
            extension = "json"
        elif payload.format == "JSONL":
            content = "\n".join(json.dumps(row, default=str, separators=(",", ":")) for row in rows)
            media_type = "application/x-ndjson"
            extension = "jsonl"
        else:
            output = io.StringIO()
            fieldnames = sorted({key for row in rows for key in row})
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                writer.writerows(rows)
            content = output.getvalue()
            media_type = "text/csv"
            extension = "csv"
        if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise ValueError("Export exceeds the 10MB artifact limit.")
        await self._artifacts.replace_one(
            {"_id": job.id},
            {
                "_id": job.id,
                "filename": f"{workspace['name']}-{job.id}.{extension}",
                "mediaType": media_type,
                "content": content,
                "createdAt": datetime.now(UTC),
            },
            upsert=True,
        )
        await self._finish(
            job.id,
            worker_id,
            final_status="COMPLETED",
            metrics=JobMetrics(
                totalRecords=len(rows),
                processedRecords=len(rows),
                progressPercentage=100,
            ),
            issues=[],
        )

    async def process_claimed(self, job: Job, worker_id: str) -> None:
        command_document = await self._commands.find_one({"_id": job.id})
        if command_document is None:
            await self._finish(
                job.id,
                worker_id,
                final_status="FAILED",
                metrics=JobMetrics(failedRecords=1, progressPercentage=100),
                issues=[{"severity": "ERROR", "message": "Durable job command is missing."}],
            )
            return
        try:
            payload = cast(dict[str, Any], command_document["payload"])
            if job.type == "IMPORT":
                await self._execute_import(
                    job, worker_id, CreateImportPayload.model_validate(payload)
                )
            elif job.type == "EXPORT":
                await self._execute_export(
                    job, worker_id, CreateExportPayload.model_validate(payload)
                )
            else:
                raise ValueError(f"Unsupported job type {job.type}.")
        except (ValueError, json.JSONDecodeError, csv.Error) as error:
            await self._finish(
                job.id,
                worker_id,
                final_status="FAILED",
                metrics=JobMetrics(failedRecords=1, progressPercentage=100),
                issues=[{"severity": "ERROR", "message": str(error)}],
            )
        except Exception:
            await self._finish(
                job.id,
                worker_id,
                final_status="FAILED",
                metrics=JobMetrics(failedRecords=1, progressPercentage=100),
                issues=[
                    {
                        "severity": "ERROR",
                        "message": "Job execution failed due to an internal dependency error.",
                    }
                ],
            )
            raise

    async def artifact(self, job_id: str) -> dict[str, Any] | None:
        job = await self._jobs.find_one({"_id": job_id}, {"status": 1})
        if job is None or job.get("status") != "COMPLETED":
            return None
        document = await self._artifacts.find_one({"_id": job_id})
        return None if document is None else cast(dict[str, Any], document)


def resolve_job_service(request: Request) -> JobService:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable")
    return JobService(resources.mongo, resources.settings.mongo_database)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("/jobs", response_model=APIResponse[list[Job]])
async def list_jobs(
    request: Request,
    job_type: str | None = Query(default=None, alias="type"),
    job_status: str | None = Query(default=None, alias="status"),
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[list[Job]]:
    return APIResponse(
        data=await resolve_job_service(request).list_jobs(job_type, job_status), meta=_meta(request)
    )


@router.get("/jobs/{job_id}", response_model=APIResponse[Job])
async def get_job(
    request: Request, job_id: str, _user_id: str = Depends(require_read_roles)
) -> APIResponse[Job]:
    data = await resolve_job_service(request).get_job(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return APIResponse(data=data, meta=_meta(request))


@router.post("/jobs/{job_id}/cancel", response_model=APIResponse[Job])
async def cancel_job(
    request: Request,
    job_id: str,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[Job]:
    try:
        data = await resolve_job_service(request).cancel(job_id, actor_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Job not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.post("/jobs/{job_id}/retry", response_model=APIResponse[Job])
async def retry_job(
    request: Request,
    job_id: str,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[Job]:
    try:
        data = await resolve_job_service(request).retry(job_id, actor_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Job not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/imports", response_model=APIResponse[list[Job]])
async def list_imports(
    request: Request, _user_id: str = Depends(require_read_roles)
) -> APIResponse[list[Job]]:
    return APIResponse(
        data=await resolve_job_service(request).list_jobs("IMPORT", None), meta=_meta(request)
    )


@router.get("/imports/{job_id}", response_model=APIResponse[Job])
async def get_import(
    request: Request, job_id: str, user_id: str = Depends(require_read_roles)
) -> APIResponse[Job]:
    return await get_job(request, job_id, user_id)


@router.post("/imports", status_code=status.HTTP_202_ACCEPTED, response_model=APIResponse[Job])
async def create_import(
    request: Request,
    payload: CreateImportPayload,
    user_id: str = Depends(require_write_roles),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> APIResponse[Job]:
    try:
        data = await resolve_job_service(request).enqueue_import(payload, user_id, idempotency_key)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/exports", response_model=APIResponse[list[Job]])
async def list_exports(
    request: Request, _user_id: str = Depends(require_read_roles)
) -> APIResponse[list[Job]]:
    return APIResponse(
        data=await resolve_job_service(request).list_jobs("EXPORT", None), meta=_meta(request)
    )


@router.get("/exports/{job_id}", response_model=APIResponse[Job])
async def get_export(
    request: Request, job_id: str, user_id: str = Depends(require_read_roles)
) -> APIResponse[Job]:
    return await get_job(request, job_id, user_id)


@router.post("/exports", status_code=status.HTTP_202_ACCEPTED, response_model=APIResponse[Job])
async def create_export(
    request: Request,
    payload: CreateExportPayload,
    user_id: str = Depends(require_write_roles),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> APIResponse[Job]:
    data = await resolve_job_service(request).enqueue_export(payload, user_id, idempotency_key)
    return APIResponse(data=data, meta=_meta(request))


@router.get("/exports/{job_id}/download")
async def download_export(
    request: Request, job_id: str, _user_id: str = Depends(require_read_roles)
) -> StreamingResponse:
    artifact = await resolve_job_service(request).artifact(job_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Completed export artifact not found")
    return StreamingResponse(
        iter([str(artifact["content"]).encode("utf-8")]),
        media_type=str(artifact["mediaType"]),
        headers={"Content-Disposition": f'attachment; filename="{artifact["filename"]}"'},
    )
