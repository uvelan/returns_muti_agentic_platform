"""Publish the validated baseline as the first graph configuration release."""

from __future__ import annotations

import asyncio

from neo4j import AsyncGraphDatabase

from return_platform.configuration.graph_repository import Neo4jConfigurationGraphRepository
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import RETURN_PLATFORM_DOMAIN_KEY
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault


async def main() -> None:
    settings, _resolver = await resolve_runtime_settings_from_vault(Settings())
    loaded = load_return_configuration(settings.return_configuration_path)
    release_id = f"return-platform-{loaded.sha256[:16]}"
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        await driver.verify_connectivity()
        repository = Neo4jConfigurationGraphRepository(driver)
        active = await repository.get_active_release()
        baseline_payload = loaded.configuration.model_dump(mode="json")
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
        if existing.status == "VALIDATED":
            await repository.promote_release(
                release_id,
                "RELEASED",
                actor_id="linux-runtime-bootstrap",
                expected_head_revision=await repository.get_head_revision(),
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
    asyncio.run(main())


if __name__ == "__main__":
    run()
