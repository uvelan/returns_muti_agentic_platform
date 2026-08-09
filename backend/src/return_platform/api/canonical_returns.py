"""The canonical Return Business Copilot API: `/api/returns`.

Phase 16. Versionless, matching `/api/graph-schema` and `/api/config`.

**One session aggregate, one surface.** Today the return domain is served by
nine routers across six prefixes, three of which (`api/returns.py`,
`api/physical_operations.py`, `api/return_artifacts.py`) already share
`/api/v1/returns` — so "which module owns this path" is not answerable from the
path alone. That fragmentation is what this consolidates.

**Reads first, deliberately.** The plan's own instruction is "resolve duplicate
current implementations before deleting anything", and the duplicates are on the
*write* side: two artifact endpoints on one prefix, a production workflow router
and a physical operations router with overlapping stage actions, and an
associate flow that drives the same session by another route. Publishing a
canonical write surface before those are reconciled would add a tenth way to
mutate a return rather than replacing nine. The inventory is in the ledger.

**No generic advance.** There is deliberately no `POST /{session_id}/advance`
here and there never will be: a stage completes because a specific,
evidence-carrying command was applied (`ReturnWorkflowAdvanceCommand`), and an
endpoint that took a target state as a parameter would let a caller move a
return without producing the evidence that justifies the move. A test in
`tests/platform/` enforces this for the canonical surface as well as the legacy
one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.data_console.api.auth import require_read_roles
from return_platform.operations.models import ReturnSessionView, TimelineEvent
from return_platform.operations.repository import resolve_operational_repository
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/returns", tags=["Returns"])


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


async def _require_session(request: Request, session_id: str) -> Any:
    """404 before doing anything else.

    Sub-resources check the parent explicitly rather than returning an empty
    list for a session that does not exist -- an empty timeline and a
    nonexistent return are different answers, and a UI cannot tell them apart
    from `[]`.
    """
    repository = resolve_operational_repository(request)
    session = await repository.get_return(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Return session not found"
        )
    return session


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
    return APIResponse(data=await _require_session(request, session_id), meta=_meta(request))


@router.get("/{session_id}/timeline", response_model=APIResponse[list[TimelineEvent]])
async def get_timeline(
    request: Request,
    session_id: str,
    after_sequence: int = Query(default=0, alias="after", ge=0),
    limit: int = Query(default=1_000, ge=1, le=10_000),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[TimelineEvent]]:
    """Named `timeline`, not `events`.

    The legacy path is `/events`, but the aggregate this belongs to calls it a
    timeline and so does the plan's domain list. The canonical name should match
    the domain rather than inherit an implementation word; the legacy path keeps
    working until Wave F.
    """
    await _require_session(request, session_id)
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=await repository.list_events(session_id, after_sequence=after_sequence, limit=limit),
        meta=_meta(request),
    )
