"""The graph's source synchronization, as an operator can see and steer it.

`GraphSyncService` has recorded every run it has ever performed into
`graph_sync_runs` since it was written, and exposed `list_runs`/`get_run` to
nobody: the Data Console routers that once served them were unmounted in Wave F1
along with the frontend that called them, and nothing replaced the surface. The
service was reachable only from inside the process -- the operational-generation
adapter and the seed coordinator -- so "did the graph sync, and what did it
write" was a question answerable by reading Mongo by hand.

Two kinds of run share that ledger now, and this serves both. A scheduled run
covers every participating source; a targeted one covers a single record an
agent asked for mid-conversation, and carries the conversation and turn that
asked (`requestedBy`). Keeping them in one list is the point: an operator
looking at an unexpected node in the graph should not have to know which of two
mechanisms could have written it.

Versionless by design (`/api/graph-sync`, not `/api/v1/...`) -- see
`frontend/src/api/noVersionedPaths.test.ts`.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncRunView,
    GraphSyncService,
)
from return_platform.security import capabilities
from return_platform.security.authorization import require_capability
from return_platform.shared.contracts import APIResponse, ResponseMeta

logger = logging.getLogger("return_platform.api.graph_sync")

router = APIRouter(prefix="/api/graph-sync", tags=["Graph Sync"])

#: What `list_runs` will filter on. Not a free-form string: `mode` is written by
#: two producers with fixed vocabularies (`GraphSyncScope` and the targeted
#: ledger's `ON_DEMAND`), and an unconstrained filter would silently return an
#: empty list for a typo.
RUN_MODES = ("FULL", "SOURCE_MONGODB", "SQLSERVER", "ON_DEMAND")


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _service(request: Request) -> GraphSyncService:
    """The one `GraphSyncService` the lifespan built, or a plain 503.

    Read from `app.state` rather than from the operational-generation adapter's
    module global, which is set from the same object: a router reaching into
    another subsystem's private global to find its own dependency is how two
    instances end up existing.
    """
    service = getattr(request.app.state, "graph_sync", None)
    if not isinstance(service, GraphSyncService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "GRAPH_SYNC_UNAVAILABLE",
                "message": (
                    "Graph synchronization needs the platform database, the source "
                    "database and Neo4j, and at least one is not connected."
                ),
            },
        )
    return service


@router.get("/runs", response_model=APIResponse[list[GraphSyncRunView]])
async def list_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    mode: str | None = Query(default=None),
    _actor_id: str = Depends(require_capability(capabilities.CONFIG_SOURCE_READ)),
) -> APIResponse[list[GraphSyncRunView]]:
    """Sync runs, newest first: scheduled and agent-initiated in one history."""
    if mode is not None and mode not in RUN_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "UNKNOWN_SYNC_MODE",
                "message": f"mode must be one of {list(RUN_MODES)}",
            },
        )
    runs = await _service(request).list_runs(limit, mode=mode)
    return APIResponse(data=runs, meta=_meta(request))


@router.get("/runs/{run_id}", response_model=APIResponse[GraphSyncRunView])
async def read_run(
    run_id: str,
    request: Request,
    _actor_id: str = Depends(require_capability(capabilities.CONFIG_SOURCE_READ)),
) -> APIResponse[GraphSyncRunView]:
    run = await _service(request).get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SYNC_RUN_NOT_FOUND", "message": f"No sync run {run_id!r}."},
        )
    return APIResponse(data=run, meta=_meta(request))


@router.post(
    "/runs",
    response_model=APIResponse[GraphSyncRunView],
    responses={
        status.HTTP_409_CONFLICT: {
            "description": (
                "A graph sync is already running and still reporting. Declared "
                "rather than merely raised: a client cannot handle a status the "
                "contract does not mention."
            )
        }
    },
)
async def start_run(
    payload: GraphSyncRequest,
    request: Request,
    # The capability, not a role group -- this is a new surface and
    # `require_capability` is the canonical dependency for one, so the console's
    # gate and the server's are literally the same string rather than two
    # vocabularies that have to be kept in agreement by hand.
    #
    # `config.source.write` rather than a read: a resync re-reads production
    # sources and rewrites the graph the copilot answers from. It is deliberately
    # *not* the admin gate that `/api/source-bindings` uses for a rebind, because
    # rebinding decides where the platform reads from and this only re-reads
    # where it already reads.
    actor_id: str = Depends(require_capability(capabilities.CONFIG_SOURCE_WRITE)),
) -> APIResponse[GraphSyncRunView]:
    """Run a sync now and answer with the run it produced.

    Awaited rather than backgrounded, matching `/api/v1/seed-data/apply`: the
    request is bounded by `maxRecordsPerAsset` and by
    `settings.graph_sync_max_records`, and a fire-and-forget task would have to
    invent a way to hand back the run id that `GraphSyncService.sync` mints
    internally. A failed run is still recorded FAILED in the ledger before the
    error propagates, so the list is truthful either way.
    """
    service = _service(request)

    # One rebuild at a time, and "at a time" means beating rather than merely
    # marked RUNNING. The handler had no concurrency guard at all, so a second
    # "Sync now" started a concurrent full rebuild against a graph already being
    # rebuilt -- and the screen showed a fifteen-hour-old RUNNING row as the
    # latest run, which is exactly the state an operator would press it in.
    #
    # A stale row is deliberately not a reason to refuse: treating one as active
    # would have made the graph unrebuildable until somebody edited Mongo by
    # hand, which is a worse failure than the one being prevented.
    active = await service.active_run()
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "GRAPH_SYNC_ALREADY_RUNNING",
                "message": "A graph sync is already running.",
                "runId": str(active.get("_id") or ""),
            },
        )

    try:
        return APIResponse(data=await service.sync(payload, actor_id=actor_id), meta=_meta(request))
    except Exception as error:
        logger.exception(
            "graph_sync_run_failed",
            extra={"mode": payload.mode.value, "actor_id": actor_id},
        )
        detail: dict[str, Any] = {
            "code": "GRAPH_SYNC_FAILED",
            # The type, not the message: a connector error can carry a DSN.
            "message": f"The sync did not complete ({type(error).__name__}).",
        }
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from error
