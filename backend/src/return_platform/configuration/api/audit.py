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

import re
from collections.abc import Sequence
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

    async def list_logs(
        self,
        *,
        actions: Sequence[str] | None = None,
        target: str | None = None,
    ) -> list[AuditLog]:
        """Every record, or the ones matching `actions`/`target`.

        Server-side, not a client-side filter over `find({})`: the `audit`
        collection is platform-wide (AI gateway, governance kernel,
        configuration releases all write through the same `append_audit`),
        so a caller asking for one release's trail must not have to page
        through a thousand unrelated records first -- `limit(1_000)` runs
        AFTER the filter, not before it.

        `actions` entries may end with `*` for a prefix match
        (`CONFIGURATION_*`) or name one action exactly; multiple entries are
        OR'd together, the same way a caller would read a comma-free
        repeated query parameter. `target` is an exact match -- every writer
        of an audit record already knows the exact target it acted on (a
        release id, a source id), so there is no prefix case to support
        without inventing a wildcard convention nothing produces yet.

        Neither parameter changes the default: called with neither, this is
        exactly the unfiltered `find({})` it always was.
        """
        query: dict[str, Any] = {}
        if actions:
            query["action"] = {"$regex": _action_pattern(actions)}
        if target:
            query["target"] = target
        cursor = self._audit.find(query).sort("timestamp", DESCENDING).limit(1_000)
        return [self._log(cast(dict[str, Any], document)) async for document in cursor]

    async def get_log(self, audit_id: str) -> AuditLog | None:
        document = await self._audit.find_one({"_id": audit_id})
        return None if document is None else self._log(cast(dict[str, Any], document))


def _action_pattern(actions: Sequence[str]) -> str:
    """One alternation per `actions` entry: `NAME` matches exactly, `NAME*`
    matches as a prefix. Every action name in this platform is written
    `DOMAIN_VERB` with no other place a `*` is meaningful, so a trailing
    wildcard is the whole glob vocabulary this needs."""
    alternatives = [
        f"^{re.escape(action[:-1])}" if action.endswith("*") else f"^{re.escape(action)}$"
        for action in actions
    ]
    return "|".join(alternatives)


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
    *,
    actions: Sequence[str] | None = None,
    target: str | None = None,
) -> APIResponse[list[AuditLog]]:
    """`actions`/`target` are keyword-only and default-`None` -- the query
    parameters `router.py`'s route declares -- so every existing caller of
    this handler function (there are none left mounting it directly, but
    the shape is the platform's convention for these plain-function-not-route
    handlers) keeps calling it with just `(request, user_id)` unchanged."""
    return APIResponse(
        data=await resolve_audit_service(request).list_logs(actions=actions, target=target),
        meta=_response_meta(request),
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
