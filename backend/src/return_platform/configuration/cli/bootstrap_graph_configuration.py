"""Publish the runtime configuration as an active graph release."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from typing import Any

from neo4j import AsyncGraphDatabase

from return_platform.ai.routing.tasks import (
    LoadedAIGatewayConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.configuration.application.packaged_adoption import (
    CARRY_FORWARD_SPLIT_KEYS,
    PACKAGED_DOMAIN_KEY_DIGESTS,
    PACKAGED_KEY_DIGESTS,
    _adopt_requests,
    _assemble,
    _carry_forward,
    _drop_retired_keys,
    _fill_absent_leaves,
    _key_digests,
    _units,
    adopt_packaged_configuration,
)
from return_platform.configuration.bootstrap_runtime_integrations import (
    begin_ai_validation_run,
    build_bootstrap_runtime_configuration,
    build_configured_runtime_configuration,
    finish_ai_validation_run,
)
from return_platform.configuration.deployment_settings import (
    deployment_payload_from_settings,
    merge_deployment_defaults,
)
from return_platform.configuration.graph_repository import (
    Neo4jConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
from return_platform.dependency_simulation.configuration import (
    load_dependency_simulation_configuration,
)
from return_platform.secrets.runtime import (
    resolve_runtime_settings_from_vault,
)
from return_platform.secrets.vault import SecretResolver

logger = logging.getLogger(__name__)

# The carry-forward decision itself moved to
# `configuration/application/packaged_adoption.py` (CFG-3a) so
# `POST /api/config/adopt-packaged` and `GET /api/config/packaged-drift` can
# run it too -- see that module's docstring. Every name imported above and
# re-bound here (`_units` through `_drop_retired_keys`, the two metadata
# keys, `CARRY_FORWARD_SPLIT_KEYS`) is unchanged in behaviour; listed in
# `__all__` so `bootstrap_graph_configuration.<name>` keeps resolving for
# every existing caller and test (none of which needed to change for the
# move) without ruff flagging the re-export as an unused import.
__all__ = [
    "CARRY_FORWARD_SPLIT_KEYS",
    "PACKAGED_DOMAIN_KEY_DIGESTS",
    "PACKAGED_KEY_DIGESTS",
    "_adopt_requests",
    "_assemble",
    "_carry_forward",
    "_drop_retired_keys",
    "_fill_absent_leaves",
    "_key_digests",
    "_units",
    "main",
    "run",
]


def _require_secret_resolver(resolver: SecretResolver | None) -> SecretResolver:
    """Return the resolver, or refuse the AI paths that cannot run without one.

    Publishing a configuration release needs no secrets beyond the ones the
    process was already started with. Recording AI provider routes does: every
    provider credential is held as a `vault://` reference and only a resolver can
    turn one into a key. So the resolver is required here, at the point of use,
    rather than for the command as a whole -- which is what lets the ordinary
    `--if-missing` publish run on a stack with `PLATFORM_VAULT_ENABLED=false`.
    """

    if resolver is None:
        raise RuntimeError(
            "AI provider validation and route refresh resolve provider credentials "
            "from Vault, so they cannot run while PLATFORM_VAULT_ENABLED is false. "
            "Publish without --validate-ai/--refresh-ai-routes, or enable Vault."
        )
    return resolver


async def _prepare_return_configuration(
    *,
    validate_ai: bool,
    force_ai_validation: bool = False,
    refresh_ai_routes: bool = False,
    settings: Settings,
    resolver: SecretResolver | None,
    loaded_ai_gateway: LoadedAIGatewayConfiguration,
    configuration: ReturnPlatformConfiguration,
    existing_configuration: ReturnPlatformConfiguration | None = None,
) -> ReturnPlatformConfiguration:
    if refresh_ai_routes:
        print("ai_bootstrap_validation=SKIPPED reason=receipt-and-configuration-refresh")
        return await build_configured_runtime_configuration(
            settings=settings,
            resolver=_require_secret_resolver(resolver),
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=configuration,
            existing_configuration=existing_configuration,
        )

    if not validate_ai:
        print("ai_bootstrap_validation=SKIPPED reason=explicit-parameter-required")
        return configuration
    if validate_ai and not isinstance(settings, Settings):
        return await build_bootstrap_runtime_configuration(
            settings=settings,
            resolver=_require_secret_resolver(resolver),
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=configuration,
        )

    decision = await begin_ai_validation_run(
        settings=settings,
        force=force_ai_validation,
    )
    if not decision.allowed:
        print(
            "ai_bootstrap_validation=SKIPPED "
            f"reason={decision.reason} "
            f"run_id={decision.run_id or 'none'}"
        )
        return await build_configured_runtime_configuration(
            settings=settings,
            resolver=_require_secret_resolver(resolver),
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=configuration,
            existing_configuration=existing_configuration,
        )

    if decision.run_id is None:
        raise RuntimeError("Allowed AI validation run is missing a run ID")

    try:
        prepared = await build_bootstrap_runtime_configuration(
            settings=settings,
            resolver=_require_secret_resolver(resolver),
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=configuration,
        )
    except Exception as exc:
        await finish_ai_validation_run(
            settings=settings,
            run_id=decision.run_id,
            status="FAILED",
            error_type=type(exc).__name__,
        )
        raise

    await finish_ai_validation_run(
        settings=settings,
        run_id=decision.run_id,
        status="COMPLETED",
    )
    return prepared


async def main(
    *,
    if_missing: bool = False,
    validate_ai: bool = False,
    force_ai_validation: bool = False,
    refresh_ai_routes: bool = False,
    adopt_packaged: bool = False,
    adopt_packaged_keys: tuple[str, ...] = (),
) -> None:
    settings, resolver = await resolve_runtime_settings_from_vault(
        Settings(),
        resolve_ai_credentials=False,
    )
    # `resolve_runtime_settings_from_vault` returns no resolver for exactly one
    # reason: `PLATFORM_VAULT_ENABLED` is false, so the process was started with
    # its credentials already in the environment rather than behind references.
    # That is now the default and the only configuration this repository ships.
    #
    # Refusing outright would be wrong even so. This command is what
    # `runtime-configuration-init` runs, and every application service waits on
    # that init completing, so a refusal takes the whole profile down with it.
    #
    # Publishing needs no resolver: the release is built from the packaged YAML
    # and the active release's own payload. Only the AI validation paths do, and
    # `_require_secret_resolver` refuses those individually.
    if resolver is None:
        print("vault_secret_resolver=DISABLED reason=PLATFORM_VAULT_ENABLED-false")

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(
            settings.neo4j_user,
            settings.neo4j_password.get_secret_value(),
        ),
    )
    try:
        await driver.verify_connectivity()
        repository = Neo4jConfigurationGraphRepository(driver)
        active = await repository.get_active_release()

        if if_missing and active is not None and not validate_ai and not refresh_ai_routes:
            print(f"graph_configuration_release={active.release_id}")
            print("graph_configuration_status=EXISTING")
            print("ai_bootstrap_validation=SKIPPED reason=active-release-reused")
            return

        loaded = load_return_configuration(settings.return_configuration_path)
        loaded_ai_gateway = load_ai_gateway_configuration(settings.ai_gateway_configuration_path)
        loaded_dependency_simulation = load_dependency_simulation_configuration(
            settings.dependency_simulation_configuration_path
        )

        # The packaged files, as dicts -- `adopt_packaged_configuration` does no
        # file I/O of its own, so callers with no filesystem access to the
        # packaged directory (the API process) can run the identical decision.
        packaged_payload = loaded.configuration.model_dump(mode="json")
        # CFG-6: the env stays the bootstrap default for the `deployment`
        # section by being injected into the PACKAGED payload here, the way
        # `runtime_integrations` already is -- never into `settings` itself,
        # which this command never re-derives from a release (unlike the API
        # process). `settings` at this point is exactly the bootstrap Settings
        # `resolve_runtime_settings_from_vault` returned above, before any
        # release has touched it.
        packaged_payload["deployment"] = merge_deployment_defaults(
            packaged_payload.get("deployment", {}),
            deployment_payload_from_settings(settings),
        )
        packaged_domains: dict[str, dict[str, Any]] = {
            AI_GATEWAY_DOMAIN_KEY: loaded_ai_gateway.configuration.model_dump(mode="json"),
            DEPENDENCY_SIMULATION_DOMAIN_KEY: (
                loaded_dependency_simulation.configuration.model_dump(mode="json")
            ),
        }

        active_return_platform: dict[str, Any] | None = None
        active_domains: dict[str, dict[str, Any]] = {}
        active_metadata: dict[str, Any] = {}
        if active is not None:
            active_metadata = dict(active.metadata)
            active_return_platform = await repository.get_domain_config(
                active.release_id, RETURN_PLATFORM_DOMAIN_KEY
            )
            for domain_key in packaged_domains:
                domain_payload = await repository.get_domain_config(active.release_id, domain_key)
                if domain_payload is not None:
                    active_domains[domain_key] = domain_payload

        # The one decision: `configuration/application/packaged_adoption.py`.
        # Extracted so `POST /api/config/adopt-packaged` and
        # `GET /api/config/packaged-drift` run this exact computation rather
        # than a second copy of it -- see that module's own docstring.
        adoption = adopt_packaged_configuration(
            packaged_return_platform=packaged_payload,
            packaged_domains=packaged_domains,
            active_return_platform=active_return_platform,
            active_domains=active_domains,
            active_metadata=active_metadata,
            adopt_packaged=adopt_packaged,
            adopt_packaged_keys=adopt_packaged_keys,
            release_id=active.release_id if active is not None else None,
        )
        existing_configuration = adoption.existing_return_platform_configuration
        recordable_baseline = adoption.recordable_baseline
        recordable_domain_baselines = adoption.recordable_domain_baselines
        carried_domains: dict[str, dict[str, Any]] = {
            AI_GATEWAY_DOMAIN_KEY: adoption.merged_domains[AI_GATEWAY_DOMAIN_KEY],
            DEPENDENCY_SIMULATION_DOMAIN_KEY: adoption.merged_domains[
                DEPENDENCY_SIMULATION_DOMAIN_KEY
            ],
        }

        base_configuration = existing_configuration or loaded.configuration
        configuration = await _prepare_return_configuration(
            validate_ai=validate_ai,
            force_ai_validation=force_ai_validation,
            refresh_ai_routes=refresh_ai_routes,
            settings=settings,
            resolver=resolver,
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=base_configuration,
            existing_configuration=existing_configuration,
        )

        baseline_payload = configuration.model_dump(mode="json")
        domain_payloads = {
            RETURN_PLATFORM_DOMAIN_KEY: baseline_payload,
            AI_GATEWAY_DOMAIN_KEY: carried_domains[AI_GATEWAY_DOMAIN_KEY],
            DEPENDENCY_SIMULATION_DOMAIN_KEY: carried_domains[DEPENDENCY_SIMULATION_DOMAIN_KEY],
        }
        release_metadata = {
            PACKAGED_KEY_DIGESTS: recordable_baseline,
            PACKAGED_DOMAIN_KEY_DIGESTS: recordable_domain_baselines,
        }
        payload_checksum = hashlib.sha256(
            json.dumps(
                domain_payloads,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        base_release_id = f"return-platform-{payload_checksum[:16]}"
        release_id = base_release_id

        if active is not None:
            active_payloads = await repository.get_all_domain_configs(active.release_id)
            if active_payloads == domain_payloads:
                # Nothing to publish, but the baseline still moves: the packaged
                # file can change and leave the merged payload identical -- the
                # key it changed was one an operator had already edited, so the
                # release keeps its value. Recording what the file says NOW is
                # what keeps that key readable as an operator edit next time
                # instead of drifting back into "changed by someone, unknown".
                if recordable_baseline or any(recordable_domain_baselines.values()):
                    await repository.set_release_metadata(active.release_id, release_metadata)
                print(f"graph_configuration_release={active.release_id}")
                print("graph_configuration_status=UNCHANGED")
                return

        existing = await repository.get_release(release_id)
        if existing is not None and existing.status in {"SUPERSEDED", "ARCHIVED"}:
            revision = await repository.get_head_revision() + 1
            while True:
                release_id = f"{base_release_id}-r{revision}"
                existing = await repository.get_release(release_id)
                if existing is None or existing.status not in {"SUPERSEDED", "ARCHIVED"}:
                    break
                revision += 1

        if existing is None or existing.status == "DRAFT":
            for domain_key, domain_payload in domain_payloads.items():
                await repository.save_draft_domain(
                    release_id,
                    domain_key,
                    domain_payload,
                    actor_id="linux-runtime-bootstrap",
                )
            existing = await repository.get_release(release_id)

        if existing is not None and existing.status == "DRAFT":
            await repository.promote_release(
                release_id,
                "VALIDATED",
                actor_id="linux-runtime-bootstrap",
            )
            existing = await repository.get_release(release_id)

        if existing is None:
            raise RuntimeError(f"Configuration release {release_id} was not created")

        if existing.status == "VALIDATED":
            await repository.promote_release(
                release_id,
                "RELEASED",
                actor_id="linux-runtime-bootstrap",
                expected_head_revision=(await repository.get_head_revision()),
            )
        elif existing.status != "RELEASED":
            raise RuntimeError(
                f"Existing graph configuration release {release_id} has status {existing.status}"
            )

        # The baseline this release was built from, recorded on the release
        # itself so the NEXT publish can tell an operator's edit apart from a
        # change to the packaged file. Digests of the PACKAGED values, never of
        # the published ones: what is published also carries state this function
        # generates -- AI receipts under `runtime_integrations` -- and recording
        # those as the baseline would mark them unedited and let the next run
        # overwrite them from the file.
        #
        # After the release, because metadata sits outside the checksum that is
        # frozen at VALIDATED. A publish that reached RELEASED and failed here
        # leaves a correct release with no baseline, which is the recoverable
        # direction: the next run decides nothing and says so.
        if recordable_baseline or any(recordable_domain_baselines.values()):
            await repository.set_release_metadata(release_id, release_metadata)

        print(f"graph_configuration_release={release_id}")
        print("graph_configuration_status=READY")
    finally:
        await driver.close()


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help=("Publish bootstrap configuration only when no active release exists."),
    )
    parser.add_argument(
        "--validate-ai",
        action="store_true",
        help=("Run live provider/model validation when the daily validation interval has elapsed."),
    )
    parser.add_argument(
        "--force-ai-validation",
        action="store_true",
        help=("Bypass the daily validation interval. This is an operator-only action."),
    )
    parser.add_argument(
        "--refresh-ai-routes",
        action="store_true",
        help=(
            "Publish all configured provider/model/credential routes without calling AI providers."
        ),
    )
    parser.add_argument(
        "--adopt-packaged",
        action="store_true",
        help=(
            "Take the packaged configuration files for every key and unit they "
            "declare -- business keys, every AI task and limit, every simulated "
            "dependency -- overwriting the active release. Needed only for a release published "
            "before releases recorded a packaged baseline: without one, a publish "
            "cannot tell an operator's edit from a change to the file and keeps the "
            "release. This and --adopt-packaged-key are the only paths that can "
            "overwrite an operator's edits; prefer the per-key form."
        ),
    )
    parser.add_argument(
        "--adopt-packaged-key",
        action="append",
        default=[],
        metavar="KEY",
        help=(
            "Take the packaged configuration file for ONE unit (repeatable), "
            "overwriting the active release for that unit only: a business key "
            "such as `discovery`, or `AI_GATEWAY/tasks.<TASK_ID>`, "
            "`DEPENDENCY_SIMULATION/dependencies.<NAME>`. "
            "The narrow form of --adopt-packaged: answers a "
            "packaged_configuration_not_adopted warning for the key it names "
            "without touching any key an operator edited."
        ),
    )
    args = parser.parse_args()

    if args.force_ai_validation:
        args.validate_ai = True
    if args.refresh_ai_routes and args.validate_ai:
        parser.error("--refresh-ai-routes cannot be combined with AI validation")

    asyncio.run(
        main(
            if_missing=args.if_missing,
            validate_ai=args.validate_ai,
            force_ai_validation=args.force_ai_validation,
            refresh_ai_routes=args.refresh_ai_routes,
            adopt_packaged=args.adopt_packaged,
            adopt_packaged_keys=tuple(args.adopt_packaged_key),
        )
    )


if __name__ == "__main__":
    run()
