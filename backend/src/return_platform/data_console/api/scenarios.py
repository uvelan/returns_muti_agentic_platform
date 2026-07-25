"""Deterministic scenario generation, validation, comparison, and approval APIs."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Final, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo import DESCENDING, AsyncMongoClient, ReturnDocument

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/data-console/v1/scenarios", tags=["Scenarios"])
_SOURCE: Final = "SCENARIOS"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str
    baseWorkspaceId: str
    status: str
    parameters: dict[str, Any]
    createdAt: datetime
    owner: str
    version: int = Field(ge=0)
    generatedDigest: str | None = None
    validatedDigest: str | None = None
    validationIssues: list[str] = Field(default_factory=list)


class ScenarioDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recordId: str
    status: str
    baseData: dict[str, Any] | None = None
    scenarioData: dict[str, Any] | None = None
    issues: list[str] = Field(default_factory=list)


class CreateScenarioPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2_000)
    baseWorkspaceId: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ScenarioService:
    def __init__(self, client: AsyncMongoClient[dict[str, object]], database: str) -> None:
        self._db = client[database]
        self._scenarios = self._db["scenarios"]
        self._scenario_records = self._db["scenario_records"]
        self._workspaces = self._db["workspaces"]
        self._records = self._db["sandbox_records"]
        self._audit_collection = self._db["audit"]

    @staticmethod
    def _view(document: dict[str, Any]) -> Scenario:
        return Scenario.model_validate(
            {
                "id": str(document["_id"]),
                "name": document["name"],
                "description": document.get("description", ""),
                "baseWorkspaceId": document["baseWorkspaceId"],
                "status": document.get("status", "DRAFT"),
                "parameters": document.get("parameters", {}),
                "createdAt": document["createdAt"],
                "owner": document.get("owner", "unknown"),
                "version": max(0, int(document.get("version", 0))),
                "generatedDigest": document.get("generatedDigest"),
                "validatedDigest": document.get("validatedDigest"),
                "validationIssues": document.get("validationIssues", []),
            }
        )

    @staticmethod
    def _active_filter() -> dict[str, Any]:
        return {"$or": [{"deletedAt": None}, {"deletedAt": {"$exists": False}}]}

    async def _write_audit(
        self, action: str, actor: str, target: str, details: dict[str, Any]
    ) -> None:
        await self._audit_collection.insert_one(
            {
                "_id": str(uuid.uuid4()),
                "action": action,
                "actor": actor,
                "target": target,
                "timestamp": datetime.now(UTC),
                "details": details,
            }
        )

    async def list_scenarios(self) -> list[Scenario]:
        cursor = (
            self._scenarios.find({"status": {"$ne": "ARCHIVED"}})
            .sort("createdAt", DESCENDING)
            .limit(500)
        )
        return [self._view(cast(dict[str, Any], document)) async for document in cursor]

    async def get_scenario(self, scenario_id: str) -> Scenario | None:
        document = await self._scenarios.find_one({"_id": scenario_id})
        return None if document is None else self._view(cast(dict[str, Any], document))

    async def create_scenario(self, payload: CreateScenarioPayload, owner: str) -> Scenario:
        workspace = await self._workspaces.find_one(
            {"_id": payload.baseWorkspaceId, **self._active_filter()}
        )
        if workspace is None:
            raise ValueError("Base workspace does not exist.")
        now = datetime.now(UTC)
        document: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "name": payload.name.strip(),
            "description": payload.description.strip(),
            "baseWorkspaceId": payload.baseWorkspaceId,
            "status": "DRAFT",
            "parameters": payload.parameters,
            "createdAt": now,
            "owner": owner,
            "version": 0,
            "generatedDigest": None,
            "validatedDigest": None,
            "validationIssues": [],
        }
        await self._scenarios.insert_one(document)
        await self._write_audit(
            "CREATE_SCENARIO", owner, document["_id"], {"baseWorkspaceId": payload.baseWorkspaceId}
        )
        return self._view(document)

    @staticmethod
    def _apply_parameters(data: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(data)
        set_values = parameters.get("set")
        if isinstance(set_values, dict):
            for key, value in set_values.items():
                if isinstance(key, str) and key and not key.startswith("$") and "." not in key:
                    result[key] = value
        remove_fields = parameters.get("removeFields")
        if isinstance(remove_fields, list):
            for field in remove_fields:
                if isinstance(field, str):
                    result.pop(field, None)
        numeric_multiplier = parameters.get("numericMultiplier")
        if isinstance(numeric_multiplier, (int, float)) and not isinstance(
            numeric_multiplier, bool
        ):
            for key, value in tuple(result.items()):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    result[key] = value * numeric_multiplier
        return result

    async def generate(self, scenario_id: str, actor: str) -> Scenario:
        scenario = await self.get_scenario(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        if scenario.status == "APPROVED":
            raise ValueError("Approved scenarios are immutable.")
        await self._scenario_records.delete_many({"scenarioId": scenario_id})
        cursor = (
            self._records.find({"workspaceId": scenario.baseWorkspaceId, **self._active_filter()})
            .sort("createdAt", 1)
            .limit(10_000)
        )
        generated: list[dict[str, Any]] = []
        async for document in cursor:
            record = cast(dict[str, Any], document)
            generated_data = self._apply_parameters(
                cast(dict[str, Any], record.get("data", {})), scenario.parameters
            )
            generated.append(
                {
                    "_id": f"{scenario_id}:{record['_id']}",
                    "scenarioId": scenario_id,
                    "recordId": record["_id"],
                    "baseData": record.get("data", {}),
                    "scenarioData": generated_data,
                    "issues": [],
                    "createdAt": datetime.now(UTC),
                }
            )
        if generated:
            await self._scenario_records.insert_many(generated, ordered=True)
        canonical = json.dumps(
            [
                {"recordId": item["recordId"], "scenarioData": item["scenarioData"]}
                for item in generated
            ],
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        updated_document = await self._scenarios.find_one_and_update(
            {"_id": scenario_id, "version": scenario.version},
            {
                "$set": {
                    "status": "READY",
                    "generatedDigest": digest,
                    "validatedDigest": None,
                    "validationIssues": [],
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated_document is None:
            raise ValueError("Scenario version conflict.")
        await self._write_audit(
            "GENERATE_SCENARIO",
            actor,
            scenario_id,
            {"recordCount": len(generated), "digest": digest},
        )
        return self._view(cast(dict[str, Any], updated_document))

    async def validate(self, scenario_id: str, actor: str) -> Scenario:
        scenario = await self.get_scenario(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        if not scenario.generatedDigest:
            raise ValueError("Generate the scenario before validation.")
        issues: list[str] = []
        count = 0
        cursor = self._scenario_records.find({"scenarioId": scenario_id})
        async for document in cursor:
            count += 1
            data = document.get("scenarioData")
            if not isinstance(data, dict) or not data:
                issues.append(f"Record {document.get('recordId')} has empty or invalid data.")
            if len(issues) >= 100:
                break
        if count == 0:
            issues.append("Scenario contains no records.")
        status = "FAILED" if issues else "READY"
        updated_document = await self._scenarios.find_one_and_update(
            {"_id": scenario_id, "version": scenario.version},
            {
                "$set": {
                    "status": status,
                    "validationIssues": issues,
                    "validatedDigest": scenario.generatedDigest if not issues else None,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated_document is None:
            raise ValueError("Scenario version conflict.")
        await self._write_audit(
            "VALIDATE_SCENARIO", actor, scenario_id, {"issueCount": len(issues)}
        )
        return self._view(cast(dict[str, Any], updated_document))

    async def approve(self, scenario_id: str, actor: str) -> Scenario:
        scenario = await self.get_scenario(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        if (
            scenario.status != "READY"
            or scenario.validationIssues
            or not scenario.generatedDigest
            or scenario.validatedDigest != scenario.generatedDigest
        ):
            raise ValueError(
                "Only generated scenarios validated against the current digest can be approved."
            )
        document = cast(
            dict[str, Any] | None,
            await self._scenarios.find_one_and_update(
                {"_id": scenario_id, "version": scenario.version, "status": "READY"},
                {
                    "$set": {
                        "status": "APPROVED",
                        "approvedAt": datetime.now(UTC),
                        "approvedBy": actor,
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            ),
        )
        if document is None:
            raise ValueError("Scenario version conflict.")
        await self._write_audit(
            "APPROVE_SCENARIO", actor, scenario_id, {"digest": scenario.generatedDigest}
        )
        return self._view(document)

    async def archive(self, scenario_id: str, actor: str) -> bool:
        result = await self._scenarios.update_one(
            {"_id": scenario_id, "status": {"$ne": "ARCHIVED"}},
            {
                "$set": {"status": "ARCHIVED", "archivedAt": datetime.now(UTC)},
                "$inc": {"version": 1},
            },
        )
        if result.modified_count:
            await self._write_audit("ARCHIVE_SCENARIO", actor, scenario_id, {})
        return result.modified_count > 0

    async def diffs(self, scenario_id: str) -> list[ScenarioDiff]:
        if await self.get_scenario(scenario_id) is None:
            raise KeyError(scenario_id)
        cursor = (
            self._scenario_records.find({"scenarioId": scenario_id})
            .sort("recordId", 1)
            .limit(10_000)
        )
        result: list[ScenarioDiff] = []
        async for document in cursor:
            base = cast(dict[str, Any], document.get("baseData", {}))
            generated = cast(dict[str, Any], document.get("scenarioData", {}))
            if base == generated:
                continue
            result.append(
                ScenarioDiff(
                    recordId=str(document["recordId"]),
                    status="MODIFIED",
                    baseData=base,
                    scenarioData=generated,
                    issues=cast(list[str], document.get("issues", [])),
                )
            )
        return result

    async def preview(self, scenario_id: str) -> list[dict[str, Any]]:
        if await self.get_scenario(scenario_id) is None:
            raise KeyError(scenario_id)
        cursor = (
            self._scenario_records.find({"scenarioId": scenario_id}).sort("recordId", 1).limit(100)
        )
        return [
            {
                "recordId": str(document["recordId"]),
                "data": document.get("scenarioData", {}),
                "issues": document.get("issues", []),
            }
            async for document in cursor
        ]


def resolve_scenario_service(request: Request) -> ScenarioService:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable")
    return ScenarioService(resources.mongo, resources.settings.mongo_database)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("", response_model=APIResponse[list[Scenario]])
async def list_scenarios(
    request: Request, _user_id: str = Depends(require_read_roles)
) -> APIResponse[list[Scenario]]:
    return APIResponse(
        data=await resolve_scenario_service(request).list_scenarios(), meta=_meta(request)
    )


@router.post("", response_model=APIResponse[Scenario])
async def create_scenario(
    request: Request, payload: CreateScenarioPayload, user_id: str = Depends(require_write_roles)
) -> APIResponse[Scenario]:
    try:
        data = await resolve_scenario_service(request).create_scenario(payload, user_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/{scenario_id}", response_model=APIResponse[Scenario])
async def get_scenario(
    request: Request, scenario_id: str, _user_id: str = Depends(require_read_roles)
) -> APIResponse[Scenario]:
    data = await resolve_scenario_service(request).get_scenario(scenario_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return APIResponse(data=data, meta=_meta(request))


@router.delete("/{scenario_id}", response_model=APIResponse[dict[str, bool]])
async def delete_scenario(
    request: Request, scenario_id: str, user_id: str = Depends(require_write_roles)
) -> APIResponse[dict[str, bool]]:
    if not await resolve_scenario_service(request).archive(scenario_id, user_id):
        raise HTTPException(status_code=404, detail="Scenario not found")
    return APIResponse(data={"archived": True}, meta=_meta(request))


@router.post("/{scenario_id}/generate", response_model=APIResponse[Scenario])
async def generate_scenario(
    request: Request, scenario_id: str, user_id: str = Depends(require_write_roles)
) -> APIResponse[Scenario]:
    try:
        data = await resolve_scenario_service(request).generate(scenario_id, user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Scenario not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.post("/{scenario_id}/validate", response_model=APIResponse[Scenario])
async def validate_scenario(
    request: Request, scenario_id: str, user_id: str = Depends(require_write_roles)
) -> APIResponse[Scenario]:
    try:
        data = await resolve_scenario_service(request).validate(scenario_id, user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Scenario not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.post("/{scenario_id}/approve", response_model=APIResponse[Scenario])
async def approve_scenario(
    request: Request, scenario_id: str, user_id: str = Depends(require_write_roles)
) -> APIResponse[Scenario]:
    try:
        data = await resolve_scenario_service(request).approve(scenario_id, user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Scenario not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/{scenario_id}/diffs", response_model=APIResponse[list[ScenarioDiff]])
async def get_scenario_diffs(
    request: Request, scenario_id: str, _user_id: str = Depends(require_read_roles)
) -> APIResponse[list[ScenarioDiff]]:
    try:
        data = await resolve_scenario_service(request).diffs(scenario_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Scenario not found") from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/{scenario_id}/preview", response_model=APIResponse[list[dict[str, Any]]])
async def preview_scenario(
    request: Request, scenario_id: str, _user_id: str = Depends(require_read_roles)
) -> APIResponse[list[dict[str, Any]]]:
    try:
        data = await resolve_scenario_service(request).preview(scenario_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Scenario not found") from error
    return APIResponse(data=data, meta=_meta(request))
