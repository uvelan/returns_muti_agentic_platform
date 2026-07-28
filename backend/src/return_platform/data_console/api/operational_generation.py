# ruff: noqa: B008

import typing
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from return_platform.data_console.api.auth import require_admin_roles
from return_platform.data_platform.ai_studio import AIStudioGenerationRequest
from return_platform.data_platform.operational_generation.apply_models import ExecutionRun
from return_platform.data_platform.operational_generation.apply_service import ApplyService
from return_platform.data_platform.operational_generation.approval import ApprovalRecord
from return_platform.data_platform.operational_generation.authorization import (
    OperationalGenerationPermission,
    require_permission,
)
from return_platform.data_platform.operational_generation.execution_lock import ExecutionLock
from return_platform.data_platform.operational_generation.execution_repository import (
    ExecutionRepository,
)
from return_platform.data_platform.operational_generation.generator import OperationalGenerator
from return_platform.data_platform.operational_generation.guard import HallucinationGuard
from return_platform.data_platform.operational_generation.models import (
    OperationalGenerationProposal,
)
from return_platform.data_platform.operational_generation.planner import OperationalPlanner
from return_platform.data_platform.operational_generation.rollback_service import RollbackService
from return_platform.data_platform.operational_generation.service import (
    OperationalGenerationService,
)
from return_platform.data_platform.operational_generation.write_models import OperationalWritePlan
from return_platform.data_platform.schema_registry import SchemaRegistry
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/data-console/ai-studio/operational")


def _build_service_from_state(request: Request) -> OperationalGenerationService:
    """Build the service once per application instance."""
    existing = getattr(request.app.state, "operational_generation_service", None)
    if isinstance(existing, OperationalGenerationService):
        return existing

    resources = getattr(request.app.state, "resources", None)
    registry = getattr(resources, "schema_registry", None)
    if not isinstance(registry, SchemaRegistry):
        raise HTTPException(status_code=503, detail="Schema registry is unavailable.")

    guard = HallucinationGuard(registry)
    repository = ExecutionRepository()
    service = OperationalGenerationService(
        generator=OperationalGenerator(registry, guard),
        planner=OperationalPlanner(registry, guard),
        apply_service=ApplyService(repository, ExecutionLock()),
        rollback_service=RollbackService(repository),
        registry=registry,
    )
    request.app.state.operational_generation_service = service
    return service


def get_operational_service(request: Request) -> OperationalGenerationService:
    return _build_service_from_state(request)


def get_actor_permissions(request: Request) -> list[str]:
    require_admin_roles(request)
    return [
        "AI_STUDIO_GENERATE",
        "AI_STUDIO_VALIDATE",
        "AI_STUDIO_PLAN",
        "AI_STUDIO_APPROVE",
        "AI_STUDIO_APPLY_OPERATIONAL",
        "AI_STUDIO_ROLLBACK_OPERATIONAL",
        "AI_STUDIO_VIEW_OPERATIONAL",
    ]


def get_actor_id(request: Request) -> str:
    return require_admin_roles(request)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.post(
    "/proposals",
    response_model=APIResponse[OperationalGenerationProposal],
    status_code=201,
)
async def create_proposal(
    body: AIStudioGenerationRequest,
    request: Request,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> APIResponse[OperationalGenerationProposal]:
    config: dict[str, typing.Any] = {
        "asset_ids": body.assetIds,
        "record_count": body.recordsPerAsset,
        "deterministic_seed": body.seed,
        "tenant_id": "default",
    }
    try:
        proposal = await service.generate_proposal(config, actor_permissions)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=proposal, meta=_meta(request))


@router.get(
    "/proposals/{proposal_id}",
    response_model=APIResponse[OperationalGenerationProposal],
)
def get_proposal(
    proposal_id: str,
    request: Request,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> APIResponse[OperationalGenerationProposal]:
    require_permission(actor_permissions, OperationalGenerationPermission.VIEW_OPERATIONAL)
    proposal = service.proposals.get(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return APIResponse(data=proposal, meta=_meta(request))


@router.post(
    "/proposals/{proposal_id}/validate",
    response_model=APIResponse[dict[str, typing.Any]],
)
def validate_proposal_endpoint(
    proposal_id: str,
    request: Request,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> APIResponse[dict[str, typing.Any]]:
    try:
        result = service.validate_proposal(proposal_id, actor_permissions)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post(
    "/proposals/{proposal_id}/plan",
    response_model=APIResponse[OperationalWritePlan],
)
def plan_proposal_endpoint(
    proposal_id: str,
    plan_salt: str,
    request: Request,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> APIResponse[OperationalWritePlan]:
    try:
        plan = service.plan_proposal(proposal_id, plan_salt, actor_permissions)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return APIResponse(data=plan, meta=_meta(request))


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=APIResponse[ApprovalRecord],
)
def approve_plan_endpoint(
    proposal_id: str,
    plan_id: UUID,
    target_environment: str,
    request: Request,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_id: str = Depends(get_actor_id),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> APIResponse[ApprovalRecord]:
    try:
        approval = service.approve_plan(
            plan_id, proposal_id, target_environment, actor_id, actor_permissions
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return APIResponse(data=approval, meta=_meta(request))


@router.post(
    "/proposals/{proposal_id}/apply",
    response_model=APIResponse[ExecutionRun],
)
async def apply_plan_endpoint(
    proposal_id: str,
    plan_id: UUID,
    approval_id: UUID,
    target_environment: str,
    request: Request,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> APIResponse[ExecutionRun]:
    if proposal_id not in service.proposals:
        raise HTTPException(status_code=404, detail="Proposal not found")
    try:
        run = await service.apply_plan(plan_id, approval_id, target_environment, actor_permissions)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=run, meta=_meta(request))


@router.get("/runs/{run_id}", response_model=APIResponse[ExecutionRun])
def get_run(
    run_id: UUID,
    request: Request,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> APIResponse[ExecutionRun]:
    require_permission(actor_permissions, OperationalGenerationPermission.VIEW_OPERATIONAL)
    run = service.apply_service.repository.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return APIResponse(data=run, meta=_meta(request))


@router.post("/runs/{run_id}/rollback", response_model=APIResponse[ExecutionRun])
async def rollback_run_endpoint(
    run_id: UUID,
    plan_id: UUID,
    request: Request,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> APIResponse[ExecutionRun]:
    try:
        run = await service.rollback_run(run_id, plan_id, actor_permissions)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=run, meta=_meta(request))
