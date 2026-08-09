"""Operations APIs for versioned graph-backed runtime configuration."""

from __future__ import annotations

import copy
from typing import Any, Final, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.ai_gateway.configuration import (
    AIGatewayConfiguration,
    LoadedAIGatewayConfiguration,
)
from return_platform.configuration.graph_repository import (
    ConfigurationGraphRepository,
    ConfigurationRevisionConflict,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.runtime_integrations import (
    verify_runtime_validation_receipts,
)
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
    ConfigurationSnapshotBuilder,
)
from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.dependency_simulation.configuration import (
    DependencySimulationConfiguration,
    LoadedDependencySimulationConfiguration,
)
from return_platform.resources import RuntimeResources
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

    release = await repo.get_release(payload.release_id)
    if release is None:
        raise HTTPException(status_code=500, detail="Configuration release creation failed")
    data = release.model_dump(mode="json")
    data["domains"] = await repo.get_all_domain_configs(payload.release_id)
    return APIResponse(data=data, meta=_response_meta(request))


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
    if domain_key == RETURN_PLATFORM_DOMAIN_KEY:
        try:
            ReturnPlatformConfiguration.model_validate(body.payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif domain_key == AI_GATEWAY_DOMAIN_KEY:
        try:
            AIGatewayConfiguration.model_validate(body.payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif domain_key == DEPENDENCY_SIMULATION_DOMAIN_KEY:
        try:
            DependencySimulationConfiguration.model_validate(body.payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        await repo.save_draft_domain(release_id, domain_key, body.payload, actor_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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
    updated_payload = _apply_merge_patch(current, body.patch)
    if domain_key == RETURN_PLATFORM_DOMAIN_KEY:
        try:
            ReturnPlatformConfiguration.model_validate(updated_payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif domain_key == AI_GATEWAY_DOMAIN_KEY:
        try:
            AIGatewayConfiguration.model_validate(updated_payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif domain_key == DEPENDENCY_SIMULATION_DOMAIN_KEY:
        try:
            DependencySimulationConfiguration.model_validate(updated_payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        await repo.save_draft_domain(
            release_id,
            domain_key,
            updated_payload,
            actor_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    """Promote a validated immutable release through an explicit lifecycle."""

    repo = resolve_configuration_repository(request)
    release = await repo.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail=f"Release {release_id} not found")

    allowed_transitions = {
        "DRAFT": {"VALIDATED", "ARCHIVED"},
        "VALIDATED": {"RELEASED", "ARCHIVED"},
        "SUPERSEDED": {"ARCHIVED"},
    }
    if body.status not in allowed_transitions.get(release.status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Invalid configuration transition {release.status} -> {body.status}",
        )

    if body.status in {"VALIDATED", "RELEASED"}:
        domain_payloads = await repo.get_all_domain_configs(release_id)
        required_domains = {
            RETURN_PLATFORM_DOMAIN_KEY,
            AI_GATEWAY_DOMAIN_KEY,
            DEPENDENCY_SIMULATION_DOMAIN_KEY,
        }
        missing_domains = sorted(required_domains - set(domain_payloads))
        if missing_domains:
            raise HTTPException(
                status_code=422,
                detail="Release is missing behavior domains: " + ", ".join(missing_domains),
            )
        try:
            validated_configuration = ReturnPlatformConfiguration.model_validate(
                domain_payloads[RETURN_PLATFORM_DOMAIN_KEY]
            )
            AIGatewayConfiguration.model_validate(domain_payloads[AI_GATEWAY_DOMAIN_KEY])
            DependencySimulationConfiguration.model_validate(
                domain_payloads[DEPENDENCY_SIMULATION_DOMAIN_KEY]
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        resources = getattr(request.app.state, "resources", None)
        if not isinstance(resources, RuntimeResources) or resources.mongo is None:
            raise HTTPException(status_code=503, detail="Validation receipt store is unavailable")
        try:
            await verify_runtime_validation_receipts(
                resources.mongo,
                resources.settings.mongo_database,
                validated_configuration,
                require_unexpired=True,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.status == "RELEASED" and body.expected_head_revision is None:
        raise HTTPException(
            status_code=422,
            detail="expected_head_revision is required to publish a configuration release",
        )

    try:
        updated = await repo.promote_release(
            release_id,
            body.status,
            actor_id=user_id,
            expected_head_revision=body.expected_head_revision,
        )
    except ConfigurationRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFIGURATION_REVISION_CONFLICT",
                "message": str(exc),
                "current_head_revision": await repo.get_head_revision(),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    activated_snapshot = None
    if body.status == "RELEASED":
        activator = getattr(request.app.state, "runtime_configuration_activator", None)
        if not isinstance(activator, RuntimeConfigurationActivator):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Configuration was released in the graph, but this process has no runtime "
                    "configuration activator"
                ),
            )
        try:
            activated_snapshot = await activator.refresh(force=True)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Configuration was released in the graph but could not be activated in this "
                    f"process: {type(exc).__name__}"
                ),
            ) from exc

    data = updated.model_dump(mode="json")
    data["domains"] = await repo.get_all_domain_configs(release_id)
    data["head_revision"] = await repo.get_head_revision()
    if activated_snapshot is not None:
        data["runtime_activation"] = {
            "release_id": activated_snapshot.release_id,
            "checksum_sha256": activated_snapshot.checksum_sha256,
            "head_revision": activated_snapshot.head_revision,
            "loaded_at": activated_snapshot.loaded_at,
        }
    return APIResponse(data=data, meta=_response_meta(request))
