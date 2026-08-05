"""Publish the validated baseline as the first graph configuration release."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json

from neo4j import AsyncGraphDatabase

from return_platform.ai_gateway.configuration import (
    LoadedAIGatewayConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.configuration.bootstrap_runtime_integrations import (
    build_bootstrap_runtime_configuration,
)
from return_platform.configuration.graph_repository import Neo4jConfigurationGraphRepository
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
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault
from return_platform.secrets.vault import SecretResolver


async def _prepare_return_configuration(
    *,
    validate_ai: bool,
    settings: Settings,
    resolver: SecretResolver,
    loaded_ai_gateway: LoadedAIGatewayConfiguration,
    configuration: ReturnPlatformConfiguration,
) -> ReturnPlatformConfiguration:
    if not validate_ai:
        print("ai_bootstrap_validation=SKIPPED reason=explicit-parameter-required")
        return configuration
    return await build_bootstrap_runtime_configuration(
        settings=settings,
        resolver=resolver,
        loaded_ai_gateway=loaded_ai_gateway,
        configuration=configuration,
    )


async def main(*, if_missing: bool = False, validate_ai: bool = False) -> None:
    settings, resolver = await resolve_runtime_settings_from_vault(
        Settings(),
        resolve_ai_credentials=False,
    )
    if resolver is None:
        raise RuntimeError("Runtime bootstrap requires the Vault secret resolver")
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        await driver.verify_connectivity()
        repository = Neo4jConfigurationGraphRepository(driver)
        active = await repository.get_active_release()
        if if_missing and active is not None and not validate_ai:
            print(f"graph_configuration_release={active.release_id}")
            print("graph_configuration_status=EXISTING")
            print("ai_bootstrap_validation=SKIPPED reason=active-release-reused")
            return
        loaded = load_return_configuration(settings.return_configuration_path)
        loaded_ai_gateway = load_ai_gateway_configuration(settings.ai_gateway_configuration_path)
        loaded_dependency_simulation = load_dependency_simulation_configuration(
            settings.dependency_simulation_configuration_path
        )
        configuration = await _prepare_return_configuration(
            validate_ai=validate_ai,
            settings=settings,
            resolver=resolver,
            loaded_ai_gateway=loaded_ai_gateway,
            configuration=loaded.configuration,
        )
        baseline_payload = configuration.model_dump(mode="json")
        domain_payloads = {
            RETURN_PLATFORM_DOMAIN_KEY: baseline_payload,
            AI_GATEWAY_DOMAIN_KEY: loaded_ai_gateway.configuration.model_dump(mode="json"),
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
                expected_head_revision=await repository.get_head_revision(),
            )
        elif existing.status != "RELEASED":
            raise RuntimeError(
                "Existing graph configuration release "
                f"{release_id} has status {existing.status}"
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
        help="Publish bootstrap configuration only when no active release exists.",
    )
    parser.add_argument(
        "--validate-ai",
        action="store_true",
        help="Run live provider/model validation before publishing configuration.",
    )
    args = parser.parse_args()
    asyncio.run(main(if_missing=args.if_missing, validate_ai=args.validate_ai))


if __name__ == "__main__":
    run()
