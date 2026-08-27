"""Promoting a configuration release, as one callable.

The body of `POST /data-console/v1/configuration/releases/{id}/promote`, moved
out of the handler so something other than an HTTP request can drive it. W4.2
needs exactly that: an agent configuration edit becomes a release, and the
proposal kernel activates it -- with no `Request` anywhere in sight.

**Moved, not reimplemented.** The rules it carries are the ones that make a
promotion safe and none of them are obvious: all three behaviour domains must be
present and valid, runtime validation receipts must be unexpired, publishing
requires the caller's `expected_head_revision` so two operators cannot both
publish over each other, and RELEASED must be followed by a forced refresh or
the process that published it goes on serving the old snapshot. A second copy of
that list would be a second lifecycle, which is what D3 spent its effort
deleting.

The router keeps its status codes by mapping `ReleasePromotionError.status_code`
-- the error carries the code the handler used to raise inline, so the HTTP
contract is unchanged by the move.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pymongo import AsyncMongoClient

from return_platform.ai.routing.tasks import AIGatewayConfiguration
from return_platform.configuration.graph_repository import (
    ConfigurationGraphRepository,
    ConfigurationReleaseNode,
    ConfigurationRevisionConflict,
    transition_allowed,
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
    PinnedConfigurationSnapshot,
)
from return_platform.dependency_simulation.configuration import (
    DependencySimulationConfiguration,
)

__all__ = [
    "PromotionOutcome",
    "PromotionTarget",
    "ReleasePromotionError",
    "promote_configuration_release",
    "publish_release_with_domains",
]

PromotionTarget = Literal["VALIDATED", "RELEASED", "ARCHIVED"]


class ReleasePromotionError(Exception):
    """A refused promotion, carrying the status the HTTP surface answers with.

    The status code lives on the error rather than being re-derived by each
    caller: this used to be a handler raising `HTTPException` inline, and the
    distinction between "your document is invalid" (422), "the lifecycle does not
    allow that" (409) and "this process cannot activate" (503) is meaning, not
    presentation.
    """

    def __init__(self, message: str, *, status_code: int, detail: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail if detail is not None else message


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    release: ConfigurationReleaseNode
    domains: dict[str, Any]
    head_revision: int
    activated_snapshot: PinnedConfigurationSnapshot | None


async def promote_configuration_release(
    *,
    repository: ConfigurationGraphRepository,
    release_id: str,
    target_status: PromotionTarget,
    actor_id: str,
    expected_head_revision: int | None = None,
    mongo: AsyncMongoClient[dict[str, object]] | None = None,
    mongo_database: str | None = None,
    activator: RuntimeConfigurationActivator | None = None,
) -> PromotionOutcome:
    release = await repository.get_release(release_id)
    if release is None:
        raise ReleasePromotionError(f"Release {release_id} not found", status_code=404)

    # Shared table, not a third copy -- see RELEASE_TRANSITIONS. This early check
    # exists so a refused promotion answers 409 before the domain validation
    # below does any work; `promote_release` enforces the same rule as the real
    # boundary.
    if not transition_allowed(release.status, target_status):
        raise ReleasePromotionError(
            f"Invalid configuration transition {release.status} -> {target_status}",
            status_code=409,
        )

    if target_status in {"VALIDATED", "RELEASED"}:
        domain_payloads = await repository.get_all_domain_configs(release_id)
        required_domains = {
            RETURN_PLATFORM_DOMAIN_KEY,
            AI_GATEWAY_DOMAIN_KEY,
            DEPENDENCY_SIMULATION_DOMAIN_KEY,
        }
        missing_domains = sorted(required_domains - set(domain_payloads))
        if missing_domains:
            raise ReleasePromotionError(
                "Release is missing behavior domains: " + ", ".join(missing_domains),
                status_code=422,
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
            raise ReleasePromotionError(str(exc), status_code=422) from exc
        if mongo is None or mongo_database is None:
            raise ReleasePromotionError("Validation receipt store is unavailable", status_code=503)
        try:
            await verify_runtime_validation_receipts(
                mongo,
                mongo_database,
                validated_configuration,
                require_unexpired=True,
            )
        except RuntimeError as exc:
            raise ReleasePromotionError(str(exc), status_code=422) from exc

    if target_status == "RELEASED" and expected_head_revision is None:
        raise ReleasePromotionError(
            "expected_head_revision is required to publish a configuration release",
            status_code=422,
        )

    try:
        updated = await repository.promote_release(
            release_id,
            target_status,
            actor_id=actor_id,
            expected_head_revision=expected_head_revision,
        )
    except ConfigurationRevisionConflict as exc:
        raise ReleasePromotionError(
            str(exc),
            status_code=409,
            detail={
                "code": "CONFIGURATION_REVISION_CONFLICT",
                "message": str(exc),
                "current_head_revision": await repository.get_head_revision(),
            },
        ) from exc
    except ValueError as exc:
        raise ReleasePromotionError(str(exc), status_code=409) from exc

    activated_snapshot = None
    if target_status == "RELEASED":
        if activator is None:
            raise ReleasePromotionError(
                "Configuration was released in the graph, but this process has no runtime "
                "configuration activator",
                status_code=503,
            )
        try:
            activated_snapshot = await activator.refresh(force=True)
        except Exception as exc:
            raise ReleasePromotionError(
                "Configuration was released in the graph but could not be activated in this "
                f"process: {type(exc).__name__}",
                status_code=503,
            ) from exc

    return PromotionOutcome(
        release=updated,
        domains=await repository.get_all_domain_configs(release_id),
        head_revision=await repository.get_head_revision(),
        activated_snapshot=activated_snapshot,
    )


async def publish_release_with_domains(
    *,
    repository: ConfigurationGraphRepository,
    release_id: str,
    domains: Mapping[str, Any],
    actor_id: str,
    mongo: AsyncMongoClient[dict[str, object]] | None,
    mongo_database: str | None,
    activator: RuntimeConfigurationActivator | None,
) -> PromotionOutcome:
    """Cut a release from the active one with `domains` overlaid, and publish it.

    Both governed configuration changes need exactly this -- an agent module
    edit and a feedback improvement -- and the sequence is not obvious enough to
    write twice: clone the active release *whole* (a release carrying one domain
    is not a release, it is a way to delete the other three), overwrite what
    changed, then VALIDATED and only then RELEASED.

    `expected_head_revision` is read between the two promotions rather than at
    the start. Publication is compare-and-set on that value; reading it earlier
    widens the window in which another publisher slips in and this one still
    claims the head it expected.
    """
    active = await repository.get_active_release()
    if active is None:
        raise ReleasePromotionError(
            "there is no active configuration release to base this one on",
            status_code=409,
        )
    merged = dict(await repository.get_all_domain_configs(active.release_id))
    merged.update({key: dict(value) for key, value in domains.items()})
    for domain_key, payload in merged.items():
        await repository.save_draft_domain(release_id, domain_key, dict(payload), actor_id=actor_id)

    # The packaged baseline the cloned release was built from, carried onto the
    # clone. `bootstrap_graph_configuration` reads it to tell an operator's edit
    # apart from a change to the packaged file; a release published without one
    # is undecidable, and the next bootstrap keeps the whole release rather than
    # adopt anything -- so dropping it here would re-break packaged config
    # updates after every governed change an operator makes.
    #
    # The overlay itself needs no special treatment: it moves those domains AWAY
    # from the baseline, which is exactly how the next publish reads them as
    # edited and leaves them alone.
    if active.metadata:
        await repository.set_release_metadata(release_id, dict(active.metadata))

    await promote_configuration_release(
        repository=repository,
        release_id=release_id,
        target_status="VALIDATED",
        actor_id=actor_id,
        mongo=mongo,
        mongo_database=mongo_database,
        activator=activator,
    )
    return await promote_configuration_release(
        repository=repository,
        release_id=release_id,
        target_status="RELEASED",
        actor_id=actor_id,
        expected_head_revision=await repository.get_head_revision(),
        mongo=mongo,
        mongo_database=mongo_database,
        activator=activator,
    )
