"""Graph schema and governed source-to-Neo4j synchronization APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.data_platform.graph.schema import GraphSchemaManager
from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncRunView,
    GraphSyncService,
)
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/data-console/v1/graph-sync", tags=["Graph Sync"])


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources):
        raise HTTPException(status_code=503, detail="Application resources are unavailable.")
    if (
        resources.mongo is None
        or resources.source_mongo is None
        or resources.neo4j is None
        or resources.schema_registry is None
    ):
        raise HTTPException(
            status_code=503,
            detail="Graph synchronization dependencies are unavailable.",
        )
    return resources


def _service(request: Request) -> GraphSyncService:
    resources = _resources(request)
    assert resources.mongo is not None
    assert resources.source_mongo is not None
    assert resources.neo4j is not None
    assert resources.schema_registry is not None
    return GraphSyncService(
        platform_client=resources.mongo,
        source_client=resources.source_mongo,
        driver=resources.neo4j,
        settings=resources.settings,
        registry=resources.schema_registry,
    )


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("/runs", response_model=APIResponse[list[GraphSyncRunView]])
async def list_sync_runs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[GraphSyncRunView]]:
    service = _service(request)
    await service.ensure_indexes()
    return APIResponse(data=await service.list_runs(limit), meta=_meta(request))


@router.get("/runs/{run_id}", response_model=APIResponse[GraphSyncRunView])
async def get_sync_run(
    run_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[GraphSyncRunView]:
    result = await _service(request).get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Graph sync run not found.")
    return APIResponse(data=result, meta=_meta(request))


@router.post("/runs", response_model=APIResponse[GraphSyncRunView], status_code=202)
async def execute_graph_sync(
    payload: GraphSyncRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[GraphSyncRunView]:
    service = _service(request)
    await service.ensure_indexes()
    try:
        result = await service.sync(payload, actor_id=actor)
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Graph synchronization failed: {type(error).__name__}",
        ) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post("/schema/apply", response_model=APIResponse[list[str]])
async def apply_graph_schema(
    request: Request,
    _actor: str = Depends(require_write_roles),
) -> APIResponse[list[str]]:
    resources = _resources(request)
    assert resources.neo4j is not None
    assert resources.schema_registry is not None
    manager = GraphSchemaManager(
        resources.neo4j,
        resources.settings.neo4j_database,
        resources.schema_registry.graph,
    )
    return APIResponse(data=await manager.apply(), meta=_meta(request))
