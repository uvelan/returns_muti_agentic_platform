"""Run durable Data Console jobs as a dedicated worker process."""

import asyncio
import socket
import uuid

from pymongo import AsyncMongoClient

from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.data_console.api.jobs import JobService
from return_platform.operations.repository import OperationalRepository


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    jobs = JobService(mongo, settings.mongo_database)
    operations = OperationalRepository(mongo, settings)
    try:
        await verify_runtime_validation_receipts(
            mongo,
            settings.mongo_database,
            runtime.return_configuration.configuration,
        )
        await jobs.ensure_indexes()
        await operations.ensure_indexes()
        while True:
            await operations.heartbeat(
                "data-job-worker",
                worker_id,
                ttl_seconds=settings.worker_readiness_ttl_seconds,
            )
            job = await jobs.claim_next(worker_id)
            if job is None:
                await asyncio.sleep(settings.orchestration_poll_seconds)
                continue
            try:
                await jobs.process_claimed(job, worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # process_claimed records a sanitized failure before re-raising.
                await asyncio.sleep(settings.orchestration_poll_seconds)
    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
