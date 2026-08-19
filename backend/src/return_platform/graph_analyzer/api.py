from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.api.dependency_probes import probe_source_mongodb, probe_sqlserver
from return_platform.graph_analyzer.models import (
    AgentReply,
    AgentRequest,
    AnalysisRequest,
    AnalysisRun,
    AnalyzerBootstrap,
    AnalyzerGraphSchema,
    AnalyzerSource,
    GraphEntity,
    GraphRelationship,
    PreviewPage,
    RecommendationDecision,
    RecommendationResult,
    SchemaValidation,
    SourceInput,
    SyncRequest,
    SyncRun,
)
from return_platform.graph_analyzer.service import GraphAnalyzerService
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_capability
from return_platform.security.capabilities import (
    GRAPH_SCHEMA_DRAFT_READ,
    GRAPH_SCHEMA_DRAFT_WRITE,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta

require_analyzer_read = require_capability(GRAPH_SCHEMA_DRAFT_READ)
require_analyzer_write = require_capability(GRAPH_SCHEMA_DRAFT_WRITE)

router = APIRouter(prefix="/graph-analyzer/v1", tags=["Graph Schema Analyzer"])


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
            status_code=503, detail="Graph Schema Analyzer dependencies are unavailable."
        )
    return resources


def _service(request: Request) -> GraphAnalyzerService:
    resources = _resources(request)
    assert resources.mongo is not None
    assert resources.source_mongo is not None
    assert resources.neo4j is not None
    assert resources.schema_registry is not None
    return GraphAnalyzerService(
        platform_client=resources.mongo,
        source_client=resources.source_mongo,
        graph_driver=resources.neo4j,
        settings=resources.settings,
        registry=resources.schema_registry,
    )


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


async def _ready_service(request: Request) -> GraphAnalyzerService:
    service = _service(request)
    await service.ensure_indexes()
    return service


@router.get("/bootstrap", response_model=APIResponse[AnalyzerBootstrap])
async def bootstrap(
    request: Request, _actor: str = Depends(require_analyzer_read)
) -> APIResponse[AnalyzerBootstrap]:
    return APIResponse(data=await (await _ready_service(request)).bootstrap(), meta=_meta(request))


@router.post("/sources/test", response_model=APIResponse[dict[str, str]])
async def test_source(
    payload: SourceInput,
    request: Request,
    sourceId: str | None = Query(default=None),
    _actor: str = Depends(require_analyzer_write),
) -> APIResponse[dict[str, str]]:
    try:
        result, message = await (await _ready_service(request)).test_source(payload, sourceId)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Source configuration not found.") from error
    return APIResponse(data={"status": result, "message": message}, meta=_meta(request))


@router.post(
    "/sources", response_model=APIResponse[AnalyzerSource], status_code=status.HTTP_201_CREATED
)
async def create_source(
    payload: SourceInput, request: Request, _actor: str = Depends(require_analyzer_write)
) -> APIResponse[AnalyzerSource]:
    try:
        source = await (await _ready_service(request)).save_source(payload, None)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=source, meta=_meta(request))


@router.put("/sources/{source_id}", response_model=APIResponse[AnalyzerSource])
async def update_source(
    source_id: str,
    payload: SourceInput,
    request: Request,
    _actor: str = Depends(require_analyzer_write),
) -> APIResponse[AnalyzerSource]:
    try:
        source = await (await _ready_service(request)).save_source(payload, source_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=source, meta=_meta(request))


@router.delete("/sources/{source_id}", response_model=APIResponse[None])
async def delete_source(
    source_id: str, request: Request, _actor: str = Depends(require_analyzer_write)
) -> APIResponse[None]:
    removed = await (await _ready_service(request)).delete_source(source_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Source configuration not found.")
    return APIResponse(data=None, meta=_meta(request))


@router.post("/sources/{source_id}/validate", response_model=APIResponse[AnalyzerSource])
@router.post("/sources/{source_id}/metadata", response_model=APIResponse[AnalyzerSource])
async def validate_source(
    source_id: str, request: Request, _actor: str = Depends(require_analyzer_write)
) -> APIResponse[AnalyzerSource]:
    service = await _ready_service(request)
    built_in = next(
        (
            source
            for source in await service.list_sources()
            if source.id == source_id and source.id.startswith("configured:")
        ),
        None,
    )
    if built_in is not None:
        probe = (
            await probe_source_mongodb(request)
            if built_in.engine == "MONGODB"
            else await probe_sqlserver(request)
        )
        if probe.status.value == "HEALTHY":
            source_status = "CONNECTED"
        elif probe.error_code is not None and probe.error_code.value == "AUTH_FAILED":
            source_status = "AUTHENTICATION_FAILED"
        else:
            source_status = "UNREACHABLE"
        return APIResponse(
            data=built_in.model_copy(
                update={"status": source_status, "lastValidatedAt": probe.checked_at}
            ),
            meta=_meta(request),
        )
    try:
        source = await service.validate_source(source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Source configuration not found.") from error
    return APIResponse(data=source, meta=_meta(request))


@router.get("/sources/{source_id}/preview", response_model=APIResponse[PreviewPage])
async def preview_source(
    source_id: str,
    request: Request,
    objectId: str = Query(min_length=1),
    page: int = Query(default=1, ge=1, le=10_000),
    pageSize: int = Query(default=25, ge=1, le=100),
    _actor: str = Depends(require_analyzer_read),
) -> APIResponse[PreviewPage]:
    del source_id
    try:
        preview = await (await _ready_service(request)).preview(objectId, page, pageSize)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Source object not found.") from error
    return APIResponse(data=preview, meta=_meta(request))


@router.post(
    "/analyses", response_model=APIResponse[AnalysisRun], status_code=status.HTTP_202_ACCEPTED
)
async def start_analysis(
    payload: AnalysisRequest, request: Request, _actor: str = Depends(require_analyzer_write)
) -> APIResponse[AnalysisRun]:
    try:
        run = await (await _ready_service(request)).start_analysis(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=run, meta=_meta(request))


@router.get("/analyses/{run_id}", response_model=APIResponse[AnalysisRun])
async def get_analysis(
    run_id: str, request: Request, _actor: str = Depends(require_analyzer_read)
) -> APIResponse[AnalysisRun]:
    run = await (await _ready_service(request)).get_analysis(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found.")
    return APIResponse(data=run, meta=_meta(request))


@router.get("/schemas", response_model=APIResponse[dict[str, AnalyzerGraphSchema | None]])
async def get_schemas(
    request: Request, _actor: str = Depends(require_analyzer_read)
) -> APIResponse[dict[str, AnalyzerGraphSchema | None]]:
    service = await _ready_service(request)
    return APIResponse(
        data={"existing": service.existing_schema(), "proposed": await service.proposed_schema()},
        meta=_meta(request),
    )


@router.put(
    "/schemas/proposed/entities/{entity_id}", response_model=APIResponse[AnalyzerGraphSchema]
)
async def update_entity(
    entity_id: str,
    payload: GraphEntity,
    request: Request,
    _actor: str = Depends(require_analyzer_write),
) -> APIResponse[AnalyzerGraphSchema]:
    if payload.id != entity_id:
        raise HTTPException(status_code=409, detail="Entity route and payload IDs do not match.")
    service = await _ready_service(request)
    schema = await service.proposed_schema()
    if schema is None or not any(entity.id == entity_id for entity in schema.entities):
        raise HTTPException(status_code=404, detail="Proposed graph entity not found.")
    updated = schema.model_copy(
        update={
            "entities": [
                payload if entity.id == entity_id else entity for entity in schema.entities
            ]
        }
    )
    return APIResponse(data=await service.save_schema(updated), meta=_meta(request))


@router.put(
    "/schemas/proposed/relationships/{relationship_id}",
    response_model=APIResponse[AnalyzerGraphSchema],
)
async def update_relationship(
    relationship_id: str,
    payload: GraphRelationship,
    request: Request,
    _actor: str = Depends(require_analyzer_write),
) -> APIResponse[AnalyzerGraphSchema]:
    if payload.id != relationship_id:
        raise HTTPException(
            status_code=409, detail="Relationship route and payload IDs do not match."
        )
    service = await _ready_service(request)
    schema = await service.proposed_schema()
    if schema is None or not any(item.id == relationship_id for item in schema.relationships):
        raise HTTPException(status_code=404, detail="Proposed graph relationship not found.")
    updated = schema.model_copy(
        update={
            "relationships": [
                payload if item.id == relationship_id else item for item in schema.relationships
            ]
        }
    )
    return APIResponse(data=await service.save_schema(updated), meta=_meta(request))


@router.post("/schemas/proposed/validate", response_model=APIResponse[SchemaValidation])
async def validate_schema(
    request: Request, _actor: str = Depends(require_analyzer_write)
) -> APIResponse[SchemaValidation]:
    try:
        result = await (await _ready_service(request)).validate_schema()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post("/schemas/proposed/finalize", response_model=APIResponse[AnalyzerGraphSchema])
async def finalize_schema(
    request: Request, _actor: str = Depends(require_analyzer_write)
) -> APIResponse[AnalyzerGraphSchema]:
    try:
        result = await (await _ready_service(request)).finalize_schema()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post("/agent/messages", response_model=APIResponse[AgentReply])
async def ask_agent(
    payload: AgentRequest, request: Request, _actor: str = Depends(require_analyzer_write)
) -> APIResponse[AgentReply]:
    return APIResponse(
        data=await (await _ready_service(request)).ask_agent(payload), meta=_meta(request)
    )


@router.post(
    "/agent/recommendations/{recommendation_id}", response_model=APIResponse[RecommendationResult]
)
async def review_recommendation(
    recommendation_id: str,
    payload: RecommendationDecision,
    request: Request,
    _actor: str = Depends(require_analyzer_write),
) -> APIResponse[RecommendationResult]:
    try:
        result = await (await _ready_service(request)).review_recommendation(
            recommendation_id, payload.decision == "APPLY"
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent recommendation not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post(
    "/sync/runs", response_model=APIResponse[SyncRun], status_code=status.HTTP_202_ACCEPTED
)
async def start_sync(
    payload: SyncRequest, request: Request, _actor: str = Depends(require_analyzer_write)
) -> APIResponse[SyncRun]:
    try:
        result = await (await _ready_service(request)).start_sync(payload)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"System graph synchronization failed: {type(error).__name__}"
        ) from error
    return APIResponse(data=result, meta=_meta(request))


@router.get("/sync/runs/{run_id}", response_model=APIResponse[SyncRun])
async def get_sync_run(
    run_id: str, request: Request, _actor: str = Depends(require_analyzer_read)
) -> APIResponse[SyncRun]:
    result = await (await _ready_service(request)).get_sync(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Synchronization run not found.")
    return APIResponse(data=result, meta=_meta(request))
