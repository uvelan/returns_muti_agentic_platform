"""API routes for configured data sources and inventory details."""

from datetime import UTC, datetime
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from return_platform.data_console.api.auth import require_read_roles
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta, WarningMeta

router = APIRouter(prefix="/data-console/v1", tags=["Sources", "Inventory"])

_SOURCE: Final = "SOURCES"


class SourceItem(BaseModel):
    id: str
    name: str
    engine: str
    environment: str
    ownership: str
    health: str
    capability: str
    lastInventoryTime: str | None


class InventoryTotals(BaseModel):
    assets: int
    records: int


class SourceDetail(SourceItem):
    connectionIdentity: str
    inventoryTotals: InventoryTotals
    lastMetadataRefresh: str | None
    dependencyWarnings: list[str]


class InventoryDetail(BaseModel):
    assetId: str
    engine: str
    name: str
    ownership: str
    capability: str
    recordCount: int | None
    schemaVersion: str
    operations: list[str]
    metadata: dict


def _request_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) and value else "unknown"


def _response_meta(request: Request, warnings: list[WarningMeta] | None = None) -> ResponseMeta:
    return ResponseMeta(
        request_id=_request_id(request),
        partial=bool(warnings),
        warnings=tuple(warnings) if warnings else (),
    )


# Derive sources dynamically from catalog or keep known list
_STATIC_SOURCES = [
    {
        "id": "src-sql-omc",
        "name": "OMC SQL Server",
        "engine": "SQLSERVER",
        "ownership": "AUTHORITATIVE",
    },
    {
        "id": "src-mongo-returns",
        "name": "Returns MongoDB",
        "engine": "MONGODB",
        "ownership": "AUTHORITATIVE",
    },
]


@router.get("/sources", response_model=APIResponse[list[SourceItem]])
async def get_sources(
    request: Request, user_id: str = Depends(require_read_roles)
) -> APIResponse[list[SourceItem]]:
    resources_value: object = getattr(request.app.state, "resources", None)
    if not isinstance(resources_value, RuntimeResources):
        raise HTTPException(status_code=500, detail="Resources unavailable")

    sources = []
    # Identify unique stores in catalog
    catalog_stores = {asset.store.value for asset in resources_value.catalog.catalog.assets}

    for s in _STATIC_SOURCES:
        if s["engine"] in catalog_stores:
            sources.append(
                SourceItem(
                    id=s["id"],
                    name=s["name"],
                    engine=s["engine"],
                    environment="PRODUCTION",
                    ownership=s["ownership"],
                    health="HEALTHY",
                    capability="READ_ONLY",
                    lastInventoryTime=datetime.now(UTC).isoformat(),
                )
            )

    return APIResponse(
        data=sources,
        meta=_response_meta(request),
        page={"next_cursor": None, "has_more": False, "page_size": len(sources) or 10},
    )


@router.get("/sources/{source_id}", response_model=APIResponse[SourceDetail])
async def get_source(
    request: Request, source_id: str, user_id: str = Depends(require_read_roles)
) -> APIResponse[SourceDetail]:
    resources_value: object = getattr(request.app.state, "resources", None)
    if not isinstance(resources_value, RuntimeResources):
        raise HTTPException(status_code=500, detail="Resources unavailable")

    source_info = next((s for s in _STATIC_SOURCES if s["id"] == source_id), None)
    if not source_info:
        raise HTTPException(status_code=404, detail="Source not found")

    catalog_stores = {asset.store.value for asset in resources_value.catalog.catalog.assets}
    if source_info["engine"] not in catalog_stores:
        raise HTTPException(status_code=404, detail="Source not found in active catalog")

    assets_count = sum(
        1 for a in resources_value.catalog.catalog.assets if a.store.value == source_info["engine"]
    )

    detail = SourceDetail(
        id=source_info["id"],
        name=source_info["name"],
        engine=source_info["engine"],
        environment="PRODUCTION",
        ownership=source_info["ownership"],
        health="HEALTHY",
        capability="READ_ONLY",
        lastInventoryTime=datetime.now(UTC).isoformat(),
        connectionIdentity=f"conn-{source_id}",
        inventoryTotals=InventoryTotals(assets=assets_count, records=0),
        lastMetadataRefresh=datetime.now(UTC).isoformat(),
        dependencyWarnings=[],
    )

    return APIResponse(
        data=detail,
        meta=_response_meta(request),
        page={"next_cursor": None, "has_more": False, "page_size": 1},
    )


@router.get("/inventory/{engine}/{asset_id}", response_model=APIResponse[InventoryDetail])
async def get_inventory_detail(
    request: Request, engine: str, asset_id: str, user_id: str = Depends(require_read_roles)
) -> APIResponse[InventoryDetail]:
    resources_value: object = getattr(request.app.state, "resources", None)
    if not isinstance(resources_value, RuntimeResources):
        raise HTTPException(status_code=500, detail="Resources unavailable")

    engine_upper = engine.upper()
    catalog_entry = next(
        (
            a
            for a in resources_value.catalog.catalog.assets
            if a.asset_id == asset_id and a.store.value == engine_upper
        ),
        None,
    )
    if not catalog_entry:
        raise HTTPException(status_code=404, detail="Asset not found in catalog")

    # Determine name
    name = catalog_entry.object_name
    if catalog_entry.namespace:
        name = f"{catalog_entry.namespace}.{name}"

    detail = InventoryDetail(
        assetId=catalog_entry.asset_id,
        engine=catalog_entry.store.value,
        name=name,
        ownership=catalog_entry.ownership.value,
        capability="READ_ONLY",
        recordCount=None,
        schemaVersion="1.0",
        operations=[op.value for op in catalog_entry.allowed_operations],
        metadata={
            "database": catalog_entry.database,
            "namespace": catalog_entry.namespace,
            "objectKind": catalog_entry.object_kind.value,
            "authoritative": catalog_entry.authoritative,
        },
    )

    return APIResponse(
        data=detail,
        meta=_response_meta(request),
        page={"next_cursor": None, "has_more": False, "page_size": 1},
    )
