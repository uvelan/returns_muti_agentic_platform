"""Live data-source inventory derived from the governed schema registry and probes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from return_platform.api.dependency_probes import (
    probe_mongodb,
    probe_neo4j,
    probe_source_mongodb,
    probe_sqlserver,
)
from return_platform.data_platform.schema_registry import DataAssetSchema, SchemaRegistry
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_read_roles
from return_platform.shared.contracts import (
    APIResponse,
    DependencyProbeResult,
    DependencyStatus,
    PageMeta,
    ResponseMeta,
)

router = APIRouter(prefix="/data-console/v1", tags=["Sources", "Inventory"])


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceItem(SourceModel):
    id: str
    name: str
    engine: str
    environment: str
    ownership: str
    health: str
    capability: str
    lastInventoryTime: datetime | None


class InventoryTotals(SourceModel):
    assets: int
    records: int | None


class SourceAssetSummary(SourceModel):
    assetId: str
    name: str
    kind: str
    ownership: str
    authoritative: bool
    writableInSandbox: bool


class SourceDetail(SourceItem):
    connectionIdentity: str
    inventoryTotals: InventoryTotals
    lastMetadataRefresh: datetime | None
    dependencyWarnings: list[str]
    assets: list[SourceAssetSummary]


class InventoryDetail(SourceModel):
    assetId: str
    engine: str
    name: str
    ownership: str
    capability: str
    recordCount: int | None
    schemaVersion: str
    operations: list[str]
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    source_id: str
    name: str
    engine: str
    ownership: str
    capability: str
    connection_identity: str
    assets: tuple[DataAssetSchema, ...]
    probe: Callable[[Request], Awaitable[DependencyProbeResult]]


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.schema_registry is None:
        raise HTTPException(status_code=503, detail="Schema and runtime resources are unavailable.")
    return resources


def _request_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) and value else "unknown"


def _response_meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=_request_id(request))


def _environment(resources: RuntimeResources) -> str:
    return {
        "development": "LOCAL",
        "test": "SANDBOX",
        "staging": "STAGING",
        "production": "PRODUCTION",
    }[resources.settings.environment]


def _definitions(
    resources: RuntimeResources, registry: SchemaRegistry
) -> tuple[SourceDefinition, ...]:
    source_mongo = tuple(
        asset
        for asset in registry.assets
        if asset.engine == "MONGODB" and asset.database == resources.settings.source_mongo_database
    )
    platform_mongo = tuple(
        asset
        for asset in registry.assets
        if asset.engine == "MONGODB" and asset.database == resources.settings.mongo_database
    )
    sql_assets = tuple(asset for asset in registry.assets if asset.engine == "SQLSERVER")
    return (
        SourceDefinition(
            source_id="source-mongodb",
            name="Source MongoDB",
            engine="MONGODB",
            ownership="AUTHORITATIVE",
            capability="READ_ONLY",
            connection_identity=f"mongodb/{resources.settings.source_mongo_database}",
            assets=source_mongo,
            probe=probe_source_mongodb,
        ),
        SourceDefinition(
            source_id="platform-mongodb",
            name="Platform MongoDB",
            engine="PLATFORM",
            ownership="INTERNAL",
            capability="WRITABLE",
            connection_identity=f"mongodb/{resources.settings.mongo_database}",
            assets=platform_mongo,
            probe=probe_mongodb,
        ),
        SourceDefinition(
            source_id="sqlserver",
            name="Return Business State SQL Server",
            engine="SQL_SERVER",
            ownership="AUTHORITATIVE",
            capability="READ_ONLY",
            connection_identity=f"sqlserver/{resources.settings.sqlserver_database}",
            assets=sql_assets,
            probe=probe_sqlserver,
        ),
        SourceDefinition(
            source_id="neo4j",
            name="Neo4j Graph Projection",
            engine="NEO4J",
            ownership="DERIVED",
            capability="READ_ONLY",
            connection_identity=f"neo4j/{resources.settings.neo4j_database}",
            assets=(),
            probe=probe_neo4j,
        ),
    )


def _health(probe: DependencyProbeResult) -> str:
    if probe.status is DependencyStatus.HEALTHY:
        return "HEALTHY"
    if probe.status in {DependencyStatus.DEGRADED, DependencyStatus.STARTING}:
        return "DEGRADED"
    if probe.status is DependencyStatus.UNAVAILABLE:
        return "UNAVAILABLE"
    return "UNKNOWN"


def _asset_summary(asset: DataAssetSchema) -> SourceAssetSummary:
    qualified = f"{asset.namespace}.{asset.name}" if asset.namespace else asset.name
    return SourceAssetSummary(
        assetId=asset.asset_id,
        name=qualified,
        kind="TABLE" if asset.engine == "SQLSERVER" else "COLLECTION",
        ownership=asset.ownership,
        authoritative=asset.authoritative,
        writableInSandbox=asset.writable_in_sandbox,
    )


def _source_item(
    definition: SourceDefinition,
    probe: DependencyProbeResult,
    environment: str,
) -> SourceItem:
    return SourceItem(
        id=definition.source_id,
        name=definition.name,
        engine=definition.engine,
        environment=environment,
        ownership=definition.ownership,
        health=_health(probe),
        capability=definition.capability,
        lastInventoryTime=probe.checked_at,
    )


def source_definition(request: Request, source_id: str) -> SourceDefinition | None:
    """One configured source and the assets it owns, or `None` if unknown.

    Public because the canonical `/api/config` router needs to answer "does this
    asset belong to this source" before it delegates, and containment is the only
    thing a nested `/sources/{id}/assets/{id}` path claims that the flat
    inventory read does not.

    Probe-free on purpose: containment is a schema-registry fact, so fanning out
    four dependency probes to establish it would make a metadata read depend on
    the health of every datastore in the deployment.
    """
    resources = _resources(request)
    registry = cast(SchemaRegistry, resources.schema_registry)
    return next(
        (
            definition
            for definition in _definitions(resources, registry)
            if definition.source_id == source_id
        ),
        None,
    )


async def _probed_sources(
    request: Request,
) -> tuple[RuntimeResources, tuple[SourceDefinition, ...], list[DependencyProbeResult]]:
    resources = _resources(request)
    registry = cast(SchemaRegistry, resources.schema_registry)
    definitions = _definitions(resources, registry)
    probes = await asyncio.gather(*(definition.probe(request) for definition in definitions))
    return resources, definitions, list(probes)


@router.get("/sources", response_model=APIResponse[list[SourceItem]])
async def get_sources(
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[SourceItem]]:
    resources, definitions, probes = await _probed_sources(request)
    views = [
        _source_item(definition, probe, _environment(resources))
        for definition, probe in zip(definitions, probes, strict=True)
    ]
    return APIResponse(
        data=views,
        meta=_response_meta(request),
        page=PageMeta(next_cursor=None, has_more=False, page_size=len(views)),
    )


@router.get("/sources/{source_id}", response_model=APIResponse[SourceDetail])
async def get_source(
    source_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[SourceDetail]:
    resources, definitions, probes = await _probed_sources(request)
    selected = next(
        (
            (definition, probe)
            for definition, probe in zip(definitions, probes, strict=True)
            if definition.source_id == source_id
        ),
        None,
    )
    if selected is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    definition, probe = selected
    base = _source_item(definition, probe, _environment(resources))
    registry = cast(SchemaRegistry, resources.schema_registry)
    graph_asset_count = (
        len(registry.graph.nodes) + len(registry.graph.relationships)
        if definition.source_id == "neo4j"
        else 0
    )
    warnings = [probe.safe_message] if probe.safe_message else []
    detail = SourceDetail(
        **base.model_dump(),
        connectionIdentity=definition.connection_identity,
        inventoryTotals=InventoryTotals(
            assets=len(definition.assets) + graph_asset_count,
            records=None,
        ),
        lastMetadataRefresh=probe.checked_at,
        dependencyWarnings=warnings,
        assets=[_asset_summary(asset) for asset in definition.assets],
    )
    return APIResponse(data=detail, meta=_response_meta(request))


@router.get("/inventory/{engine}/{asset_id}", response_model=APIResponse[InventoryDetail])
async def get_inventory_detail(
    engine: str,
    asset_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[InventoryDetail]:
    resources = _resources(request)
    registry = cast(SchemaRegistry, resources.schema_registry)
    try:
        asset = registry.asset(asset_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail="Asset not found in schema registry."
        ) from error
    expected_engine = "SQL_SERVER" if asset.engine == "SQLSERVER" else "MONGODB"
    if engine.upper() not in {asset.engine, expected_engine}:
        raise HTTPException(status_code=404, detail="Asset engine does not match the route.")
    qualified = f"{asset.namespace}.{asset.name}" if asset.namespace else asset.name
    detail = InventoryDetail(
        assetId=asset.asset_id,
        engine=expected_engine,
        name=qualified,
        ownership=asset.ownership,
        capability="READ_ONLY",
        recordCount=None,
        schemaVersion=registry.schema_version,
        operations=["READ", "INSPECT_SCHEMA"],
        metadata={
            "database": asset.database,
            "namespace": asset.namespace,
            "authoritative": asset.authoritative,
            "writableInSandbox": asset.writable_in_sandbox,
            "fields": [field.model_dump() for field in asset.fields],
        },
    )
    return APIResponse(data=detail, meta=_response_meta(request))
