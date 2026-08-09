"""V2 modular configuration, schema design, and canonical order-sync APIs."""

from __future__ import annotations

import json
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from return_platform.data_console.api.auth import (
    require_admin_roles,
    require_associate_roles,
    require_read_roles,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.v2.models import (
    ConfigurationModule,
    DraftCreate,
    FieldPatch,
    FullSyncRequest,
    ImportRecord,
    ImportRequest,
    ModuleCreate,
    ModuleStatus,
    PartialSyncRequest,
    PayloadUpdate,
    ReleaseCreate,
    ReleaseManifest,
    ReleaseStatus,
    SchemaAnswer,
    SchemaDesignContext,
    SchemaDesignCreate,
    SyncResult,
    ValidationResult,
)
from return_platform.v2.services import (
    V2ConflictError,
    V2NotFoundError,
    V2PlatformServices,
    V2ValidationError,
)
from return_platform.v2.sync_jobs import JobClaimRequest, SyncJob

router = APIRouter(prefix="/api/v2", tags=["Returns Platform V2"])


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _services(request: Request) -> V2PlatformServices:
    services = getattr(request.app.state, "v2_platform_services", None)
    if not isinstance(services, V2PlatformServices):
        raise HTTPException(status_code=503, detail="V2 platform services are unavailable")
    return services


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, V2NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, V2ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, V2ValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="V2 operation failed")


@router.get("/configuration/module-schemas", response_model=APIResponse[list[dict[str, Any]]])
async def module_schemas(
    request: Request, _actor: str = Depends(require_read_roles)
) -> APIResponse[list[dict[str, Any]]]:
    data = await _services(request).configuration.module_schemas()
    return APIResponse(data=data, meta=_meta(request))


@router.get("/configuration/modules", response_model=APIResponse[list[ConfigurationModule]])
async def list_modules(
    request: Request,
    module_type: str | None = Query(default=None, alias="moduleType"),  # noqa: B008
    module_status: ModuleStatus | None = Query(default=None, alias="status"),  # noqa: B008
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[ConfigurationModule]]:
    data = await _services(request).configuration.list_modules(module_type, module_status)
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/configuration/modules",
    response_model=APIResponse[ConfigurationModule],
    status_code=status.HTTP_201_CREATED,
)
async def create_module(
    body: ModuleCreate,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[ConfigurationModule]:
    try:
        data = await _services(request).configuration.create_module(body, actor)
    except (V2ConflictError, V2ValidationError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.get(
    "/configuration/modules/{module_id}/versions/{version}",
    response_model=APIResponse[ConfigurationModule],
)
async def get_module(
    module_id: str,
    version: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[ConfigurationModule]:
    try:
        data = await _services(request).configuration.get_module(module_id, version)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/configuration/modules/{module_id}/drafts",
    response_model=APIResponse[ConfigurationModule],
    status_code=status.HTTP_201_CREATED,
)
async def create_draft(
    module_id: str,
    body: DraftCreate,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[ConfigurationModule]:
    try:
        data = await _services(request).configuration.create_draft(module_id, body, actor)
    except (V2NotFoundError, V2ConflictError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.patch(
    "/configuration/modules/{module_id}/drafts/{version}/fields",
    response_model=APIResponse[ConfigurationModule],
)
async def patch_module_field(
    module_id: str,
    version: str,
    body: FieldPatch,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[ConfigurationModule]:
    try:
        data = await _services(request).configuration.patch_fields(module_id, version, body, actor)
    except (V2NotFoundError, V2ConflictError, V2ValidationError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.put(
    "/configuration/modules/{module_id}/drafts/{version}/payload",
    response_model=APIResponse[ConfigurationModule],
)
async def put_module_payload(
    module_id: str,
    version: str,
    body: PayloadUpdate,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[ConfigurationModule]:
    try:
        data = await _services(request).configuration.update_payload(
            module_id, version, body, actor
        )
    except (V2NotFoundError, V2ConflictError, V2ValidationError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/configuration/modules/{module_id}/drafts/{version}/validate",
    response_model=APIResponse[ValidationResult],
)
async def validate_module(
    module_id: str,
    version: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ValidationResult]:
    try:
        data = await _services(request).configuration.validate_module(module_id, version)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/configuration/modules/{module_id}/drafts/{version}/submit",
    response_model=APIResponse[ConfigurationModule],
)
async def submit_module(
    module_id: str,
    version: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ConfigurationModule]:
    try:
        data = await _services(request).configuration.transition_module(
            module_id, version, ModuleStatus.VALIDATED
        )
    except (V2NotFoundError, V2ConflictError, V2ValidationError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/configuration/modules/{module_id}/drafts/{version}/approve",
    response_model=APIResponse[ConfigurationModule],
)
async def approve_module(
    module_id: str,
    version: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ConfigurationModule]:
    try:
        data = await _services(request).configuration.transition_module(
            module_id, version, ModuleStatus.APPROVED
        )
    except (V2NotFoundError, V2ConflictError, V2ValidationError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.get("/configuration/modules/{module_id}/versions/{version}/download")
async def download_module(
    module_id: str,
    version: str,
    request: Request,
    format_name: Literal["JSON", "YAML"] = Query(default="YAML", alias="format"),
    _actor: str = Depends(require_read_roles),
) -> Response:
    try:
        content = await _services(request).configuration.export_module(
            module_id, version, format_name
        )
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    media_type = "application/yaml" if format_name == "YAML" else "application/json"
    suffix = "yaml" if format_name == "YAML" else "json"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{module_id}-{version}.{suffix}"'},
    )


@router.get("/configuration/releases", response_model=APIResponse[list[ReleaseManifest]])
async def list_v2_releases(
    request: Request, _actor: str = Depends(require_read_roles)
) -> APIResponse[list[ReleaseManifest]]:
    return APIResponse(
        data=await _services(request).configuration.list_releases(), meta=_meta(request)
    )


@router.post(
    "/configuration/releases",
    response_model=APIResponse[ReleaseManifest],
    status_code=status.HTTP_201_CREATED,
)
async def create_v2_release(
    body: ReleaseCreate,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[ReleaseManifest]:
    try:
        data = await _services(request).configuration.create_release(body, actor)
    except V2ConflictError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.get("/configuration/releases/{release_id}", response_model=APIResponse[ReleaseManifest])
async def get_v2_release(
    release_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[ReleaseManifest]:
    try:
        data = await _services(request).configuration.get_release(release_id)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


async def _release_transition(
    release_id: str, target: ReleaseStatus, request: Request
) -> APIResponse[ReleaseManifest]:
    try:
        data = await _services(request).configuration.transition_release(release_id, target)
    except (V2NotFoundError, V2ConflictError, V2ValidationError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/configuration/releases/{release_id}/resolve",
    response_model=APIResponse[ReleaseManifest],
)
async def resolve_v2_release(
    release_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ReleaseManifest]:
    return await _release_transition(release_id, ReleaseStatus.DEPENDENCIES_RESOLVED, request)


@router.post(
    "/configuration/releases/{release_id}/validate",
    response_model=APIResponse[ReleaseManifest],
)
async def validate_v2_release(
    release_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ReleaseManifest]:
    return await _release_transition(release_id, ReleaseStatus.VALIDATED, request)


class ReleaseTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["APPROVED", "MIGRATION_READY"]


@router.post(
    "/configuration/releases/{release_id}/transition",
    response_model=APIResponse[ReleaseManifest],
)
async def transition_v2_release(
    release_id: str,
    body: ReleaseTransition,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ReleaseManifest]:
    return await _release_transition(release_id, ReleaseStatus(body.target), request)


@router.post(
    "/configuration/releases/{release_id}/activate",
    response_model=APIResponse[ReleaseManifest],
)
async def activate_v2_release(
    release_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ReleaseManifest]:
    return await _release_transition(release_id, ReleaseStatus.ACTIVE, request)


@router.get("/configuration/releases/{release_id}/download")
async def download_v2_release(
    release_id: str,
    request: Request,
    format_name: Literal["JSON", "YAML"] = Query(default="YAML", alias="format"),
    _actor: str = Depends(require_read_roles),
) -> Response:
    service = _services(request).configuration
    try:
        release = await service.get_release(release_id)
        modules = [
            await service.get_module(reference.module_id, reference.version)
            for reference in release.modules
        ]
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    package = {
        "manifest": release.model_dump(mode="json", by_alias=True),
        "modules": [item.model_dump(mode="json", by_alias=True) for item in modules],
        "secretsIncluded": False,
        "activatable": release.status
        in {ReleaseStatus.APPROVED, ReleaseStatus.MIGRATION_READY, ReleaseStatus.ACTIVE},
    }
    content = (
        yaml.safe_dump(package, sort_keys=False)
        if format_name == "YAML"
        else json.dumps(package, indent=2, sort_keys=True)
    )
    suffix = "yaml" if format_name == "YAML" else "json"
    return Response(
        content=content,
        media_type="application/yaml" if suffix == "yaml" else "application/json",
        headers={"Content-Disposition": f'attachment; filename="{release_id}.{suffix}"'},
    )


@router.post(
    "/configuration/imports",
    response_model=APIResponse[ImportRecord],
    status_code=status.HTTP_201_CREATED,
)
async def import_configuration(
    body: ImportRequest,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[ImportRecord]:
    data = await _services(request).configuration.import_modules(body, actor)
    return APIResponse(data=data, meta=_meta(request))


@router.get("/configuration/imports/{import_id}", response_model=APIResponse[ImportRecord])
async def get_configuration_import(
    import_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[ImportRecord]:
    try:
        data = await _services(request).configuration.get_import(import_id)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/configuration/imports/{import_id}/create-drafts",
    response_model=APIResponse[ImportRecord],
)
async def create_import_drafts(
    import_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ImportRecord]:
    try:
        data = await _services(request).configuration.create_import_drafts(import_id)
    except (V2NotFoundError, V2ConflictError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/schema-design/requests",
    response_model=APIResponse[SchemaDesignContext],
    status_code=status.HTTP_201_CREATED,
)
async def create_schema_design(
    body: SchemaDesignCreate,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[SchemaDesignContext]:
    data = await _services(request).schema_design.create(body, actor)
    return APIResponse(data=data, meta=_meta(request))


@router.get("/schema-design/requests/{request_id}", response_model=APIResponse[SchemaDesignContext])
async def get_schema_design(
    request_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[SchemaDesignContext]:
    try:
        data = await _services(request).schema_design.get(request_id)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/schema-design/requests/{request_id}/next-question",
    response_model=APIResponse[SchemaDesignContext],
)
async def next_schema_question(
    request_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[SchemaDesignContext]:
    try:
        data = await _services(request).schema_design.next_question(request_id)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/schema-design/requests/{request_id}/answers",
    response_model=APIResponse[SchemaDesignContext],
)
async def answer_schema_question(
    request_id: str,
    body: SchemaAnswer,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[SchemaDesignContext]:
    try:
        data = await _services(request).schema_design.answer(request_id, body)
    except (V2NotFoundError, V2ConflictError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/schema-design/requests/{request_id}/validate",
    response_model=APIResponse[ValidationResult],
)
async def validate_schema_design(
    request_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[ValidationResult]:
    try:
        data = await _services(request).schema_design.validate(request_id)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/schema-design/requests/{request_id}/simulate",
    response_model=APIResponse[dict[str, Any]],
)
async def simulate_schema_design(
    request_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[dict[str, Any]]:
    try:
        data = await _services(request).schema_design.simulate(request_id)
    except (V2NotFoundError, V2ValidationError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


async def _require_active_release(request: Request, release_id: str) -> None:
    active = await _services(request).configuration.active_release()
    if active is None:
        raise HTTPException(status_code=409, detail="SCHEMA_NOT_ACTIVE")
    if active.release_id != release_id:
        raise HTTPException(status_code=409, detail="Requested release is not active")


@router.post(
    "/order-sync/jobs/partial",
    response_model=APIResponse[SyncJob],
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_partial_order_sync(
    body: PartialSyncRequest,
    request: Request,
    max_attempts: int = Query(default=3, alias="maxAttempts", ge=1, le=10),  # noqa: B008
    _actor: str = Depends(require_associate_roles),
) -> APIResponse[SyncJob]:
    await _require_active_release(request, body.release_id)
    data = await _services(request).order_jobs.enqueue_partial(body, max_attempts)
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/order-sync/jobs/full",
    response_model=APIResponse[SyncJob],
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_full_order_sync(
    body: FullSyncRequest,
    request: Request,
    max_attempts: int = Query(default=3, alias="maxAttempts", ge=1, le=10),  # noqa: B008
    _actor: str = Depends(require_associate_roles),
) -> APIResponse[SyncJob]:
    await _require_active_release(request, body.release_id)
    data = await _services(request).order_jobs.enqueue_full(body, max_attempts)
    return APIResponse(data=data, meta=_meta(request))


@router.post("/order-sync/jobs/claim", response_model=APIResponse[SyncJob])
async def claim_order_sync_job(
    body: JobClaimRequest,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[SyncJob]:
    data = await _services(request).order_jobs.claim(body)
    return APIResponse(data=data, meta=_meta(request))


@router.post("/order-sync/jobs/{job_id}/heartbeat", response_model=APIResponse[SyncJob])
async def heartbeat_order_sync_job(
    job_id: str,
    body: JobClaimRequest,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[SyncJob]:
    try:
        data = await _services(request).order_jobs.heartbeat(job_id, body)
    except (V2NotFoundError, V2ConflictError) as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post("/order-sync/jobs/{job_id}/execute", response_model=APIResponse[dict[str, Any]])
async def execute_order_sync_job(
    job_id: str,
    request: Request,
    worker_id: str = Query(alias="workerId", min_length=1, max_length=200),  # noqa: B008
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[dict[str, Any]]:
    try:
        job, result = await _services(request).order_jobs.execute(job_id, worker_id)
    except (V2NotFoundError, V2ConflictError) as exc:
        raise _translate(exc) from exc
    return APIResponse(
        data={
            "job": job.model_dump(mode="json", by_alias=True),
            "result": result.model_dump(mode="json", by_alias=True) if result else None,
        },
        meta=_meta(request),
    )


@router.get("/order-sync/jobs/{job_id}", response_model=APIResponse[SyncJob])
async def get_order_sync_job(
    job_id: str,
    request: Request,
    _actor: str = Depends(require_associate_roles),
) -> APIResponse[SyncJob]:
    try:
        data = await _services(request).order_jobs.get(job_id)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post("/order-sync/partial", response_model=APIResponse[SyncResult])
async def partial_order_sync(
    body: PartialSyncRequest,
    request: Request,
    _actor: str = Depends(require_associate_roles),
) -> APIResponse[SyncResult]:
    await _require_active_release(request, body.release_id)
    try:
        data = await _services(request).order_sync.partial(body)
    except V2ValidationError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.post("/order-sync/full", response_model=APIResponse[SyncResult])
async def full_order_sync(
    body: FullSyncRequest,
    request: Request,
    _actor: str = Depends(require_associate_roles),
) -> APIResponse[SyncResult]:
    await _require_active_release(request, body.release_id)
    try:
        data = await _services(request).order_sync.full(body)
    except V2ValidationError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))


@router.get("/order-sync/requests/{request_id}", response_model=APIResponse[SyncResult])
async def get_order_sync(
    request_id: str,
    request: Request,
    _actor: str = Depends(require_associate_roles),
) -> APIResponse[SyncResult]:
    try:
        data = await _services(request).order_sync.get(request_id)
    except V2NotFoundError as exc:
        raise _translate(exc) from exc
    return APIResponse(data=data, meta=_meta(request))
