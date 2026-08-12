"""Run the Order Discovery workflow worker as a dedicated Docker process."""

import asyncio
import socket
import uuid

from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.ai.interception.store import SystemStoreInterceptionStore
from return_platform.ai.providers.replay_store import MongoReplayStore
from return_platform.ai_gateway.routing import AIRoutePool, build_routes
from return_platform.bootstrap.system_store import bootstrap_system_store
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.data_platform.graph.sync_service import MongoTargetedSyncRunLedger
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.integration.runtime_factory import (
    build_dynamic_order_agent_runtime,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.workflows.order_discovery_activities import OrderDiscoveryActivities
from return_platform.workflows.order_discovery_worker import create_order_discovery_worker


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    platform_mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    source_mongo: AsyncMongoClient[dict[str, object]] = (
        platform_mongo
        if source_dsn == settings.mongo_dsn.get_secret_value()
        else AsyncMongoClient[dict[str, object]](source_dsn)
    )
    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        await platform_mongo.admin.command("ping")
        if source_mongo is not platform_mongo:
            await source_mongo.admin.command("ping")
        await neo4j_driver.verify_connectivity()

        system_store, envelope_encryptor = await bootstrap_system_store(settings, platform_mongo)
        # This is the process that actually runs MANUAL reasoning turns, and it
        # already has a SystemStore before routes are built -- so MANUAL resolves
        # to the durable interception store here rather than to
        # ManualFileProvider's `.manual_llm/` directory, which would be relative
        # to whatever CWD the worker container happened to start in.
        replay_store = MongoReplayStore(platform_mongo, settings.mongo_database)
        await replay_store.ensure_indexes()
        route_pool = AIRoutePool(
            build_routes(
                settings,
                interception_store=SystemStoreInterceptionStore(system_store, envelope_encryptor),
                replay_store=replay_store,
            ),
            runtime.ai_gateway_configuration.configuration,
        )
        coordinator = await build_dynamic_order_agent_runtime(
            settings=settings,
            platform_mongo=platform_mongo,
            source_mongo=source_mongo,
            neo4j_driver=neo4j_driver,
            ai_gateway_configuration=runtime.ai_gateway_configuration,
            route_pool=route_pool,
            system_store=system_store,
            reasoning_encryptor=envelope_encryptor,
            # Composed here rather than inside the factory: the ledger writes a
            # `data_platform` collection and `dynamic_knowledge` does not import
            # that package. Without it a targeted sync still runs and still
            # records its receipt -- it just never appears on the sync screen,
            # which is how an agent-initiated write to the graph stayed
            # invisible to operators.
            targeted_sync_runs=MongoTargetedSyncRunLedger(platform_mongo, settings.mongo_database),
        )
        schema = load_active_schema(settings.dynamic_knowledge_schema_path)
        activities = OrderDiscoveryActivities(coordinator=coordinator, schema=schema)

        temporal = await Client.connect(settings.temporal_target)
        worker = create_order_discovery_worker(temporal, activities)
        operational_repository = OperationalRepository(platform_mongo, settings)
        instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

        async def heartbeat() -> None:
            while True:
                await operational_repository.heartbeat(
                    "order-discovery-worker",
                    instance_id,
                    ttl_seconds=settings.worker_readiness_ttl_seconds,
                )
                await asyncio.sleep(max(1.0, settings.worker_readiness_ttl_seconds / 3))

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            await worker.run()
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
    finally:
        await neo4j_driver.close()
        if source_mongo is not platform_mongo:
            await source_mongo.close()
        await platform_mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
