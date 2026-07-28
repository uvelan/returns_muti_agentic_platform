"""Publish the validated baseline as the first graph configuration release."""

from __future__ import annotations

import asyncio
import hashlib
import json

from neo4j import AsyncGraphDatabase

from return_platform.ai_gateway.configuration import load_ai_gateway_configuration
from return_platform.configuration.bootstrap_runtime_integrations import (
    build_bootstrap_runtime_configuration,
)
from return_platform.configuration.graph_repository import Neo4jConfigurationGraphRepository
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import RETURN_PLATFORM_DOMAIN_KEY
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault


async def main() -> None:
    settings, resolver = await resolve_runtime_settings_from_vault(
        Settings(),
        resolve_ai_credentials=False,
    )
    if resolver is None:
        raise RuntimeError("Runtime bootstrap requires the Vault secret resolver")
    loaded = load_return_configuration(settings.return_configuration_path)
    loaded_ai_gateway = load_ai_gateway_configuration(settings.ai_gateway_configuration_path)
    configuration = await build_bootstrap_runtime_configuration(
        settings=settings,
        resolver=resolver,
        loaded_ai_gateway=loaded_ai_gateway,
        configuration=loaded.configuration,
    )
    baseline_payload = configuration.model_dump(mode="json")
    payload_checksum = hashlib.sha256(
        json.dumps(baseline_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    base_release_id = f"return-platform-{payload_checksum[:16]}"
    release_id = base_release_id
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        await driver.verify_connectivity()
        repository = Neo4jConfigurationGraphRepository(driver)
        active = await repository.get_active_release()
        if active is not None:
            active_payload = await repository.get_domain_config(
                active.release_id,
                RETURN_PLATFORM_DOMAIN_KEY,
            )
            if active_payload == baseline_payload:
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
        if existing is None:
            await repository.save_draft_domain(
                release_id,
                RETURN_PLATFORM_DOMAIN_KEY,
                baseline_payload,
                actor_id="linux-runtime-bootstrap",
            )
            await repository.promote_release(
                release_id,
                "VALIDATED",
                actor_id="linux-runtime-bootstrap",
            )
            existing = await repository.get_release(release_id)

        if existing is None:
            raise RuntimeError(f"Configuration release {release_id} was not created")
        if existing.status == "DRAFT":
            await repository.promote_release(
                release_id,
                "VALIDATED",
                actor_id="linux-runtime-bootstrap",
            )
            existing = await repository.get_release(release_id)
        if existing is not None and existing.status == "VALIDATED":
            await repository.promote_release(
                release_id,
                "RELEASED",
                actor_id="linux-runtime-bootstrap",
                expected_head_revision=await repository.get_head_revision(),
            )
        elif existing is None or existing.status != "RELEASED":
            raise RuntimeError(
                "Existing graph configuration release "
                f"{release_id} has status {existing.status if existing is not None else 'MISSING'}"
            )
        print(f"graph_configuration_release={release_id}")
        print("graph_configuration_status=READY")
    finally:
        await driver.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
