"""Read-only operational visibility for external integration commands."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_audit_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/integration-outbox", tags=["Integration Outbox"])


class OutboxView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    topic: str
    aggregateType: str
    aggregateId: str
    idempotencyKey: str
    status: str
    attemptCount: int = Field(ge=0)
    nextAttemptAt: datetime
    lastErrorCode: str | None = None
    externalReference: str | None = None
    createdAt: datetime
    updatedAt: datetime


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("", response_model=APIResponse[list[OutboxView]])
async def list_outbox(
    request: Request,
    outbox_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1_000),
    _actor: str = Depends(require_audit_roles),
) -> APIResponse[list[OutboxView]]:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable.")
    collection = resources.mongo[resources.settings.mongo_database]["integration_outbox"]
    query: dict[str, Any] = {"status": outbox_status} if outbox_status else {}
    cursor = collection.find(query).sort("createdAt", -1).limit(limit)
    results: list[OutboxView] = []
    async for raw in cursor:
        document = cast(dict[str, Any], raw)
        results.append(
            OutboxView.model_validate(
                {
                    "id": str(document["_id"]),
                    "topic": document.get("topic", "UNKNOWN"),
                    "aggregateType": document.get("aggregateType", "UNKNOWN"),
                    "aggregateId": document.get("aggregateId", "UNKNOWN"),
                    "idempotencyKey": document.get("idempotencyKey", "UNKNOWN"),
                    "status": document.get("status", "UNKNOWN"),
                    "attemptCount": document.get("attemptCount", 0),
                    "nextAttemptAt": document.get("nextAttemptAt", document.get("createdAt")),
                    "lastErrorCode": document.get("lastErrorCode"),
                    "externalReference": document.get("externalReference"),
                    "createdAt": document.get("createdAt"),
                    "updatedAt": document.get("updatedAt", document.get("createdAt")),
                }
            )
        )
    return APIResponse(data=results, meta=_meta(request))
