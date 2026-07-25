"""Version-controlled physical and graph schema catalog APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request

from return_platform.data_console.api.auth import require_read_roles
from return_platform.data_platform.schema_registry import (
    DataAssetSchema,
    GraphSchema,
    SchemaRegistry,
)
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/data-console/v1/schema", tags=["Schema Catalog"])


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources):
        raise HTTPException(status_code=503, detail="Application resources are unavailable.")
    return resources


def _registry(request: Request) -> SchemaRegistry:
    registry = _resources(request).schema_registry
    if registry is None:
        raise HTTPException(status_code=503, detail="Schema registry is unavailable.")
    return registry


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("", response_model=APIResponse[SchemaRegistry])
async def get_schema_registry(
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[SchemaRegistry]:
    return APIResponse(data=_registry(request), meta=_meta(request))


@router.get("/assets", response_model=APIResponse[list[DataAssetSchema]])
async def list_schema_assets(
    request: Request,
    engine: str | None = None,
    ownership: str | None = None,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[DataAssetSchema]]:
    assets = list(_registry(request).assets)
    if engine:
        assets = [asset for asset in assets if asset.engine == engine.upper()]
    if ownership:
        assets = [asset for asset in assets if asset.ownership == ownership.upper()]
    return APIResponse(data=assets, meta=_meta(request))


@router.get("/assets/{asset_id}", response_model=APIResponse[DataAssetSchema])
async def get_schema_asset(
    asset_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[DataAssetSchema]:
    try:
        asset = _registry(request).asset(asset_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Schema asset not found.") from error
    return APIResponse(data=asset, meta=_meta(request))


@router.get("/graph", response_model=APIResponse[GraphSchema])
async def get_graph_schema(
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[GraphSchema]:
    return APIResponse(data=_registry(request).graph, meta=_meta(request))
