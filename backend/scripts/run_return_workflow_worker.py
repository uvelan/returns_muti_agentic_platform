"""Run the Return workflow worker as a dedicated Docker process."""

import asyncio

from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.settings import Settings
from return_platform.workflows.persistence import ReturnSessionRepository
from return_platform.workflows.worker import create_return_workflow_worker

_SESSIONS_COLLECTION = "return_sessions"
_AUDITS_COLLECTION = "return_session_audit_events"
_OUTBOX_COLLECTION = "return_session_outbox_events"
_DECISIONS_COLLECTION = "return_session_agent_decisions"
_PERSISTENCE_TIMEOUT_SECONDS = 5.0


async def _run() -> None:
    settings = Settings()
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    try:
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
        await worker.run()
    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
