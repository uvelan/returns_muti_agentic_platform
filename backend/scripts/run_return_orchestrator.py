"""Run the E2E return orchestrator as a dedicated process."""

import asyncio
import socket
import uuid

from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.operations.orchestrator import ReturnOrchestrator
from return_platform.operations.repository import OperationalRepository


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    platform_dsn = settings.mongo_dsn.get_secret_value()
    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else platform_dsn
    )
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(platform_dsn)
    source_mongo = mongo if source_dsn == platform_dsn else AsyncMongoClient(source_dsn)
    try:
        await verify_runtime_validation_receipts(
            mongo,
            settings.mongo_database,
            runtime.return_configuration.configuration,
        )
        temporal = await Client.connect(settings.temporal_target)
        repository = OperationalRepository(mongo, settings, source_mongo)
        worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        orchestrator = ReturnOrchestrator(
            repository=repository,
            temporal=temporal,
            settings=settings,
            worker_id=worker_id,
        )
        await orchestrator.run_forever()
    finally:
        if source_mongo is not mongo:
            await source_mongo.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
