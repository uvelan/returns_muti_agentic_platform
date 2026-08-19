"""Publish the runtime configuration as an active graph release."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging

from neo4j import AsyncGraphDatabase
from pydantic import ValidationError

from return_platform.ai.routing.tasks import (
    LoadedAIGatewayConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.configuration.bootstrap_runtime_integrations import (
    begin_ai_validation_run,
    build_bootstrap_runtime_configuration,
    build_configured_runtime_configuration,
    finish_ai_validation_run,
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

        existing_configuration: ReturnPlatformConfiguration | None = None
        if active is not None:
            active_payload = await repository.get_domain_config(
                active.release_id,
                RETURN_PLATFORM_DOMAIN_KEY,
            )
            if active_payload is not None:
                # Carrying the active release's operator values forward is the
                # point of this block -- but only while that release still
                # validates. Unguarded, this was a deadlock: a release published
                # before a schema change (a removed key, or one that became
                # required) fails validation here, so no NEW release can be
                # published, so the stale release stays active. The only tool
                # that could repair it was blocked by the thing it repairs.
                #
                # Observed exactly that way: the active release predated both the
                # removal of `agents.*.failure_policy` and the addition of the
                # now-required `return_policy.bol_tendering_instruction_types`,
                # and failed with seven validation errors. Every process
                # independently rejected the same release and fell back to the
                # version-controlled baseline, so adoption reported ACTIVATING
                # forever with nothing able to move it.
                #
                # Falling back to the packaged YAML is the recoverable answer,
                # and it is loud rather than silent: the operator values in that
                # release ARE being dropped, which is a real loss and must be
                # read, not discovered later.
                #
                # The packaged document underneath is the second half of the
                # same problem, and it was missing. A key added to
                # `config/returns/production.yaml` after the active release was
                # cut is simply not in `active_payload`, so validating that
                # payload alone produced a configuration holding the *model*
                # default for the new key -- and republishing wrote that default
                # back. The new setting could never reach a deployment that had
                # ever published a release, which is every deployment.
                #
                # Observed with `copilot.order_discovery_agent_id`: the value was
                # in the YAML, the endpoint that serves it was correct, and
                # `/api/runtime-config` still answered `null`.
                #
                # A top-level merge, at the same granularity the rest of this
                # function works at. Every key the release carries wins, so an
                # operator's edits are still authoritative; keys it predates come
                # from the packaged file. A release already carrying every key
                # merges to itself and still reports UNCHANGED.
                merged_payload = {
                    **loaded.configuration.model_dump(mode="json"),
                    **active_payload,
                }
                try:
                    existing_configuration = ReturnPlatformConfiguration.model_validate(
                        merged_payload
                    )
                except ValidationError as error:
                    logger.error(
                        "active_release_no_longer_validates release_id=%s errors=%d; "
                        "falling back to the packaged configuration. Operator values "
                        "carried by that release are NOT preserved -- re-apply them "
                        "after this publish. Detail: %s",
                        active.release_id,
                        error.error_count(),
                        error,
                    )

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
            AI_GATEWAY_DOMAIN_KEY: (loaded_ai_gateway.configuration.model_dump(mode="json")),
            DEPENDENCY_SIMULATION_DOMAIN_KEY: (
                loaded_dependency_simulation.configuration.model_dump(mode="json")
            ),
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
        )
    )


if __name__ == "__main__":
    run()
