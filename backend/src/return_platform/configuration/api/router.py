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

Reads require read roles and promotion requires write roles, matching the rest of
the platform. Backend authorization is authoritative; the frontend's capability
hook is presentation only.

The one mutation is `POST /releases/{id}/promote`, which drives the single graph
release lifecycle D3 settled on. It delegates for the same reason the reads do.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.configuration.api.audit import AuditLog
from return_platform.configuration.api.audit import get_audit_log as console_get_audit_log
from return_platform.configuration.api.audit import list_audit_logs as console_list_audit_logs
from return_platform.configuration.api.releases import (
    PromoteReleasePayload,
    resolve_configuration_repository,
)
from return_platform.configuration.api.releases import (
    promote_release_status as console_promote_release_status,
)
from return_platform.configuration.api.secrets import redact_secret_values
from return_platform.configuration.api.sources import (
    InventoryDetail,
    SourceDetail,
    SourceItem,
    source_definition,
)
from return_platform.configuration.api.sources import (
    get_inventory_detail as console_get_inventory_detail,
)
from return_platform.configuration.api.sources import get_source as console_get_source
from return_platform.configuration.api.sources import get_sources as console_get_sources
from return_platform.configuration.process_adoption import (
    MongoProcessAdoptionStore,
    evaluate_release_adoption,
)
from return_platform.security import capabilities
from return_platform.security.authorization import (
    require_capability,
    require_read_roles,
    require_write_roles,
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


@router.get("/adoption", response_model=APIResponse[dict[str, Any]])
async def get_release_adoption(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    """`ACTIVATED != LIVE` -- which processes are actually running the release.

    Promoting a release moves the graph pointer and nothing else; the API
    process, the workers and the model the Order Agent calls each adopt on their
    own poll. Until every required process class has reported the activated
    revision the platform is running two releases at once, and this is the
    endpoint that says so rather than leaving an operator to assume otherwise.

    Activated is read from the graph, not from `app.state`: this process is one
    of the adopters, so answering from its own snapshot would make it
    structurally unable to report itself as behind.
    """
    repository = resolve_configuration_repository(request)
    store = getattr(request.app.state, "process_adoption_store", None)
    if not isinstance(store, MongoProcessAdoptionStore):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Process adoption reporting is unavailable",
        )
    active_release = await repository.get_active_release()
    state = evaluate_release_adoption(
        activated_release_id=active_release.release_id if active_release is not None else None,
        activated_head_revision=(
            await repository.get_head_revision() if active_release is not None else None
        ),
        records=await store.list_live(),
    )
    return _ok(request, state.model_dump(mode="json"))


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


# --- promotion ---------------------------------------------------------------
#
# Two configuration release lifecycles used to exist -- a checksum-hardened Mongo
# one that no production path constructed, and the graph one the runtime actually
# reads. Wave D3 resolved it in favour of the graph (see
# `docs/CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md`): the transition table is
# single-sourced as `graph_repository.RELEASE_TRANSITIONS`, the checksum is
# frozen at VALIDATED and verified at RELEASED, and `ReleaseService` /
# `ActivationService` are deleted.
#
# The open question this comment used to hold was scope: which promotions belong
# on a versionless canonical API versus the Data Console's operator surface.
# Answer: all of them. Splitting the lifecycle across two surfaces would mean an
# operator could take a release to VALIDATED on one API and have to change tools
# to publish it, and a caller could not tell from either surface which
# transitions it was allowed to make. One lifecycle, one place to drive it.


@router.post("/releases/{release_id}/promote", response_model=APIResponse[dict[str, Any]])
async def promote_release(
    release_id: str,
    body: PromoteReleasePayload,
    request: Request,
    user_id: str = Depends(require_write_roles),
) -> APIResponse[Any]:
    """Move a release along the lifecycle: DRAFT -> VALIDATED -> RELEASED, or ARCHIVED.

    Delegates, like the reads do. `promote_release_status` is not a thin wrapper
    around the repository -- it validates all three behaviour domains, verifies
    unexpired runtime-validation receipts, requires `expected_head_revision` to
    publish, and refreshes the process's runtime configuration on RELEASED.
    A canonical reimplementation would be a second lifecycle, which is precisely
    what D3 spent its effort deleting.

    **The response is re-scrubbed.** The console handler returns the release's
    full domain payloads, and the canonical `GET /releases/{id}` redacts those on
    the way out. A promote that answered with the unredacted version of the same
    document would make the redaction on the read pointless -- the same operator
    can call both.
    """
    response = await console_promote_release_status(release_id, body, request, user_id)
    return _ok(request, response.data)


# --- sources and audit ------------------------------------------------------
#
# The plan lists six configuration domains for this surface: sources,
# integrations, business config, modules, security and audit. Checked against
# what actually backs each, only two need an endpoint here:
#
#   business config  already served -- `/runtime` returns the whole
#                    `PinnedConfigurationSnapshot`, whose `configuration` field
#                    *is* the business configuration.
#   integrations     already served -- `configuration.integrations` and
#                    `configuration.runtime_integrations`, in the same payload.
#   modules          `_kernel_module_registry` is empty by design ("no module is
#                    registered yet"), so an endpoint would return `[]` forever.
#                    A shell that always answers nothing is worse than an
#                    honest absence; the manifest's module list is reachable
#                    through `/releases/{id}`'s domain payloads.
#   security         not configuration. The role model is code
#                    (`security/roles.py`) and a caller's own grants are on
#                    `/api/principal`. Publishing the whole role-to-capability
#                    table would let a UI reimplement authorization locally,
#                    which the capability layer exists to prevent.
#   sources          real, and below.
#   audit            real, and below.


@router.get("/sources", response_model=APIResponse[list[SourceItem]])
async def list_configured_sources(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    """Configured data sources and their probed health.

    Delegates to the Data Console handler rather than reimplementing the probe
    fan-out: this is a canonical *surface* over one implementation, which is the
    whole point of the exercise. When Wave F deletes the console router, the
    body moves here unchanged.
    """
    return await console_get_sources(request, _user_id)


@router.get("/sources/{source_id}", response_model=APIResponse[SourceDetail])
async def get_configured_source(
    source_id: str,
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    return await console_get_source(source_id, request, _user_id)


@router.get(
    "/sources/{source_id}/assets/{asset_id}",
    response_model=APIResponse[InventoryDetail],
)
async def get_configured_source_asset(
    source_id: str,
    asset_id: str,
    request: Request,
    # `config.source.read`, not `require_read_roles`. This is a new surface, so
    # `require_capability` is the canonical dependency, and the capability maps
    # to the console roles rather than to every principal that may read a return
    # -- which is right for a read that hands back a source asset's whole field
    # list. The two `/sources` reads above are older and still on the role group;
    # narrowing them is a change to a shipped contract and belongs with whoever
    # owns that consolidation.
    _user_id: str = Depends(require_capability(capabilities.CONFIG_SOURCE_READ)),
) -> APIResponse[Any]:
    """One asset's schema: its fields, ownership, and what may be done to it.

    `metadata.fields` is the point -- it is the only place the platform publishes
    what columns or keys an asset actually carries, and until this route existed
    it was reachable only through `/data-console/v1/inventory/{engine}/{asset_id}`,
    which Wave F1 unmounted deliberately and
    `test_no_versioned_data_console_path_is_mounted` keeps unmounted. Delegated
    rather than reimplemented, exactly like the two reads above.

    **Keyed by source, not by engine.** The console navigates from a source to
    its assets, and a source owns a definite set of them; the console handler
    takes an engine because it predates the nesting. Resolving containment here
    means an asset that exists in the registry but does not belong to the named
    source is a 404 rather than a successful read through a path that claims a
    relationship it does not have -- and it makes the engine argument derivable
    from the asset instead of something a caller could get wrong.
    """
    definition = source_definition(request, source_id)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SOURCE_NOT_FOUND", "message": f"No configured source {source_id!r}."},
        )
    asset = next((item for item in definition.assets if item.asset_id == asset_id), None)
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ASSET_NOT_IN_SOURCE",
                "message": f"Source {source_id!r} does not own asset {asset_id!r}.",
            },
        )
    return await console_get_inventory_detail(asset.engine, asset_id, request, _user_id)


@router.get("/audit", response_model=APIResponse[list[AuditLog]])
async def list_configuration_audit(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    """Platform audit records.

    Mounted under `/api/config` because that is where the plan puts it, and
    because the actions it records are overwhelmingly configuration ones --
    promotions, source edits, workspace changes. It is *not* filtered to
    configuration, and the path should not be read as promising that; the
    records carry their own `action` and `target`.
    """
    return await console_list_audit_logs(request, _user_id)


@router.get("/audit/{audit_id}", response_model=APIResponse[AuditLog])
async def get_configuration_audit(
    audit_id: str,
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    return await console_get_audit_log(request, audit_id, _user_id)
