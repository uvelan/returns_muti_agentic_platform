"""Operations APIs for versioned graph-backed runtime configuration."""

from __future__ import annotations

import copy
from typing import Any, Final, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.ai.routing.tasks import AIGatewayConfiguration, LoadedAIGatewayConfiguration
from return_platform.configuration.application.release_promotion import (
    ReleasePromotionError,
    promote_configuration_release,
)
from return_platform.configuration.graph_repository import (
    ConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
    ConfigurationSnapshotBuilder,
)
from return_platform.dependency_simulation.configuration import (
    DependencySimulationConfiguration,
    LoadedDependencySimulationConfiguration,
)
from return_platform.operations.repository import resolve_operational_repository
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_read_roles, require_write_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/data-console/v1/configuration", tags=["Graph Configuration"])
_SOURCE: Final = "GRAPH_CONFIGURATION"


def resolve_configuration_repository(request: Request) -> ConfigurationGraphRepository:
    repo = getattr(request.app.state, "graph_configuration_repository", None)
    if not isinstance(repo, ConfigurationGraphRepository):
        raise HTTPException(status_code=503, detail="Graph configuration repository is unavailable")
    return repo


def _response_meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


class ConfigurationReleaseView(BaseModel):
    """One configuration release, as the console reads it.

    Typed because it was not, and the cost of that was invisible. The route
    answered `list[dict[str, Any]]`, so the OpenAPI drift gate had no shape to
    compare and reported `diffs: []` while the console read three fields the
    endpoint has never served -- `approved_by`, `activated_at` and a `checksum`
    that is spelled `checksum_sha256`. A gate that cannot fail on an untyped
    endpoint reports coverage it does not have.

    **Every field here is persisted.** `ConfigurationReleaseNode`
    (`configuration/graph_repository.py:86-94`) holds exactly six, and this is
    those six. The console previously declared eleven: `updated_at`,
    `validated_at`, `approved_at`, `approved_by`, `activated_at` and
    `superseded_by` have no writer anywhere and rendered as permanent dashes on
    the one screen that answers "who released this, and when". They are gone
    rather than nulled, because a column that can never be filled is worse than
    an absent one -- it reads as missing data instead of an absent feature.
    Adding one back means persisting it first.

    **Not `ReleaseRowView`.** That name belongs to `api/schema_releases.py:34`
    and describes a *graph-schema* release, which is a different artifact with a
    different lifecycle. Reusing it here would merge two contracts that only
    look alike.

    camelCase on the wire, matching every other endpoint. This route was the one
    audited surface answering snake_case, which is how a console reading
    `caseId` and `slaDueAt` everywhere else came to read `release_id` here.
    """

    # `validation_alias`, not `alias`. FastAPI serializes response models with
    # `by_alias=True`, so a plain `alias` would have put snake_case back on the
    # wire while this model read camelCase -- the same client/server disagreement
    # this type exists to end. `validation_alias` accepts the persisted
    # snake_case in, and serialization falls back to the field name out.
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    releaseId: str = Field(validation_alias="release_id")
    status: str
    createdAt: str = Field(validation_alias="created_at")
    #: Served since the endpoint existed and never rendered. The governance
    #: question the screen exists to answer is "who released this and when", and
    #: both halves were on the wire the whole time.
    createdBy: str = Field(validation_alias="created_by")
    checksumSha256: str = Field(validation_alias="checksum_sha256")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfigurationReleaseDetailView(ConfigurationReleaseView):
    """A release plus the domain payloads it carries."""

    domains: dict[str, Any] = Field(default_factory=dict)


@router.get("/active-snapshot", response_model=APIResponse[dict[str, Any]])
async def get_active_snapshot(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[dict[str, Any]]:
    """Return the active validated runtime configuration snapshot."""

    snapshot = getattr(request.app.state, "return_configuration_snapshot", None)
    if snapshot is not None:
        return APIResponse(data=snapshot.model_dump(mode="json"), meta=_response_meta(request))

    repo = resolve_configuration_repository(request)
    default_config = getattr(request.app.state, "return_configuration", None)
    if not default_config:
        raise HTTPException(status_code=503, detail="Runtime configuration is not loaded")
    resources = getattr(request.app.state, "resources", None)
    environment = (
        resources.settings.environment if isinstance(resources, RuntimeResources) else "production"
    )
    default_ai_gateway = getattr(request.app.state, "ai_gateway_configuration", None)
    default_dependency_simulation = getattr(
        request.app.state,
        "dependency_simulation_configuration",
        None,
    )
    built = await ConfigurationSnapshotBuilder(repo).build_snapshot(
        default_config.configuration,
        allow_baseline_fallback=(environment in {"development", "test"}),
        default_ai_gateway_configuration=(
            default_ai_gateway.configuration
            if isinstance(default_ai_gateway, LoadedAIGatewayConfiguration)
            else None
        ),
        default_dependency_simulation_configuration=(
            default_dependency_simulation.configuration
            if isinstance(
                default_dependency_simulation,
                LoadedDependencySimulationConfiguration,
            )
            else None
        ),
        require_all_behavior_domains=(environment not in {"development", "test"}),
    )
    return APIResponse(data=built.model_dump(mode="json"), meta=_response_meta(request))


@router.get("/releases", response_model=APIResponse[list[dict[str, Any]]])
async def list_releases(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[list[dict[str, Any]]]:
    repo = resolve_configuration_repository(request)
    releases = await repo.list_releases(limit=limit)
    return APIResponse(
        data=[release.model_dump(mode="json") for release in releases],
        meta=_response_meta(request),
    )


@router.get("/releases/{release_id}", response_model=APIResponse[dict[str, Any]])
async def get_release_detail(
    release_id: str,
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[dict[str, Any]]:
    repo = resolve_configuration_repository(request)
    release = await repo.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail=f"Release {release_id} not found")
    data = release.model_dump(mode="json")
    data["domains"] = await repo.get_all_domain_configs(release_id)
    return APIResponse(data=data, meta=_response_meta(request))


class CreateReleasePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$")
    from_active: bool = True


@router.post(
    "/releases",
    response_model=APIResponse[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
async def create_release(
    payload: CreateReleasePayload,
    request: Request,
    user_id: str = Depends(require_write_roles),
) -> APIResponse[dict[str, Any]]:
    """Create a draft by cloning the active release or current validated baseline."""

    repo = resolve_configuration_repository(request)
    if await repo.get_release(payload.release_id) is not None:
        raise HTTPException(status_code=409, detail=f"Release {payload.release_id} already exists")

    domains_to_copy: dict[str, Any] = {}
    active_release = await repo.get_active_release()
    if active_release is not None and payload.from_active:
        domains_to_copy = await repo.get_all_domain_configs(active_release.release_id)

    loaded = getattr(request.app.state, "return_configuration", None)
    if loaded is None:
        raise HTTPException(status_code=503, detail="Runtime configuration is unavailable")
    domains_to_copy.setdefault(
        RETURN_PLATFORM_DOMAIN_KEY,
        loaded.configuration.model_dump(mode="json"),
    )
    loaded_ai_gateway = getattr(request.app.state, "ai_gateway_configuration", None)
    if isinstance(loaded_ai_gateway, LoadedAIGatewayConfiguration):
        domains_to_copy.setdefault(
            AI_GATEWAY_DOMAIN_KEY,
            loaded_ai_gateway.configuration.model_dump(mode="json"),
        )
    loaded_dependency_simulation = getattr(
        request.app.state,
        "dependency_simulation_configuration",
        None,
    )
    if isinstance(
        loaded_dependency_simulation,
        LoadedDependencySimulationConfiguration,
    ):
        domains_to_copy.setdefault(
            DEPENDENCY_SIMULATION_DOMAIN_KEY,
            loaded_dependency_simulation.configuration.model_dump(mode="json"),
        )

    for domain_key, domain_payload in domains_to_copy.items():
        await repo.save_draft_domain(
            payload.release_id,
            domain_key,
            cast(dict[str, Any], domain_payload),
            actor_id=user_id,
        )

    # The packaged baseline the cloned release was built from, carried onto the
    # clone -- the same carry `publish_release_with_domains` does for a governed
    # change, and it was missing here, on the path every Configuration screen
    # publishes through. `bootstrap_graph_configuration` reads the baseline to
    # tell an operator's edit apart from a change to the packaged file; a
    # release without one is undecidable for every key, so each publish from
    # the UI put the deployment back at "the release wins, the file's changes
    # are logged and dropped". Observed 2026-09-11: a task edit published from
    # the AI Control Center produced a RELEASED release with empty metadata one
    # start after the bootstrap had recorded a baseline.
    #
    # The operator's edits need no special treatment: they move their keys away
    # from the baseline, which is exactly how the next bootstrap reads them as
    # edited and leaves them alone.
    if active_release is not None and payload.from_active and active_release.metadata:
        await repo.set_release_metadata(payload.release_id, dict(active_release.metadata))

    release = await repo.get_release(payload.release_id)
    if release is None:
        raise HTTPException(status_code=500, detail="Configuration release creation failed")
    await record_configuration_audit(
        request,
        action="CONFIGURATION_RELEASE_CREATED",
        actor=user_id,
        target=payload.release_id,
        details={
            "clonedFrom": active_release.release_id
            if active_release is not None and payload.from_active
            else None,
            "domains": sorted(domains_to_copy),
        },
    )
    data = release.model_dump(mode="json")
    data["domains"] = await repo.get_all_domain_configs(payload.release_id)
    return APIResponse(data=data, meta=_response_meta(request))


async def record_configuration_audit(
    request: Request,
    *,
    action: str,
    actor: str,
    target: str,
    details: dict[str, Any],
) -> None:
    """One audit record per configuration release change, in the platform's audit log.

    Release create, domain patch and promotion left no entry in the `audit`
    collection that `GET /api/config/audit` serves: the only trail was the
    release's `created_by` and each domain's `updated_by`, overwritten on every
    edit, with no before/after. An operator asking who changed a threshold and
    what it was before had no answer. This is the same `append_audit` the AI
    gateway and governance kernel already write through -- one log, not a
    second one.
    """
    repository = resolve_operational_repository(request)
    await repository.append_audit(action=action, actor=actor, target=target, details=details)


def _changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Dotted paths whose value differs between two JSON documents, capped."""
    if isinstance(before, dict) and isinstance(after, dict):
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                paths.append(child)
            else:
                paths.extend(_changed_paths(before[key], after[key], child))
            if len(paths) >= 50:
                return paths[:50]
        return paths
    return [] if before == after else [prefix or "$"]


def _canonical_domain_payload(domain_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a domain payload and return the form the platform persists.

    What is stored is `model_dump(mode="json")` of the validated model, never
    the dictionary the caller sent. The bootstrap decides whether anything
    changed by comparing the active release's stored payload with its own
    `model_dump` of the packaged file (`cli/bootstrap_graph_configuration.py`),
    so a payload saved in any other shape -- a merge patch that left a defaulted
    key out, a list where the model holds a tuple, keys in another order -- read
    as a change on every start and republished the release with a new head
    revision each time (finding F-0084). One shape on the way in, and the
    comparison means what it says.

    A domain key outside the three the platform reads is refused rather than
    stored unvalidated: nothing would ever read it, but it would ride along in
    every clone of the release and count in its checksum.
    """
    try:
        if domain_key == RETURN_PLATFORM_DOMAIN_KEY:
            return ReturnPlatformConfiguration.model_validate(payload).model_dump(mode="json")
        if domain_key == AI_GATEWAY_DOMAIN_KEY:
            return AIGatewayConfiguration.model_validate(payload).model_dump(mode="json")
        if domain_key == DEPENDENCY_SIMULATION_DOMAIN_KEY:
            return DependencySimulationConfiguration.model_validate(payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(
        status_code=404,
        detail=(
            f"Domain {domain_key} is not a configuration domain; expected one of "
            f"{RETURN_PLATFORM_DOMAIN_KEY}, {AI_GATEWAY_DOMAIN_KEY}, "
            f"{DEPENDENCY_SIMULATION_DOMAIN_KEY}"
        ),
    )


class SaveDomainPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


class PatchDomainPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: dict[str, Any]


def _apply_merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply an RFC 7396-style object merge patch to a configuration document."""

    merged = copy.deepcopy(target)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict):
            current = merged.get(key)
            merged[key] = _apply_merge_patch(
                current if isinstance(current, dict) else {},
                value,
            )
        else:
            merged[key] = copy.deepcopy(value)
    return merged


@router.put(
    "/releases/{release_id}/domains/{domain_key}",
    response_model=APIResponse[dict[str, Any]],
)
async def save_domain_config(
    release_id: str,
    domain_key: str,
    body: SaveDomainPayload,
    request: Request,
    user_id: str = Depends(require_write_roles),
) -> APIResponse[dict[str, Any]]:
    """Save a validated domain payload into a mutable draft release."""

    repo = resolve_configuration_repository(request)
    canonical = _canonical_domain_payload(domain_key, body.payload)
    previous = await repo.get_domain_config(release_id, domain_key)
    try:
        await repo.save_draft_domain(release_id, domain_key, canonical, actor_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await record_configuration_audit(
        request,
        action="CONFIGURATION_DOMAIN_REPLACED",
        actor=user_id,
        target=f"{release_id}/{domain_key}",
        details={"changedPaths": _changed_paths(previous or {}, canonical)},
    )

    updated = await repo.get_domain_config(release_id, domain_key)
    return APIResponse(
        data={"domain_key": domain_key, "payload": updated},
        meta=_response_meta(request),
    )


@router.patch(
    "/releases/{release_id}/domains/{domain_key}",
    response_model=APIResponse[dict[str, Any]],
)
async def patch_domain_config(
    release_id: str,
    domain_key: str,
    body: PatchDomainPayload,
    request: Request,
    user_id: str = Depends(require_write_roles),
) -> APIResponse[dict[str, Any]]:
    """Patch selected behavior fields in a draft without replacing the full graph document."""

    repo = resolve_configuration_repository(request)
    current = await repo.get_domain_config(release_id, domain_key)
    if current is None:
        raise HTTPException(
            status_code=404,
            detail=f"Domain {domain_key} was not found in release {release_id}",
        )
    updated_payload = _canonical_domain_payload(domain_key, _apply_merge_patch(current, body.patch))
    try:
        await repo.save_draft_domain(
            release_id,
            domain_key,
            updated_payload,
            actor_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await record_configuration_audit(
        request,
        action="CONFIGURATION_DOMAIN_PATCHED",
        actor=user_id,
        target=f"{release_id}/{domain_key}",
        details={
            "patchKeys": sorted(body.patch),
            "changedPaths": _changed_paths(current, updated_payload),
        },
    )
    return APIResponse(
        data={"domain_key": domain_key, "payload": updated_payload},
        meta=_response_meta(request),
    )


class PromoteReleasePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["VALIDATED", "RELEASED", "ARCHIVED"]
    expected_head_revision: int | None = Field(default=None, ge=0)


@router.post("/releases/{release_id}/promote", response_model=APIResponse[dict[str, Any]])
async def promote_release_status(
    release_id: str,
    body: PromoteReleasePayload,
    request: Request,
    user_id: str = Depends(require_write_roles),
) -> APIResponse[dict[str, Any]]:
    """Promote a validated immutable release through an explicit lifecycle.

    The body of this handler is `promote_configuration_release`. It moved there
    when W4.2 made an agent configuration edit into a release: the kernel's
    activator has to publish one and has no `Request` to do it with, and the
    rules that make a promotion safe -- three domains present and valid,
    unexpired receipts, `expected_head_revision`, forced refresh on RELEASED --
    are not rules worth having two copies of.
    """

    repo = resolve_configuration_repository(request)
    resources = getattr(request.app.state, "resources", None)
    store = resources if isinstance(resources, RuntimeResources) else None
    activator = getattr(request.app.state, "runtime_configuration_activator", None)
    try:
        outcome = await promote_configuration_release(
            repository=repo,
            release_id=release_id,
            target_status=body.status,
            actor_id=user_id,
            expected_head_revision=body.expected_head_revision,
            mongo=store.mongo if store is not None else None,
            mongo_database=store.settings.mongo_database if store is not None else None,
            activator=(activator if isinstance(activator, RuntimeConfigurationActivator) else None),
        )
    except ReleasePromotionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    await record_configuration_audit(
        request,
        action="CONFIGURATION_RELEASE_PROMOTED",
        actor=user_id,
        target=release_id,
        details={
            "status": body.status,
            "headRevision": outcome.head_revision,
            "checksumSha256": outcome.release.checksum_sha256,
            "activatedReleaseId": (
                outcome.activated_snapshot.release_id
                if outcome.activated_snapshot is not None
                else None
            ),
        },
    )

    data = outcome.release.model_dump(mode="json")
    data["domains"] = outcome.domains
    data["head_revision"] = outcome.head_revision
    if outcome.activated_snapshot is not None:
        data["runtime_activation"] = {
            "release_id": outcome.activated_snapshot.release_id,
            "checksum_sha256": outcome.activated_snapshot.checksum_sha256,
            "head_revision": outcome.activated_snapshot.head_revision,
            "loaded_at": outcome.activated_snapshot.loaded_at,
        }
    return APIResponse(data=data, meta=_response_meta(request))
