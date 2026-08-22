"""Evidence-backed Audit, Governance, Settings, and Hardening APIs."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo import DESCENDING, AsyncMongoClient
from pymongo.errors import PyMongoError

from return_platform.configuration.process_adoption import HEARTBEAT_PROCESS_CLASSES
from return_platform.configuration.settings import Settings
from return_platform.data_platform.graph.sync_service import (
    GRAPH_SYNC_RUNS_COLLECTION,
    SYNC_STALL_SECONDS,
)
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION,
)
from return_platform.operations.alerts import OperationalReading
from return_platform.operations.alerts import evaluate as evaluate_alerts
from return_platform.operations.integrations.outbox import INTEGRATION_OUTBOX_COLLECTION
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_read_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.shared.governance import AllowedOperation, OwnershipClass

__all__ = ["AuditService", "resolve_audit_service", "router"]

router = APIRouter(prefix="/data-console/v1", tags=["Audit"])
_SOURCE: Final = "AUDIT"


class AuditLog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    action: str
    actor: str
    target: str
    timestamp: datetime
    details: dict[str, Any]


class GovernanceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["VALIDATED", "DEGRADED"]
    evaluatedAt: datetime
    catalogVersion: str
    catalogDigest: str
    catalogPath: str
    assetCount: int = Field(ge=0)
    authoritativeAssetCount: int = Field(ge=0)
    sampledAssetCount: int = Field(ge=0)
    ownershipCounts: dict[str, int]
    violations: list[str]


class ConsoleSettingsView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str
    retentionDays: int = Field(ge=1)
    strictMode: bool
    eventStreamRetention: int = Field(ge=1)
    aiProviderOrder: list[str]
    aiInterceptionEnabled: bool
    seedVersion: str


class HardeningCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: Literal["PASS", "WARN", "FAIL", "NOT_VALIDATED"]
    evidence: str
    details: str


class HardeningSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["PASS", "DEGRADED", "FAIL", "NOT_VALIDATED"]
    evaluatedAt: datetime
    score: int | None = Field(default=None, ge=0, le=100)
    vulnerabilities: int | None = Field(default=None, ge=0)
    checks: list[HardeningCheck]


class AuditService:
    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        database: str,
        resources: RuntimeResources,
        settings: Settings,
    ) -> None:
        self._db = client[database]
        self._audit = self._db["audit"]
        self._heartbeats = self._db["worker_heartbeats"]
        self._resources = resources
        self._settings = settings

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

    def governance(self) -> GovernanceSummary:
        loaded = self._resources.catalog
        assets = loaded.catalog.assets
        ownership_counts = Counter(asset.ownership.value for asset in assets)
        violations: list[str] = []
        for asset in assets:
            operations = set(asset.allowed_operations)
            if asset.ownership is OwnershipClass.SOURCE_SYSTEM and operations != {
                AllowedOperation.READ
            }:
                violations.append(f"{asset.asset_id}: source-system asset is not read-only")
            if asset.ownership is OwnershipClass.DERIVED_PROJECTION and asset.authoritative:
                violations.append(f"{asset.asset_id}: derived projection is marked authoritative")
        return GovernanceSummary(
            status="DEGRADED" if violations else "VALIDATED",
            evaluatedAt=datetime.now(UTC),
            catalogVersion=loaded.catalog.version,
            catalogDigest=loaded.sha256_hex,
            catalogPath=str(loaded.source_path),
            assetCount=len(assets),
            authoritativeAssetCount=sum(1 for asset in assets if asset.authoritative),
            sampledAssetCount=sum(1 for asset in assets if asset.sampling.enabled),
            ownershipCounts=dict(sorted(ownership_counts.items())),
            violations=violations,
        )

    def settings_view(self) -> ConsoleSettingsView:
        return ConsoleSettingsView(
            environment=self._settings.environment,
            retentionDays=self._settings.audit_retention_days,
            strictMode=self._settings.environment in {"staging", "production"},
            eventStreamRetention=self._settings.event_stream_retention,
            aiProviderOrder=[
                item.strip() for item in self._settings.ai_provider_order.split(",") if item.strip()
            ],
            aiInterceptionEnabled=self._settings.ai_interception_default,
            seedVersion=self._settings.seed_version,
        )

    async def _operational_reading(self) -> OperationalReading:
        """Measure what this process can reach, and say nothing about the rest.

        Every reading is optional and a failure to take one leaves it `None`,
        which evaluates to `NOT_VALIDATED`. A `try` that turned a broken query
        into a zero would report the healthiest possible number for the case
        where the thing measuring is itself broken.
        """
        database = (
            self._resources.mongo[self._settings.mongo_database] if self._resources.mongo else None
        )
        if database is None:
            return OperationalReading()

        outbox_depth: int | None = None
        try:
            outbox_depth = await database[INTEGRATION_OUTBOX_COLLECTION].count_documents(
                {"publishedAt": None}
            )
        except PyMongoError:
            outbox_depth = None

        stalled: int | None = None
        try:
            cutoff = datetime.now(UTC) - timedelta(seconds=SYNC_STALL_SECONDS)
            stalled = await database[GRAPH_SYNC_RUNS_COLLECTION].count_documents(
                {"status": "RUNNING", "heartbeatAt": {"$lt": cutoff}}
            )
        except PyMongoError:
            stalled = None

        serving: int | None = None
        try:
            serving = await database[ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION].count_documents({})
        except PyMongoError:
            serving = None

        return OperationalReading(
            outbox_depth=outbox_depth,
            stalled_sync_runs=stalled,
            serving_snapshots=serving,
        )

    async def hardening(self) -> HardeningSummary:
        checks: list[HardeningCheck] = []
        governance = self.governance()
        checks.append(
            HardeningCheck(
                id="governance-catalog",
                status="PASS" if governance.status == "VALIDATED" else "FAIL",
                evidence=f"sha256:{governance.catalogDigest}",
                details=f"Validated {governance.assetCount} governed assets.",
            )
        )
        checks.append(
            HardeningCheck(
                id="authentication-boundary",
                status="WARN" if self._settings.environment in {"development", "test"} else "PASS",
                evidence=f"environment:{self._settings.environment}",
                details=(
                    "Development principal is enabled; production must inject an external "
                    "principal provider."
                    if self._settings.environment in {"development", "test"}
                    else "Application startup requires an external principal provider."
                ),
            )
        )
        dependencies = {
            "mongodb": self._resources.mongo,
            "neo4j": self._resources.neo4j,
            "valkey": self._resources.valkey,
            "temporal": self._resources.temporal,
        }
        missing = sorted(name for name, value in dependencies.items() if value is None)
        checks.append(
            HardeningCheck(
                id="dependency-initialization",
                status="PASS" if not missing else "FAIL",
                evidence="runtime-resources",
                details="All runtime clients initialized."
                if not missing
                else f"Missing clients: {', '.join(missing)}.",
            )
        )
        cutoff = datetime.now(UTC) - timedelta(seconds=self._settings.worker_readiness_ttl_seconds)
        active_workers = {
            str(document.get("_id"))
            async for document in self._heartbeats.find({"lastSeenAt": {"$gte": cutoff}})
        }
        # The shared set. This named three of six, so a dead reclaimer or a
        # dead discovery worker left the hardening report clean.
        required_workers = set(HEARTBEAT_PROCESS_CLASSES)
        missing_workers = sorted(required_workers - active_workers)
        checks.append(
            HardeningCheck(
                id="background-workers",
                status="PASS" if not missing_workers else "WARN",
                evidence=f"worker_heartbeats newer than {cutoff.isoformat()}",
                details="All required workers are reporting."
                if not missing_workers
                else f"Missing heartbeats: {', '.join(missing_workers)}.",
            )
        )
        # The six operational conditions, on the surface that already rolls
        # checks into one status. A second observability authority beside this
        # one would be the duplicate this programme keeps deleting, and these
        # are per-condition rather than per-entity, so the check list stays the
        # same length however bad the day is.
        checks.extend(
            HardeningCheck(
                id=result.id,
                status=result.status,
                evidence="operational-reading" if result.value is not None else "not-measured",
                details=result.details,
            )
            for result in evaluate_alerts(await self._operational_reading())
        )
        checks.append(
            HardeningCheck(
                id="dependency-vulnerability-scan",
                status="NOT_VALIDATED",
                evidence="No signed dependency-scan receipt is registered at runtime.",
                details="Vulnerability count is intentionally not inferred or fabricated.",
            )
        )
        validated = [check for check in checks if check.status != "NOT_VALIDATED"]
        score = (
            round(100 * sum(check.status == "PASS" for check in validated) / len(validated))
            if validated
            else None
        )
        if any(check.status == "FAIL" for check in checks):
            overall: Literal["PASS", "DEGRADED", "FAIL", "NOT_VALIDATED"] = "FAIL"
        elif any(check.status == "WARN" for check in checks):
            overall = "DEGRADED"
        elif validated:
            overall = "PASS"
        else:
            overall = "NOT_VALIDATED"
        return HardeningSummary(
            status=overall,
            evaluatedAt=datetime.now(UTC),
            score=score,
            vulnerabilities=None,
            checks=checks,
        )


def resolve_audit_service(request: Request) -> AuditService:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(settings, Settings)
    ):
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable")
    return AuditService(resources.mongo, settings.mongo_database, resources, settings)


def _response_meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


@router.get("/audit", response_model=APIResponse[list[AuditLog]])
async def list_audit_logs(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[list[AuditLog]]:
    return APIResponse(
        data=await resolve_audit_service(request).list_logs(), meta=_response_meta(request)
    )


@router.get("/audit/{audit_id}", response_model=APIResponse[AuditLog])
async def get_audit_log(
    request: Request,
    audit_id: str,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[AuditLog]:
    data = await resolve_audit_service(request).get_log(audit_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Audit log not found")
    return APIResponse(data=data, meta=_response_meta(request))


@router.get("/governance", response_model=APIResponse[GovernanceSummary])
async def get_governance(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[GovernanceSummary]:
    return APIResponse(
        data=resolve_audit_service(request).governance(), meta=_response_meta(request)
    )


@router.get("/settings", response_model=APIResponse[ConsoleSettingsView])
async def get_settings(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[ConsoleSettingsView]:
    return APIResponse(
        data=resolve_audit_service(request).settings_view(), meta=_response_meta(request)
    )


@router.get("/hardening", response_model=APIResponse[HardeningSummary])
async def get_hardening(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[HardeningSummary]:
    return APIResponse(
        data=await resolve_audit_service(request).hardening(), meta=_response_meta(request)
    )
