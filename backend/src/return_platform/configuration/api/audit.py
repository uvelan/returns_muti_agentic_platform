"""Evidence-backed Audit handlers.

Handler bodies only -- no `APIRouter` here. `list_audit_logs` and
`get_audit_log` are mounted by the canonical `configuration/api/router.py`
under `/api/config`; the `/data-console/v1` `APIRouter` this module used to
also declare them under was retired in CFG-1 (D-CFG-5) because nothing ever
mounted it. Retired along with it: `get_governance` and `get_hardening` (no
canonical-router consumer, so unreachable by any route once the object they
were registered on is gone) and `get_settings`/`ConsoleSettingsView` (named
explicitly for removal in the CFG-1 brief). `AuditService` is trimmed to the
two methods the surviving handlers use; `governance()`, `settings_view()` and
`hardening()` -- and the `operations.alerts`/governance-catalog machinery only
they called -- went with them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from pymongo import DESCENDING, AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_read_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta

__all__ = ["AuditService", "resolve_audit_service"]


class AuditLog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    action: str
    actor: str
    target: str
    timestamp: datetime
    details: dict[str, Any]


class AuditService:
    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        database: str,
    ) -> None:
        self._db = client[database]
        self._audit = self._db["audit"]

    @staticmethod
    def _log(document: dict[str, Any]) -> AuditLog:
        timestamp = document.get("timestamp")
        if not isinstance(timestamp, datetime):
            timestamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return AuditLog(
            id=str(document["_id"]),
            action=str(document.get("action", "UNKNOWN")),
            actor=str(document.get("actor", "unknown")),
            target=str(document.get("target", "unknown")),
            timestamp=timestamp,
            details=cast(dict[str, Any], document.get("details", {})),
        )

    async def list_logs(self) -> list[AuditLog]:
        cursor = self._audit.find({}).sort("timestamp", DESCENDING).limit(1_000)
        return [self._log(cast(dict[str, Any], document)) async for document in cursor]

    async def get_log(self, audit_id: str) -> AuditLog | None:
        document = await self._audit.find_one({"_id": audit_id})
        return None if document is None else self._log(cast(dict[str, Any], document))


def resolve_audit_service(request: Request) -> AuditService:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(settings, Settings)
    ):
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable")
    return AuditService(resources.mongo, settings.mongo_database)


def _response_meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


async def list_audit_logs(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[list[AuditLog]]:
    return APIResponse(
        data=await resolve_audit_service(request).list_logs(), meta=_response_meta(request)
    )


async def get_audit_log(
    request: Request,
    audit_id: str,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[AuditLog]:
    data = await resolve_audit_service(request).get_log(audit_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Audit log not found")
    return APIResponse(data=data, meta=_response_meta(request))
