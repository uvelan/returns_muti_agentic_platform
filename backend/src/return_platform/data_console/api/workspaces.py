"""Versioned sandbox workspace APIs with atomic record-count maintenance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/data-console/v1/workspaces", tags=["Workspaces"])
_SOURCE: Final = "WORKSPACES"


class Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str
    isSandbox: bool
    owner: str
    createdAt: datetime
    updatedAt: datetime
    schemaId: str | None = None
    recordCount: int = Field(ge=0)
    version: int = Field(ge=0)


class SandboxIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
    field: str | None = None


class SandboxRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    data: dict[str, Any]
    createdAt: datetime
    updatedAt: datetime
    validationStatus: str
    issues: list[SandboxIssue]
    version: int = Field(ge=0)


class CreateWorkspacePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2_000)
    schemaId: str | None = Field(default=None, max_length=128)


class RecordPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any]
    idempotencyKey: str | None = Field(default=None, min_length=8, max_length=128)


class WorkspaceService:
    def __init__(self, client: AsyncMongoClient[dict[str, object]], database: str) -> None:
        self._client = client
        self._db = client[database]
        self._workspaces = self._db["workspaces"]
        self._records = self._db["sandbox_records"]
        self._audit = self._db["audit"]

    async def ensure_indexes(self) -> None:
        await self._workspaces.create_index("name", unique=True)
        await self._records.create_index([("workspaceId", ASCENDING), ("createdAt", DESCENDING)])
        await self._records.create_index(
            [("workspaceId", ASCENDING), ("idempotencyKey", ASCENDING)],
            unique=True,
            sparse=True,
        )

    @staticmethod
    def _workspace(document: dict[str, Any]) -> Workspace:
        return Workspace.model_validate(
            {
                "id": str(document["_id"]),
                "name": document["name"],
                "description": document.get("description", ""),
                "isSandbox": bool(document.get("isSandbox", True)),
                "owner": document.get("owner", "unknown"),
                "createdAt": document["createdAt"],
                "updatedAt": document["updatedAt"],
                "schemaId": document.get("schemaId"),
                "recordCount": max(0, int(document.get("recordCount", 0))),
                "version": max(0, int(document.get("version", 0))),
            }
        )

    @staticmethod
    def _record(document: dict[str, Any]) -> SandboxRecord:
        return SandboxRecord.model_validate(
            {
                "id": str(document["_id"]),
                "data": document.get("data", {}),
                "createdAt": document["createdAt"],
                "updatedAt": document["updatedAt"],
                "validationStatus": document.get("validationStatus", "INVALID"),
                "issues": document.get("issues", []),
                "version": max(0, int(document.get("version", 0))),
            }
        )

    @staticmethod
    def _active_filter() -> dict[str, Any]:
        return {"$or": [{"deletedAt": None}, {"deletedAt": {"$exists": False}}]}

    async def audit(self, action: str, actor: str, target: str, details: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        await self._audit.insert_one(
            {
                "_id": str(uuid.uuid4()),
                "action": action,
                "actor": actor,
                "target": target,
                "timestamp": now,
                "details": details,
            }
        )

    async def list_workspaces(self) -> list[Workspace]:
        cursor = (
            self._workspaces.find(self._active_filter()).sort("createdAt", DESCENDING).limit(500)
        )
        return [self._workspace(cast(dict[str, Any], document)) async for document in cursor]

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        document = await self._workspaces.find_one({"_id": workspace_id, **self._active_filter()})
        return None if document is None else self._workspace(cast(dict[str, Any], document))

    async def create_workspace(self, payload: CreateWorkspacePayload, owner: str) -> Workspace:
        await self.ensure_indexes()
        now = datetime.now(UTC)
        document: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "name": payload.name.strip(),
            "description": payload.description.strip(),
            "isSandbox": True,
            "owner": owner,
            "createdAt": now,
            "updatedAt": now,
            "schemaId": payload.schemaId,
            "recordCount": 0,
            "version": 0,
            "deletedAt": None,
        }
        try:
            await self._workspaces.insert_one(document)
        except DuplicateKeyError as error:
            raise ValueError("Workspace name already exists.") from error
        await self.audit("CREATE_WORKSPACE", owner, document["_id"], {"name": document["name"]})
        return self._workspace(document)

    async def delete_workspace(self, workspace_id: str, expected_version: int, actor: str) -> bool:
        now = datetime.now(UTC)
        document = await self._workspaces.find_one_and_update(
            {"_id": workspace_id, "version": expected_version, **self._active_filter()},
            {"$set": {"deletedAt": now, "updatedAt": now}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return False
        await self.audit("DELETE_WORKSPACE", actor, workspace_id, {"version": expected_version})
        return True

    async def list_records(self, workspace_id: str) -> list[SandboxRecord]:
        if await self.get_workspace(workspace_id) is None:
            raise KeyError(workspace_id)
        cursor = (
            self._records.find({"workspaceId": workspace_id, **self._active_filter()})
            .sort("createdAt", DESCENDING)
            .limit(10_000)
        )
        return [self._record(cast(dict[str, Any], document)) async for document in cursor]

    async def get_record(self, workspace_id: str, record_id: str) -> SandboxRecord | None:
        document = await self._records.find_one(
            {"_id": record_id, "workspaceId": workspace_id, **self._active_filter()}
        )
        return None if document is None else self._record(cast(dict[str, Any], document))

    @staticmethod
    def _validate_record(data: dict[str, Any]) -> tuple[str, list[dict[str, str | None]]]:
        issues: list[dict[str, str | None]] = []
        if not data:
            issues.append({"message": "Record data must not be empty.", "field": None})
        if any(str(key).startswith("$") or "." in str(key) for key in data):
            issues.append(
                {"message": "MongoDB operator and dotted keys are not allowed.", "field": None}
            )
        return ("INVALID" if issues else "VALID", issues)

    async def create_record(
        self, workspace_id: str, payload: RecordPayload, actor: str
    ) -> SandboxRecord:
        await self.ensure_indexes()
        validation_status, issues = self._validate_record(payload.data)
        now = datetime.now(UTC)
        document: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "workspaceId": workspace_id,
            "recordKey": str(uuid.uuid4()),
            "data": payload.data,
            "validationStatus": validation_status,
            "issues": issues,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
            "deletedAt": None,
        }
        if payload.idempotencyKey is not None:
            document["idempotencyKey"] = payload.idempotencyKey

        async def transaction(mongo_session: Any) -> dict[str, Any]:
            workspace = await self._workspaces.find_one(
                {"_id": workspace_id, **self._active_filter()},
                session=mongo_session,
            )
            if workspace is None:
                raise KeyError(workspace_id)
            if payload.idempotencyKey is not None:
                existing = await self._records.find_one(
                    {
                        "workspaceId": workspace_id,
                        "idempotencyKey": payload.idempotencyKey,
                        **self._active_filter(),
                    },
                    session=mongo_session,
                )
                if existing is not None:
                    return cast(dict[str, Any], existing)
            await self._records.insert_one(document, session=mongo_session)
            updated = await self._workspaces.update_one(
                {"_id": workspace_id, **self._active_filter()},
                {"$inc": {"recordCount": 1, "version": 1}, "$set": {"updatedAt": now}},
                session=mongo_session,
            )
            if updated.modified_count != 1:
                raise RuntimeError("Workspace record count update failed")
            await self._audit.insert_one(
                {
                    "_id": str(uuid.uuid4()),
                    "action": "CREATE_WORKSPACE_RECORD",
                    "actor": actor,
                    "target": document["_id"],
                    "timestamp": now,
                    "details": {"workspaceId": workspace_id},
                },
                session=mongo_session,
            )
            return document

        try:
            async with self._client.start_session() as mongo_session:
                created = await mongo_session.with_transaction(transaction)
        except DuplicateKeyError:
            if payload.idempotencyKey is None:
                raise
            existing = await self._records.find_one(
                {
                    "workspaceId": workspace_id,
                    "idempotencyKey": payload.idempotencyKey,
                    **self._active_filter(),
                }
            )
            if existing is None:
                raise
            created = cast(dict[str, Any], existing)
        return self._record(created)

    async def update_record(
        self,
        workspace_id: str,
        record_id: str,
        payload: RecordPayload,
        expected_version: int,
        actor: str,
    ) -> SandboxRecord | None:
        validation_status, issues = self._validate_record(payload.data)
        document = await self._records.find_one_and_update(
            {
                "_id": record_id,
                "workspaceId": workspace_id,
                "version": expected_version,
                **self._active_filter(),
            },
            {
                "$set": {
                    "data": payload.data,
                    "validationStatus": validation_status,
                    "issues": issues,
                    "updatedAt": datetime.now(UTC),
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is not None:
            await self.audit(
                "UPDATE_WORKSPACE_RECORD", actor, record_id, {"workspaceId": workspace_id}
            )
        return None if document is None else self._record(cast(dict[str, Any], document))

    async def delete_record(
        self,
        workspace_id: str,
        record_id: str,
        expected_version: int,
        actor: str,
    ) -> bool:
        now = datetime.now(UTC)

        async def transaction(mongo_session: Any) -> bool:
            document = await self._records.find_one_and_update(
                {
                    "_id": record_id,
                    "workspaceId": workspace_id,
                    "version": expected_version,
                    **self._active_filter(),
                },
                {"$set": {"deletedAt": now, "updatedAt": now}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
                session=mongo_session,
            )
            if document is None:
                return False
            updated = await self._workspaces.update_one(
                {"_id": workspace_id, "recordCount": {"$gt": 0}, **self._active_filter()},
                {"$inc": {"recordCount": -1, "version": 1}, "$set": {"updatedAt": now}},
                session=mongo_session,
            )
            if updated.modified_count != 1:
                raise RuntimeError("Workspace record count update failed")
            await self._audit.insert_one(
                {
                    "_id": str(uuid.uuid4()),
                    "action": "DELETE_WORKSPACE_RECORD",
                    "actor": actor,
                    "target": record_id,
                    "timestamp": now,
                    "details": {"workspaceId": workspace_id},
                },
                session=mongo_session,
            )
            return True

        async with self._client.start_session() as mongo_session:
            deleted = await mongo_session.with_transaction(transaction)
        return bool(deleted)


def resolve_workspace_service(request: Request) -> WorkspaceService:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable")
    return WorkspaceService(resources.mongo, resources.settings.mongo_database)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("", response_model=APIResponse[list[Workspace]])
async def list_workspaces(
    request: Request, _user_id: str = Depends(require_read_roles)
) -> APIResponse[list[Workspace]]:
    return APIResponse(
        data=await resolve_workspace_service(request).list_workspaces(), meta=_meta(request)
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=APIResponse[Workspace])
async def create_workspace(
    request: Request, payload: CreateWorkspacePayload, user_id: str = Depends(require_write_roles)
) -> APIResponse[Workspace]:
    try:
        data = await resolve_workspace_service(request).create_workspace(payload, user_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/{workspace_id}", response_model=APIResponse[Workspace])
async def get_workspace(
    request: Request, workspace_id: str, _user_id: str = Depends(require_read_roles)
) -> APIResponse[Workspace]:
    data = await resolve_workspace_service(request).get_workspace(workspace_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return APIResponse(data=data, meta=_meta(request))


@router.delete("/{workspace_id}", response_model=APIResponse[dict[str, bool]])
async def delete_workspace(
    request: Request,
    workspace_id: str,
    expected_version: int = Query(default=0, alias="expectedVersion", ge=0),
    user_id: str = Depends(require_write_roles),
) -> APIResponse[dict[str, bool]]:
    deleted = await resolve_workspace_service(request).delete_workspace(
        workspace_id, expected_version, user_id
    )
    if not deleted:
        raise HTTPException(status_code=409, detail="Workspace not found or version conflict")
    return APIResponse(data={"deleted": True}, meta=_meta(request))


@router.get("/{workspace_id}/records", response_model=APIResponse[list[SandboxRecord]])
async def list_records(
    request: Request, workspace_id: str, _user_id: str = Depends(require_read_roles)
) -> APIResponse[list[SandboxRecord]]:
    try:
        data = await resolve_workspace_service(request).list_records(workspace_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Workspace not found") from error
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/{workspace_id}/records",
    status_code=status.HTTP_201_CREATED,
    response_model=APIResponse[SandboxRecord],
)
async def create_record(
    request: Request,
    workspace_id: str,
    payload: RecordPayload,
    user_id: str = Depends(require_write_roles),
) -> APIResponse[SandboxRecord]:
    try:
        data = await resolve_workspace_service(request).create_record(
            workspace_id, payload, user_id
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Workspace not found") from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/{workspace_id}/records/{record_id}", response_model=APIResponse[SandboxRecord])
async def get_record(
    request: Request, workspace_id: str, record_id: str, _user_id: str = Depends(require_read_roles)
) -> APIResponse[SandboxRecord]:
    data = await resolve_workspace_service(request).get_record(workspace_id, record_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return APIResponse(data=data, meta=_meta(request))


@router.patch("/{workspace_id}/records/{record_id}", response_model=APIResponse[SandboxRecord])
async def update_record(
    request: Request,
    workspace_id: str,
    record_id: str,
    payload: RecordPayload,
    expected_version: int = Query(default=0, alias="expectedVersion", ge=0),
    user_id: str = Depends(require_write_roles),
) -> APIResponse[SandboxRecord]:
    data = await resolve_workspace_service(request).update_record(
        workspace_id, record_id, payload, expected_version, user_id
    )
    if data is None:
        raise HTTPException(status_code=409, detail="Record not found or version conflict")
    return APIResponse(data=data, meta=_meta(request))


@router.delete("/{workspace_id}/records/{record_id}", response_model=APIResponse[dict[str, bool]])
async def delete_record(
    request: Request,
    workspace_id: str,
    record_id: str,
    expected_version: int = Query(default=0, alias="expectedVersion", ge=0),
    user_id: str = Depends(require_write_roles),
) -> APIResponse[dict[str, bool]]:
    deleted = await resolve_workspace_service(request).delete_record(
        workspace_id, record_id, expected_version, user_id
    )
    if not deleted:
        raise HTTPException(status_code=409, detail="Record not found or version conflict")
    return APIResponse(data={"deleted": True}, meta=_meta(request))
