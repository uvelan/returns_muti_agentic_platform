"""Run the Return workflow worker as a dedicated Docker process."""

import asyncio
import socket
import uuid

from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.operations.repository import OperationalRepository
from return_platform.workflows.persistence import ReturnSessionRepository
from return_platform.workflows.worker import create_return_workflow_worker

_SESSIONS_COLLECTION = "return_sessions"
_AUDITS_COLLECTION = "return_session_audit_events"
_OUTBOX_COLLECTION = "return_session_outbox_events"
_DECISIONS_COLLECTION = "return_session_agent_decisions"
_PERSISTENCE_TIMEOUT_SECONDS = 5.0


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    try:
        await verify_runtime_validation_receipts(
            mongo,
            settings.mongo_database,
            runtime.return_configuration.configuration,
        )
        temporal = await Client.connect(settings.temporal_target)
        repository = ReturnSessionRepository.from_client(
            mongo,
            database=settings.mongo_database,
            sessions_collection=_SESSIONS_COLLECTION,
            audits_collection=_AUDITS_COLLECTION,
            outbox_collection=_OUTBOX_COLLECTION,
            decisions_collection=_DECISIONS_COLLECTION,
            operation_timeout_seconds=_PERSISTENCE_TIMEOUT_SECONDS,
        )
        worker = create_return_workflow_worker(temporal, repository)
        operational_repository = OperationalRepository(mongo, settings)
        instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

        async def heartbeat() -> None:
            while True:
                await operational_repository.heartbeat(
                    "return-workflow-worker",
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
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
