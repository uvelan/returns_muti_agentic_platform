"""Governed deterministic cross-store E2E seed-data APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.operations.models import SeedStatusView
from return_platform.operations.repository import resolve_operational_repository
from return_platform.operations.seed_coordinator import SeedCoordinator
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/seed-data", tags=["Seed Data"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _coordinator(request: Request) -> SeedCoordinator:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(resources, RuntimeResources) or resources.neo4j is None or settings is None:
        raise HTTPException(status_code=503, detail="Seed dependencies are unavailable")
    return SeedCoordinator(
        resolve_operational_repository(request),
        SQLBusinessStateRepository(settings),
        resources.neo4j,
        settings,
    )


@router.get("", response_model=APIResponse[SeedStatusView])
async def seed_status(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[SeedStatusView]:
    return APIResponse(data=await _coordinator(request).status(), meta=_meta(request))


@router.post("/apply", response_model=APIResponse[SeedStatusView])
async def apply_seed(
    request: Request,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[SeedStatusView]:
    return APIResponse(data=await _coordinator(request).apply(actor_id), meta=_meta(request))


@router.post("/reset", response_model=APIResponse[SeedStatusView])
async def reset_seed(
    request: Request,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[SeedStatusView]:
    return APIResponse(data=await _coordinator(request).reset_and_apply(actor_id), meta=_meta(request))
