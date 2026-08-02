"""Apply and validate the canonical cross-store E2E seed manifest."""

from __future__ import annotations

import asyncio
import json

from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.data_platform.schema_registry import load_schema_registry
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.seed_control import SeedOperationControl
from return_platform.operations.seed_coordinator import SeedCoordinator
from return_platform.operations.seed_manifest import effective_seed_counts
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault


async def _run() -> None:
    settings, _secret_resolver = await resolve_runtime_settings_from_vault(
        Settings(),
        resolve_ai_credentials=False,
    )
    platform_dsn = settings.mongo_dsn.get_secret_value()
    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else platform_dsn
    )
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(platform_dsn)
    source_mongo = mongo if source_dsn == platform_dsn else AsyncMongoClient(source_dsn)
    neo4j = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        repository = OperationalRepository(mongo, settings, source_mongo)
        await repository.ensure_indexes()
        coordinator = SeedCoordinator(
            repository,
            SQLBusinessStateRepository(settings),
            neo4j,
            settings,
            load_schema_registry(settings.schema_registry_path),
        )
        record_limit = effective_seed_counts()["orders"]
        counts = effective_seed_counts(record_limit)
        control = SeedOperationControl()
        operation_id = await control.begin(
            kind="APPLY",
            record_limit=record_limit,
            total_records=(
                (2 * counts["customers"])
                + (2 * counts["products"])
                + (3 * counts["orders"])
            ),
        )
        status = await coordinator.apply(
            "seed-runner",
            record_limit=record_limit,
            control=control,
            operation_id=operation_id,
        )
        print(json.dumps(status.model_dump(mode="json"), separators=(",", ":"), sort_keys=True))
        if not status.ready:
            raise RuntimeError("Cross-store seed validation failed.")
    finally:
        await neo4j.close()
        if source_mongo is not mongo:
            await source_mongo.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
