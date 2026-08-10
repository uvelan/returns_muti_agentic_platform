"""The canonical configuration API: `/api/config`.

Phase 15. Versionless, like `/api/graph-schema` -- the canonical surface is not
versioned in its path because a release, not a URL, is what pins configuration.

**This is a surface, not a second implementation.** Every handler reads through
the same `ConfigurationGraphRepository` the Data Console router uses. Two routers
serving one domain through different code would be two things to keep correct,
and the platform already has one duplicate too many here -- see the note at the
foot of this file. The Data Console router stays until Wave F deletes it.

**Every response is scrubbed.** Configuration documents carry secret
*references*, which an operator needs to see, and must never carry resolved
values. `redact_secret_values` runs on the way out so that is a property of the
response rather than a claim about every path that contributed to it.

Reads require read roles, matching the rest of the platform. Backend
authorization is authoritative; the frontend's capability hook is presentation
only. There is no mutation surface yet -- see the note at the foot of this file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.configuration.api.secrets import redact_secret_values
from return_platform.data_console.api.auth import require_read_roles
from return_platform.data_console.api.configuration import (
    resolve_configuration_repository,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/config", tags=["Configuration"])


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _ok(request: Request, data: Any) -> APIResponse[Any]:
    """One construction point, so the scrub cannot be forgotten on a new handler."""
    return APIResponse(data=redact_secret_values(data), meta=_meta(request))


# --- runtime ----------------------------------------------------------------


@router.get("/runtime", response_model=APIResponse[dict[str, Any]])
async def get_runtime_configuration(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    """The active validated snapshot -- what is serving right now.

    Read from `app.state` rather than rebuilt: the snapshot the process is
    actually using is the honest answer, and rebuilding one here could report a
    configuration nothing is running.
    """
    snapshot = getattr(request.app.state, "return_configuration_snapshot", None)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runtime configuration is not loaded",
        )
    return _ok(request, snapshot.model_dump(mode="json"))


# --- releases ---------------------------------------------------------------


@router.get("/releases", response_model=APIResponse[list[dict[str, Any]]])
async def list_releases(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    repository = resolve_configuration_repository(request)
    releases = await repository.list_releases(limit=limit)
    return _ok(request, [release.model_dump(mode="json") for release in releases])


@router.get("/releases/{release_id}", response_model=APIResponse[dict[str, Any]])
async def get_release(
    release_id: str,
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    repository = resolve_configuration_repository(request)
    release = await repository.get_release(release_id)
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Release {release_id} not found"
        )
    data = release.model_dump(mode="json")
    data["domains"] = await repository.get_all_domain_configs(release_id)
    return _ok(request, data)


# --- no mutation surface yet -------------------------------------------------
#
# The blocker this comment used to describe is gone. There were two configuration
# release lifecycles -- a checksum-hardened Mongo one that no production path
# constructed, and the graph one the runtime actually reads. Wave D3 resolved it
# in favour of the graph (see
# `docs/CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md`): the transition table is
# now single-sourced as `graph_repository.RELEASE_TRANSITIONS`, the checksum is
# frozen at VALIDATED and verified at RELEASED, and `ReleaseService` /
# `ActivationService` are deleted.
#
# So a canonical mutation surface here is now merely unbuilt, not blocked. It
# would promote through `ConfigurationGraphRepository.promote_release`, which is
# the one path that enforces the lifecycle. What it still needs is a decision
# about which promotions belong on a versionless canonical API versus the Data
# Console's operator surface, and that is scope rather than risk.
# Tracked in the ledger as D3's blocking item.
