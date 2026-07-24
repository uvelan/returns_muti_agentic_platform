"""Support queue and operator command APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.operations.models import (
    ReturnSessionView,
    SupportCaseView,
    SupportOperationRequest,
)
from return_platform.operations.repository import (
    ConcurrencyConflictError,
    resolve_operational_repository,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/support", tags=["Support Operations"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("/returns", response_model=APIResponse[list[ReturnSessionView]])
async def support_returns(
    request: Request,
    return_status: str | None = Query(default=None, alias="status"),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[ReturnSessionView]]:
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=await repository.list_returns(status=return_status), meta=_meta(request)
    )


@router.get("/cases", response_model=APIResponse[list[SupportCaseView]])
async def list_cases(
    request: Request,
    case_status: str | None = Query(default=None, alias="status"),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[SupportCaseView]]:
    repository = resolve_operational_repository(request)
    return APIResponse(data=await repository.list_support_cases(case_status), meta=_meta(request))


@router.get("/cases/{case_id}", response_model=APIResponse[SupportCaseView])
async def get_case(
    request: Request,
    case_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[SupportCaseView]:
    repository = resolve_operational_repository(request)
    data = await repository.get_support_case(case_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Support case not found")
    return APIResponse(data=data, meta=_meta(request))


@router.post("/cases/{case_id}/operations", response_model=APIResponse[SupportCaseView])
async def operate_case(
    request: Request,
    case_id: str,
    payload: SupportOperationRequest,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[SupportCaseView]:
    repository = resolve_operational_repository(request)
    try:
        updated = await repository.operate_support_case(case_id, payload, actor_id=actor_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Support case or return not found") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(status_code=409, detail="Support case version conflict") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=updated, meta=_meta(request))
