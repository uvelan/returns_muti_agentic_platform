"""Published graph-schema releases, and the migration between two of them.

The analyzer can publish a release and the store can make one active. Between
those two acts sat nothing: activation was a pointer flip, and the operator
performing it had no way to ask what it would do to the graph they were already
serving answers from.

This is that surface. `GET .../migration-plan` computes the plan against
whatever is active right now and returns it without writing anything, so
reviewing a change needs read rights and leaves no trace. `POST .../activate`
records the same plan and then flips, so the understanding a decision was made
under outlives the decision.

Deliberately not under `/api/config/releases`, which is the *configuration*
domain's own release surface for a different artifact. Two things called a
release is confusing enough without one router serving both.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.release_migration import MigrationPlan
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.security.authorization import require_admin_roles, require_read_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/schema-releases", tags=["Graph Schema Releases"])


class ReleaseRowView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configurationReleaseId: str
    configurationChecksum: str | None = None
    publishedBy: str | None = None
    publishedAt: str | None = None
    # Whether this is the one the runtime reads. The list is the only place an
    # operator can see published and live side by side, and the distinction is
    # the whole reason publishing does not activate.
    active: bool


class ReleaseListView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    releases: list[ReleaseRowView]
    activeReleaseId: str | None


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _store(request: Request) -> SchemaReleaseStore:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    mongo = getattr(resources, "mongo", None)
    if mongo is None or settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RELEASE_STORE_UNAVAILABLE",
                "message": "Platform MongoDB is unavailable, so releases cannot be read.",
            },
        )
    return SchemaReleaseStore(mongo, settings.mongo_database)


def _iso(value: Any) -> str | None:
    return None if value is None else str(value)


@router.get("", response_model=APIResponse[ReleaseListView])
async def list_releases(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[ReleaseListView]:
    """Every published release, newest first, and which one is live."""
    store = _store(request)
    active = await store.active()
    active_id = None if active is None else active.configuration_release_id
    rows = [
        ReleaseRowView(
            configurationReleaseId=str(row.get("configurationReleaseId", "")),
            configurationChecksum=_iso(row.get("configurationChecksum")),
            publishedBy=_iso(row.get("publishedBy")),
            publishedAt=_iso(row.get("publishedAt")),
            active=row.get("configurationReleaseId") == active_id,
        )
        for row in await store.list_published()
    ]
    return APIResponse(
        data=ReleaseListView(releases=rows, activeReleaseId=active_id), meta=_meta(request)
    )


@router.get("/{release_id}/migration-plan", response_model=APIResponse[MigrationPlan])
async def get_migration_plan(
    release_id: str,
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[MigrationPlan]:
    """What activating this release would do, computed against what is live now.

    A preview, recomputed on every call rather than served from the recorded
    plan: the active pointer moves, and a plan for a pair that is no longer the
    pair you are in is a confidently wrong answer.
    """
    try:
        plan = await _store(request).preview_activation(release_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UNKNOWN_RELEASE", "message": str(exc)},
        ) from exc
    return APIResponse(data=plan, meta=_meta(request))


@router.post("/{release_id}/activate", response_model=APIResponse[MigrationPlan])
async def activate_release(
    release_id: str,
    request: Request,
    # Admin, matching source bindings: this decides which schema every agent
    # turn reasons over, and it is not an act an operator with rights over one
    # return should be able to make.
    _actor_id: str = Depends(require_admin_roles),
) -> APIResponse[MigrationPlan]:
    """Make a release live, and record the migration it commits the graph to.

    Returns the plan rather than an acknowledgement. Whether a rebuild is now
    owed is the consequence of the act, and an operator who has to go and ask
    somewhere else will not.
    """
    try:
        plan = await _store(request).activate(release_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UNKNOWN_RELEASE", "message": str(exc)},
        ) from exc
    return APIResponse(data=plan, meta=_meta(request))
