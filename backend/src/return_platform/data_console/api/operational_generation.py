# ruff: noqa: B008

import typing
from uuid import UUID

from fastapi import APIRouter, Depends

from return_platform.data_platform.operational_generation.apply_models import ExecutionRun
from return_platform.data_platform.operational_generation.approval import ApprovalRecord
from return_platform.data_platform.operational_generation.authorization import (
    OperationalGenerationPermission,
    require_permission,
)
from return_platform.data_platform.operational_generation.models import (
    OperationalGenerationProposal,
)
from return_platform.data_platform.operational_generation.service import (
    OperationalGenerationService,
)
from return_platform.data_platform.operational_generation.write_models import OperationalWritePlan

router = APIRouter(prefix="/api/v1/data-console/ai-studio/operational")


def get_operational_service() -> OperationalGenerationService:
    raise NotImplementedError()


def get_actor_permissions() -> list[str]:
    return [
        "AI_STUDIO_GENERATE",
        "AI_STUDIO_VALIDATE",
        "AI_STUDIO_PLAN",
        "AI_STUDIO_APPROVE",
        "AI_STUDIO_APPLY_OPERATIONAL",
        "AI_STUDIO_ROLLBACK_OPERATIONAL",
        "AI_STUDIO_VIEW_OPERATIONAL",
    ]


def get_actor_id() -> str:
    return "admin123"


@router.post("/proposals", response_model=OperationalGenerationProposal)
async def create_proposal(
    config: dict[str, typing.Any],
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> OperationalGenerationProposal:
    return await service.generate_proposal(config, actor_permissions)


@router.get("/proposals/{proposalId}", response_model=OperationalGenerationProposal)
def get_proposal(
    proposalId: str,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> OperationalGenerationProposal:
    require_permission(actor_permissions, OperationalGenerationPermission.VIEW_OPERATIONAL)
    proposal = service.proposals.get(proposalId)
    if not proposal:
        raise ValueError("Proposal not found")
    return proposal


@router.post("/proposals/{proposalId}/validate")
def validate_proposal_endpoint(
    proposalId: str,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> dict[str, typing.Any]:
    return service.validate_proposal(proposalId, actor_permissions)


@router.post("/proposals/{proposalId}/plan", response_model=OperationalWritePlan)
def plan_proposal_endpoint(
    proposalId: str,
    plan_salt: str,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> OperationalWritePlan:
    return service.plan_proposal(proposalId, plan_salt, actor_permissions)


@router.post("/proposals/{proposalId}/approve", response_model=ApprovalRecord)
def approve_plan_endpoint(
    proposalId: str,
    plan_id: UUID,
    target_environment: str,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_id: str = Depends(get_actor_id),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> ApprovalRecord:
    return service.approve_plan(
        plan_id, proposalId, target_environment, actor_id, actor_permissions
    )


@router.post("/proposals/{proposalId}/apply", response_model=ExecutionRun)
async def apply_plan_endpoint(
    proposalId: str,
    plan_id: UUID,
    approval_id: UUID,
    target_environment: str,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> ExecutionRun:
    return await service.apply_plan(plan_id, approval_id, target_environment, actor_permissions)


@router.get("/runs/{runId}", response_model=ExecutionRun)
def get_run(
    runId: UUID,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> ExecutionRun:
    require_permission(actor_permissions, OperationalGenerationPermission.VIEW_OPERATIONAL)
    run = service.apply_service.repository.get_run(runId)
    if not run:
        raise ValueError("Run not found")
    return run


@router.post("/runs/{runId}/rollback", response_model=ExecutionRun)
async def rollback_run_endpoint(
    runId: UUID,
    plan_id: UUID,
    service: OperationalGenerationService = Depends(get_operational_service),
    actor_permissions: list[str] = Depends(get_actor_permissions),
) -> ExecutionRun:
    return await service.rollback_run(runId, plan_id, actor_permissions)
