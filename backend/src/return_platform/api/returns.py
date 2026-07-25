"""Customer-facing return APIs and resumable event stream."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.operations.events import event_stream
from return_platform.operations.models import ReturnCreateRequest, ReturnSessionView, TimelineEvent
from return_platform.operations.repository import (
    ConcurrencyConflictError,
    resolve_operational_repository,
)
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/returns", tags=["Returns"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=APIResponse[ReturnSessionView])
async def create_return(
    request: Request,
    payload: ReturnCreateRequest,
    actor_id: str = Depends(require_write_roles),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> APIResponse[ReturnSessionView]:
    if payload.channel != "SYSTEM":
        raise HTTPException(
            status_code=409,
            detail=(
                "Interactive returns must start through /api/v1/associate-returns/conversations."
            ),
        )
    if idempotency_key is not None:
        payload = payload.model_copy(update={"idempotencyKey": idempotency_key})
    repository = resolve_operational_repository(request)
    data = await repository.create_return(
        payload,
        correlation_id=_meta(request).request_id,
        actor_id=actor_id,
    )
    return APIResponse(data=data, meta=_meta(request))


@router.get("", response_model=APIResponse[list[ReturnSessionView]])
async def list_returns(
    request: Request,
    return_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[ReturnSessionView]]:
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=await repository.list_returns(status=return_status, limit=limit),
        meta=_meta(request),
    )


@router.get("/{session_id}", response_model=APIResponse[ReturnSessionView])
async def get_return(
    request: Request,
    session_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[ReturnSessionView]:
    repository = resolve_operational_repository(request)
    data = await repository.get_return(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Return session not found")
    return APIResponse(data=data, meta=_meta(request))


@router.get("/{session_id}/events", response_model=APIResponse[list[TimelineEvent]])
async def list_return_events(
    request: Request,
    session_id: str,
    after_sequence: int = Query(default=0, alias="after", ge=0),
    limit: int = Query(default=1_000, ge=1, le=10_000),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[TimelineEvent]]:
    repository = resolve_operational_repository(request)
    if await repository.get_return(session_id) is None:
        raise HTTPException(status_code=404, detail="Return session not found")
    return APIResponse(
        data=await repository.list_events(session_id, after_sequence=after_sequence, limit=limit),
        meta=_meta(request),
    )


@router.get("/{session_id}/stream")
async def stream_return_events(
    request: Request,
    session_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    _actor_id: str = Depends(require_read_roles),
) -> StreamingResponse:
    repository = resolve_operational_repository(request)
    if await repository.get_return(session_id) is None:
        raise HTTPException(status_code=404, detail="Return session not found")
    try:
        after_sequence = int(last_event_id) if last_event_id else 0
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from error
    resources = cast(RuntimeResources, request.app.state.resources)
    settings = request.app.state.settings
    return StreamingResponse(
        event_stream(
            request,
            repository,
            resources.valkey,
            stream_id=session_id,
            after_sequence=after_sequence,
            replay_limit=settings.sse_replay_limit,
            heartbeat_seconds=settings.sse_heartbeat_seconds,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{session_id}/cancel", response_model=APIResponse[ReturnSessionView])
async def cancel_return(
    request: Request,
    session_id: str,
    expected_version: int = Query(alias="expectedVersion", ge=0),
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[ReturnSessionView]:
    repository = resolve_operational_repository(request)
    try:
        data = await repository.update_return(
            session_id,
            {"status": "CANCELLED", "orchestrationState": "CANCELLED"},
            expected_version=expected_version,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Return session not found") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(status_code=409, detail="Return session version conflict") from error
    await repository.release_discovery_lock(session_id, reason="CANCELLED")
    await repository.append_event(
        session_id,
        event_type="RETURN_CANCELLED",
        actor_type="USER",
        actor_id=actor_id,
        payload={},
    )
    return APIResponse(data=data, meta=_meta(request))
