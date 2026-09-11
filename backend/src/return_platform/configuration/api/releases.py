"""Operations handlers for versioned graph-backed runtime configuration.

Handler bodies only -- no `APIRouter` here. `create_release`, `patch_domain_config`
and `promote_release_status` are mounted by the canonical
`configuration/api/router.py` under `/api/config`; the `/data-console/v1/configuration`
`APIRouter` this module used to also declare them under was retired in CFG-1
(D-CFG-5) because nothing ever mounted it. Retired along with it: `get_active_snapshot`
(a fallback-build path `ConfigurationSnapshotBuilder`'s own tests already cover),
`list_releases`/`get_release_detail` (duplicates of the canonical router's own),
and `save_domain_config` -- the full-document `PUT` had no consumer; `patch_domain_config`
is the write.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    LoadedAIGatewayConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.configuration.application.packaged_adoption import (
    PACKAGED_DOMAIN_KEY_DIGESTS,
    PACKAGED_KEY_DIGESTS,
    adopt_packaged_configuration,
    summarize_packaged_drift,
)
from return_platform.configuration.application.release_promotion import (
    ReleasePromotionError,
    promote_configuration_release,
    publish_release_with_domains,
)
from return_platform.configuration.graph_repository import (
    ConfigurationGraphRepository,
    ConfigurationReleaseNode,
)
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
from return_platform.dependency_simulation.configuration import (
    DependencySimulationConfiguration,
    LoadedDependencySimulationConfiguration,
    load_dependency_simulation_configuration,
)
from return_platform.operations.repository import resolve_operational_repository
from return_platform.resources import RuntimeResources
from return_platform.security import capabilities
from return_platform.security.authorization import (
    require_capability,
    require_read_roles,
    require_write_roles,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta

logger = logging.getLogger(__name__)


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


class CreateReleasePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$")
    from_active: bool = True


async def _active_or_baseline_domains(
    request: Request,
    repo: ConfigurationGraphRepository,
    active_release: ConfigurationReleaseNode | None,
    *,
    from_active: bool,
) -> dict[str, Any]:
    """The domains a new draft starts from: the active release, or the
    packaged baseline for whichever domain it does not carry.

    Shared by `create_release` and `publish_configuration` (CFG-3a) so a
    single-call publish clones a release exactly the way the four-round-trip
    path always has -- one behaviour, not a second copy of it for the
    collapsed pipeline.
    """
    domains_to_copy: dict[str, Any] = {}
    if active_release is not None and from_active:
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
    return domains_to_copy


async def create_release(
    payload: CreateReleasePayload,
    request: Request,
    user_id: str = Depends(require_write_roles),
) -> APIResponse[dict[str, Any]]:
    """Create a draft by cloning the active release or current validated baseline."""

    repo = resolve_configuration_repository(request)
    if await repo.get_release(payload.release_id) is not None:
        raise HTTPException(status_code=409, detail=f"Release {payload.release_id} already exists")

    active_release = await repo.get_active_release()
    domains_to_copy = await _active_or_baseline_domains(
        request, repo, active_release, from_active=payload.from_active
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
) -> str:
    """One audit record per configuration release change, in the platform's audit log.

    Release create, domain patch and promotion left no entry in the `audit`
    collection that `GET /api/config/audit` serves: the only trail was the
    release's `created_by` and each domain's `updated_by`, overwritten on every
    edit, with no before/after. An operator asking who changed a threshold and
    what it was before had no answer. This is the same `append_audit` the AI
    gateway and governance kernel already write through -- one log, not a
    second one.

    Best-effort, and deliberately so: it runs after the authoritative graph
    write. A promote that has already moved the head revision must not answer
    503 because the audit store was unreachable -- the operator would retry and
    cut a second release. The failure is logged with everything the record
    would have carried, so the gap is visible rather than silent.

    **Returns a correlation id, not the storage primary key.** `append_audit`
    (`operations/repository.py`, outside this lease's Owns list) assigns its
    own `_id` and does not hand it back. `/publish` and `/adopt-packaged`
    need something to report as `audit_ids` -- an id an operator can hand to
    support, or grep the log line above for -- so one is generated here,
    stamped into the stored record as `details["auditId"]`, and returned
    unconditionally, even when the write below fails: the id is a token this
    call assigned, not a promise the record is queryable, and the failure
    right above it is what says whether it is.
    """
    audit_id = str(uuid4())
    try:
        repository = resolve_operational_repository(request)
        await repository.append_audit(
            action=action,
            actor=actor,
            target=target,
            details={**details, "auditId": audit_id},
        )
    except Exception:  # noqa: BLE001 -- a failure here must not undo a completed write
        logger.exception(
            "configuration_audit_not_recorded action=%s actor=%s target=%s auditId=%s details=%s",
            action,
            actor,
            target,
            audit_id,
            json.dumps(details, sort_keys=True, default=str),
        )
    return audit_id


_CHANGED_PATHS_CAP = 50


def _changed_paths(before: Any, after: Any) -> list[str]:
    """Dotted paths whose value differs between two JSON documents.

    Capped at `_CHANGED_PATHS_CAP` entries; a truncated list ends with a marker
    naming how many more there were, so a reader never mistakes the cap for the
    whole change.
    """
    paths = _all_changed_paths(before, after, "")
    if len(paths) > _CHANGED_PATHS_CAP:
        return [*paths[:_CHANGED_PATHS_CAP], f"... {len(paths) - _CHANGED_PATHS_CAP} more"]
    return paths


def _all_changed_paths(before: Any, after: Any, prefix: str) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                paths.append(child)
            else:
                paths.extend(_all_changed_paths(before[key], after[key], child))
        return paths
    return [] if before == after else [prefix or "$"]


#: The three domain models this platform reads, keyed the way every release's
#: payload map is keyed. One place naming the association -- `_domain_model`,
#: `_canonical_domain_payload` and `_validation_errors` all resolve a domain
#: key through this rather than each repeating the same three-way `if`.
_DOMAIN_MODELS: dict[
    str,
    type[ReturnPlatformConfiguration | AIGatewayConfiguration | DependencySimulationConfiguration],
] = {
    RETURN_PLATFORM_DOMAIN_KEY: ReturnPlatformConfiguration,
    AI_GATEWAY_DOMAIN_KEY: AIGatewayConfiguration,
    DEPENDENCY_SIMULATION_DOMAIN_KEY: DependencySimulationConfiguration,
}


def _domain_model(
    domain_key: str,
) -> type[ReturnPlatformConfiguration | AIGatewayConfiguration | DependencySimulationConfiguration]:
    """The pydantic model that owns `domain_key`, or a 404 naming the three that exist."""
    model = _DOMAIN_MODELS.get(domain_key)
    if model is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Domain {domain_key} is not a configuration domain; expected one of "
                f"{', '.join(_DOMAIN_MODELS)}"
            ),
        )
    return model


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
    model = _domain_model(domain_key)
    try:
        return model.model_validate(payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _dotted_error_path(loc: tuple[int | str, ...]) -> str:
    """A pydantic error `loc` tuple, joined the way the console reads a field.

    `("return_policy", "return_method_derivation", "default_method")` becomes
    `return_policy.return_method_derivation.default_method`; a list index is
    suffixed onto the segment before it (`agents[2].version`, not
    `agents.2.version`) so a numeric path component never reads as a mapping
    key named `"2"`.
    """
    parts: list[str] = []
    for segment in loc:
        if isinstance(segment, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{segment}]"
            else:
                parts.append(f"[{segment}]")
        else:
            parts.append(str(segment))
    return ".".join(parts)


def _validation_errors(domain_key: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate `payload` against the domain it claims and report, never raise.

    `POST /validate/{domain_key}` exists so a form can ask "would this be
    valid" before committing to a draft or a patch -- the difference from
    `_canonical_domain_payload` is exactly that: this reports pydantic's own
    `ValidationError.errors()`, mapped to `{path, message, type}`, instead of
    collapsing them into one string and raising. An unknown domain key still
    404s -- there is no payload shape to report errors *about*.
    """
    model = _domain_model(domain_key)
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        return [
            {
                "path": _dotted_error_path(error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
    return []


class PatchDomainPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: dict[str, Any]
    #: Optimistic lock. Absent (the default) applies the patch unconditionally,
    #: exactly as before this field existed -- the frontend pipeline that reads
    #: a domain, patches it and never round-trips a version keeps working with
    #: no change. Present, it must equal `get_domain_version`'s answer for this
    #: release/domain or the write is refused with 409 rather than silently
    #: applied over an edit the caller never saw -- two operators editing the
    #: same draft from two open tabs is the ordinary way this happens.
    expected_version: int | None = Field(default=None, ge=0)


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
    if body.expected_version is not None:
        current_version = await repo.get_domain_version(release_id, domain_key)
        if current_version != body.expected_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CONFIGURATION_DOMAIN_VERSION_CONFLICT",
                    "message": (
                        f"Domain {domain_key} of release {release_id} is at version "
                        f"{current_version}, not the expected {body.expected_version}"
                    ),
                    "current_version": current_version,
                },
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


class ValidateDomainPayload(BaseModel):
    """Body of `POST /validate/{domain_key}` -- exactly one of two shapes.

    `payload` validates a whole document standalone, with nothing read from
    any release: the shape a form uses before a draft exists at all.
    `patch` merges against the ACTIVE release's stored domain (never a
    draft's -- this route takes no `release_id`, because "would this be
    valid" is a question worth asking before a draft is even open, and the
    active release is the only document a caller with no draft yet can name).
    """

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any] | None = None
    patch: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> ValidateDomainPayload:
        if (self.payload is None) == (self.patch is None):
            raise ValueError("exactly one of payload or patch must be given")
        return self


async def validate_domain_config(
    domain_key: str,
    body: ValidateDomainPayload,
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[dict[str, Any]]:
    """Report whether a payload or patch would validate, without writing anything.

    No draft is opened, no domain is stored, no audit record is written --
    this is the check a form runs on every keystroke or on submit, and it must
    be side-effect-free to be safe to call that often. `errors` is pydantic's
    own `ValidationError.errors()`, mapped to `{path, message, type}` by
    `_validation_errors` -- the same structure a caller would get by reading
    the exception `_canonical_domain_payload` raises on a real write, so a
    form's error-rendering code path is exercised by both.
    """
    if body.payload is not None:
        candidate = body.payload
    else:
        repo = resolve_configuration_repository(request)
        active = await repo.get_active_release()
        if active is None:
            raise HTTPException(
                status_code=409,
                detail="There is no active configuration release to validate a patch against",
            )
        current = await repo.get_domain_config(active.release_id, domain_key)
        if current is None:
            raise HTTPException(
                status_code=404,
                detail=f"Domain {domain_key} was not found in the active release",
            )
        candidate = _apply_merge_patch(current, cast(dict[str, Any], body.patch))

    errors = _validation_errors(domain_key, candidate)
    return APIResponse(
        data={"valid": not errors, "errors": errors},
        meta=_response_meta(request),
    )


class PromoteReleasePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["VALIDATED", "RELEASED", "ARCHIVED"]
    expected_head_revision: int | None = Field(default=None, ge=0)


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


# --- publish (single transaction) ---------------------------------------------
#
# CFG-3a scope item 2. Every screen that changes one prompt or one policy
# used to be four round trips -- create, patch, promote VALIDATED, promote
# RELEASED -- each one a chance for a concurrent editor's own draft to land
# in between. This composes the three primitives the brief names
# (`promote_configuration_release`, the canonical payload helper
# `_canonical_domain_payload`, and `record_configuration_audit`) directly
# rather than calling `create_release`/`patch_domain_config`/
# `promote_release_status` as sub-requests: those three already exist as
# HTTP handlers with their own response shapes, and composing THOSE would
# make this a wrapper around wrappers rather than the one-transaction
# handler the brief asks for. `_active_or_baseline_domains` is shared with
# `create_release` for exactly the one piece that WOULD otherwise be a
# second copy -- the baseline clone.


class PublishConfigurationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Omit to let the server assign one; name one to make the id
    #: predictable (a runbook, a migration script). Either way this is a NEW
    #: release -- PATCH is the surface for editing one already in flight,
    #: and it takes the id as a path segment for exactly that reason.
    release_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$",
    )
    domain_key: str
    patch: dict[str, Any]
    expected_head_revision: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=2000)


async def publish_configuration(
    body: PublishConfigurationPayload,
    request: Request,
    user_id: str = Depends(require_capability(capabilities.CONFIG_RELEASE_WRITE)),
) -> APIResponse[dict[str, Any]]:
    """Create-from-active, canonical patch, VALIDATED, RELEASED -- one call.

    **On any refusal the draft this call created is archived, not left
    behind** (`_archive_draft_on_refusal`): a caller retrying after a 409 or
    422 must not find a half-published release occupying the id it asked
    for.

    **Writes the same per-step audit records the four-call path writes.**
    `create_release`, `patch_domain_config` and `promote_release_status`
    each call `record_configuration_audit` themselves; this handler calls
    `promote_configuration_release` and `_canonical_domain_payload`
    directly (the brief's own primitives, not the three route handlers --
    see the module note above), so it must call `record_configuration_audit`
    itself at each step or the trail those three would have left simply
    would not exist. RV F2: an earlier version of this handler wrote one
    summary `CONFIGURATION_RELEASE_PUBLISHED` record and claimed the
    per-step trail existed anyway -- it did not. Now: `CONFIGURATION_
    RELEASE_CREATED`, `CONFIGURATION_DOMAIN_PATCHED` (with `changedPaths`,
    the same before/after leaf diff the four-call path records), two
    `CONFIGURATION_RELEASE_PROMOTED` (VALIDATED then RELEASED), and the
    summary record last -- every id returned in `audit_ids`, in that order,
    all independently queryable via `GET /audit?target=<release>`.
    """
    repo = resolve_configuration_repository(request)
    release_id = body.release_id or f"publish-{uuid4().hex[:16]}"
    if await repo.get_release(release_id) is not None:
        raise HTTPException(status_code=409, detail=f"Release {release_id} already exists")

    audit_ids: list[str] = []
    try:
        active_release = await repo.get_active_release()
        domains_to_copy = await _active_or_baseline_domains(
            request, repo, active_release, from_active=True
        )
        for domain_key, domain_payload in domains_to_copy.items():
            await repo.save_draft_domain(
                release_id, domain_key, cast(dict[str, Any], domain_payload), actor_id=user_id
            )
        if active_release is not None and active_release.metadata:
            await repo.set_release_metadata(release_id, dict(active_release.metadata))
        audit_ids.append(
            await record_configuration_audit(
                request,
                action="CONFIGURATION_RELEASE_CREATED",
                actor=user_id,
                target=release_id,
                details={
                    "clonedFrom": (
                        active_release.release_id if active_release is not None else None
                    ),
                    "domains": sorted(domains_to_copy),
                },
            )
        )

        current = await repo.get_domain_config(release_id, body.domain_key)
        if current is None:
            raise HTTPException(
                status_code=404,
                detail=f"Domain {body.domain_key} was not found in release {release_id}",
            )
        updated_payload = _canonical_domain_payload(
            body.domain_key, _apply_merge_patch(current, body.patch)
        )
        try:
            await repo.save_draft_domain(
                release_id, body.domain_key, updated_payload, actor_id=user_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_ids.append(
            await record_configuration_audit(
                request,
                action="CONFIGURATION_DOMAIN_PATCHED",
                actor=user_id,
                target=f"{release_id}/{body.domain_key}",
                details={
                    "patchKeys": sorted(body.patch),
                    "changedPaths": _changed_paths(current, updated_payload),
                },
            )
        )

        resources = getattr(request.app.state, "resources", None)
        store = resources if isinstance(resources, RuntimeResources) else None
        activator = getattr(request.app.state, "runtime_configuration_activator", None)
        promotion_kwargs: dict[str, Any] = {
            "repository": repo,
            "release_id": release_id,
            "actor_id": user_id,
            "mongo": store.mongo if store is not None else None,
            "mongo_database": store.settings.mongo_database if store is not None else None,
            "activator": (
                activator if isinstance(activator, RuntimeConfigurationActivator) else None
            ),
        }
        try:
            validated_outcome = await promote_configuration_release(
                target_status="VALIDATED", **promotion_kwargs
            )
        except ReleasePromotionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        audit_ids.append(
            await record_configuration_audit(
                request,
                action="CONFIGURATION_RELEASE_PROMOTED",
                actor=user_id,
                target=release_id,
                details={
                    "status": "VALIDATED",
                    "headRevision": validated_outcome.head_revision,
                    "checksumSha256": validated_outcome.release.checksum_sha256,
                    "activatedReleaseId": None,
                },
            )
        )

        try:
            outcome = await promote_configuration_release(
                target_status="RELEASED",
                expected_head_revision=body.expected_head_revision,
                **promotion_kwargs,
            )
        except ReleasePromotionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        audit_ids.append(
            await record_configuration_audit(
                request,
                action="CONFIGURATION_RELEASE_PROMOTED",
                actor=user_id,
                target=release_id,
                details={
                    "status": "RELEASED",
                    "headRevision": outcome.head_revision,
                    "checksumSha256": outcome.release.checksum_sha256,
                    "activatedReleaseId": (
                        outcome.activated_snapshot.release_id
                        if outcome.activated_snapshot is not None
                        else None
                    ),
                },
            )
        )
    except HTTPException:
        await _archive_draft_on_refusal(repo, release_id, user_id)
        raise

    details: dict[str, Any] = {
        "domainKey": body.domain_key,
        "patchKeys": sorted(body.patch),
        "clonedFrom": active_release.release_id if active_release is not None else None,
        "headRevision": outcome.head_revision,
        "checksumSha256": outcome.release.checksum_sha256,
    }
    if body.note:
        details["note"] = body.note
    audit_ids.append(
        await record_configuration_audit(
            request,
            action="CONFIGURATION_RELEASE_PUBLISHED",
            actor=user_id,
            target=release_id,
            details=details,
        )
    )

    data = outcome.release.model_dump(mode="json")
    data["domains"] = outcome.domains
    data["head_revision"] = outcome.head_revision
    data["audit_ids"] = audit_ids
    if outcome.activated_snapshot is not None:
        data["runtime_activation"] = {
            "release_id": outcome.activated_snapshot.release_id,
            "checksum_sha256": outcome.activated_snapshot.checksum_sha256,
            "head_revision": outcome.activated_snapshot.head_revision,
            "loaded_at": outcome.activated_snapshot.loaded_at,
        }
    return APIResponse(data=data, meta=_response_meta(request))


# --- packaged adoption ---------------------------------------------------------
#
# CFG-3a scope item 3. `adopt_packaged_configuration` is
# `bootstrap_graph_configuration.main`'s own carry-forward decision, extracted
# so this route and the CLI can never disagree about which key an edit
# belongs to -- see `configuration/application/packaged_adoption.py`.


def _packaged_domain_payloads(request: Request) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The packaged files on disk, read fresh, as dicts.

    **Not `app.state.return_configuration`/`ai_gateway_configuration`.**
    Those hold the RUNTIME snapshot: `RuntimeConfigurationActivator.refresh`
    overwrites them with whatever the ACTIVE RELEASE contains the moment one
    is promoted (`runtime_activation.py:375`), so after this process has ever
    activated a release, `app.state.return_configuration` no longer reflects
    the packaged file at all -- it reflects the release, which is exactly the
    other side of the comparison this function exists to make possible. The
    CLI reads `settings.return_configuration_path` fresh on every invocation
    for the same reason; this does the same read, from the same paths, so
    "packaged" means the same thing to both callers of
    `adopt_packaged_configuration`.
    """
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise HTTPException(status_code=503, detail="Packaged configuration is unavailable")
    try:
        loaded = load_return_configuration(settings.return_configuration_path)
        loaded_ai_gateway = load_ai_gateway_configuration(settings.ai_gateway_configuration_path)
        loaded_dependency_simulation = load_dependency_simulation_configuration(
            settings.dependency_simulation_configuration_path
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail=f"Packaged configuration is unavailable: {exc}"
        ) from exc
    return (
        loaded.configuration.model_dump(mode="json"),
        {
            AI_GATEWAY_DOMAIN_KEY: loaded_ai_gateway.configuration.model_dump(mode="json"),
            DEPENDENCY_SIMULATION_DOMAIN_KEY: (
                loaded_dependency_simulation.configuration.model_dump(mode="json")
            ),
        },
    )


async def _archive_draft_on_refusal(
    repo: ConfigurationGraphRepository, release_id: str, actor_id: str
) -> None:
    """Leave nothing behind: a release this request itself created and did
    not reach RELEASED is archived rather than left an orphaned DRAFT or
    VALIDATED node.

    `ARCHIVED` is reachable from both `DRAFT` and `VALIDATED`
    (`RELEASE_TRANSITIONS`) -- this is the existing lifecycle's own way to
    retire a release nobody will publish, not a new transition invented for
    the rollback. Best-effort and silent on failure: a release that never got
    created (the refusal happened before the first `save_draft_domain`) has
    nothing to archive, and either case must not turn a real refusal into a
    second, more confusing error.
    """
    try:
        current = await repo.get_release(release_id)
        if current is not None and current.status in {"DRAFT", "VALIDATED"}:
            await repo.promote_release(release_id, "ARCHIVED", actor_id=actor_id)
    except Exception:  # noqa: BLE001 -- the original refusal is what must surface
        logger.exception(
            "configuration_release_rollback_failed release_id=%s",
            release_id,
        )


class AdoptPackagedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: `<key>` (a RETURN_PLATFORM top-level key, `discovery`) or
    #: `<DOMAIN>/<unit>` (`AI_GATEWAY/tasks.RETURN_STATUS_SUMMARY_V1`,
    #: `DEPENDENCY_SIMULATION/dependencies.OMC`) -- the same vocabulary
    #: `--adopt-packaged-key` uses, checked by the same `_adopt_requests`.
    units: list[str] = Field(default_factory=list)
    expected_head_revision: int = Field(ge=0)


async def adopt_packaged_release(
    body: AdoptPackagedPayload,
    request: Request,
    user_id: str = Depends(require_capability(capabilities.CONFIG_RELEASE_WRITE)),
) -> APIResponse[dict[str, Any]]:
    """Publish a release that adopts the named packaged units.

    The API's answer to a `packaged_configuration_not_adopted` warning,
    without a CLI invocation: everything named in `units` is taken from the
    packaged file for that key/unit; everything else keeps the active
    release's value exactly as the CLI's carry-forward would leave it.
    Publishes through `publish_release_with_domains` -- the same clone,
    overlay, VALIDATED-then-RELEASED sequence `/publish` and the governance
    kernel use -- with `expected_head_revision` as the caller's own
    optimistic lock rather than a value read fresh at the moment of
    publishing, since an operator resolving a drift panel is acting on a
    `GET /packaged-drift` read that may already be stale.

    On refusal the release this call created is archived, not left behind.
    """
    repo = resolve_configuration_repository(request)
    active = await repo.get_active_release()
    if active is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "There is no active configuration release to adopt packaged configuration into"
            ),
        )

    packaged_return_platform, packaged_domains = _packaged_domain_payloads(request)
    active_domain_payloads = await repo.get_all_domain_configs(active.release_id)

    try:
        adoption = adopt_packaged_configuration(
            packaged_return_platform=packaged_return_platform,
            packaged_domains=packaged_domains,
            active_return_platform=active_domain_payloads.get(RETURN_PLATFORM_DOMAIN_KEY),
            active_domains={
                key: value
                for key, value in active_domain_payloads.items()
                if key in packaged_domains
            },
            active_metadata=active.metadata,
            adopt_packaged_keys=tuple(body.units),
            release_id=active.release_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if RETURN_PLATFORM_DOMAIN_KEY not in adoption.merged_domains:
        raise HTTPException(
            status_code=422,
            detail=(
                "The active release's RETURN_PLATFORM domain no longer validates against "
                "the packaged configuration; nothing was published"
            ),
        )

    resources = getattr(request.app.state, "resources", None)
    store = resources if isinstance(resources, RuntimeResources) else None
    activator = getattr(request.app.state, "runtime_configuration_activator", None)
    release_id = f"adopt-packaged-{uuid4().hex[:16]}"

    try:
        outcome = await publish_release_with_domains(
            repository=repo,
            release_id=release_id,
            domains=adoption.merged_domains,
            actor_id=user_id,
            mongo=store.mongo if store is not None else None,
            mongo_database=store.settings.mongo_database if store is not None else None,
            activator=(activator if isinstance(activator, RuntimeConfigurationActivator) else None),
            expected_head_revision=body.expected_head_revision,
        )
    except ReleasePromotionError as exc:
        await _archive_draft_on_refusal(repo, release_id, user_id)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    # Metadata sits outside the checksum (frozen at VALIDATED), so it may be
    # set after RELEASED -- the same ordering `bootstrap_graph_configuration.main`
    # uses. `adoption.recordable_baseline`/`recordable_domain_baselines`
    # already carry forward every previously-known digest (`_carry_forward`
    # starts from the active release's own baseline), so this replaces the
    # release's metadata wholesale rather than merging it -- exactly what the
    # CLI's `set_release_metadata(release_id, release_metadata)` does.
    await repo.set_release_metadata(
        release_id,
        {
            PACKAGED_KEY_DIGESTS: adoption.recordable_baseline,
            PACKAGED_DOMAIN_KEY_DIGESTS: adoption.recordable_domain_baselines,
        },
    )

    undecided = {key: list(value) for key, value in adoption.undecided.items()}
    await record_configuration_audit(
        request,
        action="CONFIGURATION_PACKAGED_ADOPTED",
        actor=user_id,
        target=release_id,
        details={"units": sorted(body.units), "undecided": undecided},
    )

    data = outcome.release.model_dump(mode="json")
    data["domains"] = outcome.domains
    data["head_revision"] = outcome.head_revision
    data["undecided"] = undecided
    return APIResponse(data=data, meta=_response_meta(request))


async def get_packaged_drift(
    request: Request,
    _user_id: str = Depends(require_capability(capabilities.CONFIG_RELEASE_WRITE)),
) -> APIResponse[dict[str, dict[str, list[str]]]]:
    """The Overview screen's undecided-keys panel: `{undecided, would_adopt,
    filled_leaves}` per domain, read-only.

    Runs `summarize_packaged_drift`, which itself runs
    `adopt_packaged_configuration` with nothing explicitly requested -- the
    same computation `POST /adopt-packaged` would run for an empty `units`
    list. `undecided` is read straight off that result. `would_adopt` is
    derived from the MERGE `adopt_packaged_configuration` actually produced,
    not from `undecided`'s complement (RV F1: a key with a recorded baseline
    that an operator edited away from the file is decided -- `_carry_forward`
    keeps the release's value and does not mark it undecided -- but that is
    not the same as the file being taken, and only the merge result can say
    which one happened). Gated the same way `/adopt-packaged` is: this is
    the panel that tells an operator what there is to request, not a
    general configuration read.
    """
    repo = resolve_configuration_repository(request)
    active = await repo.get_active_release()
    packaged_return_platform, packaged_domains = _packaged_domain_payloads(request)
    active_domain_payloads = (
        await repo.get_all_domain_configs(active.release_id) if active is not None else {}
    )

    drift = summarize_packaged_drift(
        packaged_return_platform=packaged_return_platform,
        packaged_domains=packaged_domains,
        active_return_platform=active_domain_payloads.get(RETURN_PLATFORM_DOMAIN_KEY),
        active_domains={
            key: value for key, value in active_domain_payloads.items() if key in packaged_domains
        },
        active_metadata=active.metadata if active is not None else {},
        release_id=active.release_id if active is not None else None,
    )

    data = {
        domain_key: {
            "undecided": list(domain_drift.undecided),
            "would_adopt": list(domain_drift.would_adopt),
            "filled_leaves": list(domain_drift.filled_leaves),
        }
        for domain_key, domain_drift in drift.items()
    }
    return APIResponse(data=data, meta=_response_meta(request))
