"""Governed deterministic cross-store E2E seed-data APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.operations.models import (
    SeedApplyRequest,
    SeedDeleteRequest,
    SeedOperationStatus,
    SeedOperationView,
    SeedStatusView,
)
from return_platform.operations.repository import resolve_operational_repository
from return_platform.operations.seed_control import SeedOperationCancelled, SeedOperationControl
from return_platform.operations.seed_coordinator import SeedCoordinator
from return_platform.operations.seed_manifest import effective_seed_counts
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/seed-data", tags=["Seed Data"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _coordinator(request: Request) -> SeedCoordinator:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.neo4j is None
        or resources.schema_registry is None
        or settings is None
    ):
        raise HTTPException(status_code=503, detail="Seed dependencies are unavailable")
    return SeedCoordinator(
        resolve_operational_repository(request),
        SQLBusinessStateRepository(settings),
        resources.neo4j,
        settings,
        resources.schema_registry,
    )


def _control(request: Request) -> SeedOperationControl:
    value = getattr(request.app.state, "seed_operation_control", None)
    if isinstance(value, SeedOperationControl):
        return value
    value = SeedOperationControl()
    request.app.state.seed_operation_control = value
    return value


def _record_limit(payload: SeedApplyRequest | None) -> int:
    if payload is not None:
        return payload.recordLimit
    return effective_seed_counts()["orders"]


@router.get("", response_model=APIResponse[SeedStatusView])
async def seed_status(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[SeedStatusView]:
    return APIResponse(data=await _coordinator(request).status(), meta=_meta(request))


@router.get("/operation", response_model=APIResponse[SeedOperationView])
async def seed_operation(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[SeedOperationView]:
    return APIResponse(data=await _control(request).snapshot(), meta=_meta(request))


@router.post("/apply", response_model=APIResponse[SeedStatusView])
async def apply_seed(
    request: Request,
    payload: SeedApplyRequest | None = None,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[SeedStatusView]:
    record_limit = _record_limit(payload)
    counts = effective_seed_counts(record_limit)
    control = _control(request)
    try:
        operation_id = await control.begin(
            kind="APPLY",
            record_limit=record_limit,
            total_records=(
                (2 * counts["customers"]) + (2 * counts["products"]) + (3 * counts["orders"])
            ),
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    try:
        result = await _coordinator(request).apply(
            actor_id,
            record_limit=record_limit,
            control=control,
            operation_id=operation_id,
        )
    except SeedOperationCancelled:
        await control.finish(
            operation_id,
            SeedOperationStatus.CANCELLED,
            phase="Stopped by user",
        )
        result = await _coordinator(request).status()
    except Exception as error:
        await control.finish(
            operation_id,
            SeedOperationStatus.FAILED,
            phase="Seed operation failed",
            error=type(error).__name__,
        )
        raise
    else:
        await control.finish(
            operation_id,
            SeedOperationStatus.COMPLETED,
            phase="Seed data applied",
        )
    return APIResponse(data=result, meta=_meta(request))


@router.post("/cancel", response_model=APIResponse[SeedOperationView])
async def cancel_seed(
    request: Request,
    _actor_id: str = Depends(require_write_roles),
) -> APIResponse[SeedOperationView]:
    return APIResponse(data=await _control(request).request_cancel(), meta=_meta(request))


@router.post("/delete", response_model=APIResponse[SeedStatusView])
async def delete_seed(
    payload: SeedDeleteRequest,
    request: Request,
    _actor_id: str = Depends(require_write_roles),
) -> APIResponse[SeedStatusView]:
    del payload
    coordinator = _coordinator(request)
    current = await coordinator.status()
    control = _control(request)
    try:
        operation_id = await control.begin(
            kind="DELETE",
            record_limit=current.requestedRecordLimit,
            total_records=0,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    try:
        result = await coordinator.delete_all(
            record_limit=current.requestedRecordLimit,
            control=control,
            operation_id=operation_id,
        )
    except SeedOperationCancelled:
        await control.finish(
            operation_id,
            SeedOperationStatus.CANCELLED,
            phase="Stopped by user",
        )
        result = await coordinator.status()
    except Exception as error:
        await control.finish(
            operation_id,
            SeedOperationStatus.FAILED,
            phase="Seed deletion failed",
            error=type(error).__name__,
        )
        raise
    await control.finish(
        operation_id,
        SeedOperationStatus.COMPLETED,
        phase="All seed data deleted",
    )
    return APIResponse(data=result, meta=_meta(request))


@router.post("/reset", response_model=APIResponse[SeedStatusView])
async def reset_seed(
    request: Request,
    payload: SeedApplyRequest | None = None,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[SeedStatusView]:
    record_limit = _record_limit(payload)
    counts = effective_seed_counts(record_limit)
    control = _control(request)
    try:
        operation_id = await control.begin(
            kind="RESET",
            record_limit=record_limit,
            total_records=(
                (2 * counts["customers"]) + (2 * counts["products"]) + (3 * counts["orders"])
            ),
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    try:
        result = await _coordinator(request).reset_and_apply(
            actor_id,
            record_limit=record_limit,
            control=control,
            operation_id=operation_id,
        )
    except SeedOperationCancelled:
        await control.finish(
            operation_id,
            SeedOperationStatus.CANCELLED,
            phase="Stopped by user",
        )
        result = await _coordinator(request).status()
    except Exception as error:
        await control.finish(
            operation_id,
            SeedOperationStatus.FAILED,
            phase="Seed reset failed",
            error=type(error).__name__,
        )
        raise
    else:
        await control.finish(
            operation_id,
            SeedOperationStatus.COMPLETED,
            phase="Seed data reset and applied",
        )
    return APIResponse(data=result, meta=_meta(request))
