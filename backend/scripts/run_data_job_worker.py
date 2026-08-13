"""Run durable Data Console jobs as a dedicated worker process.

Runs the configuration reconciler alongside the claim loop, so a released
change to the poll interval or the readiness TTL takes effect here without a
restart -- and so this process has an adopted release to report (T-16). It has
no Neo4j client of its own, so the reconciler opens and owns one.

A job already claimed keeps running under the settings the loop read when it
claimed it; the next claim reads the activated ones.
"""

import asyncio
import socket
import uuid

from pymongo import AsyncMongoClient
from return_platform.data_console.api.jobs import JobService

from return_platform.configuration.runtime_activation import (
    build_worker_runtime_activation,
    run_runtime_activation_loop,
)
from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.operations.repository import OperationalRepository

_PROCESS_CLASS = "data-job-worker"


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    jobs = JobService(mongo, settings.mongo_database)
    operations = OperationalRepository(mongo, settings)
    activation = await build_worker_runtime_activation(
        runtime=runtime,
        process_class=_PROCESS_CLASS,
        instance_id=worker_id,
        mongo=mongo,
    )
    state = activation.state
    activation_task = asyncio.create_task(run_runtime_activation_loop(activation.activator))
    try:
        await verify_runtime_validation_receipts(
            mongo,
            settings.mongo_database,
            runtime.return_configuration.configuration,
        )
        await jobs.ensure_indexes()
        await operations.ensure_indexes()
        while True:
            # Re-read per iteration: this is the process's activation boundary,
            # and a claim in flight is unaffected by a swap that lands mid-job.
            active = state.settings
            await operations.heartbeat(
                _PROCESS_CLASS,
                worker_id,
                ttl_seconds=active.worker_readiness_ttl_seconds,
            )
            job = await jobs.claim_next(worker_id)
            if job is None:
                await asyncio.sleep(active.orchestration_poll_seconds)
                continue
            try:
                await jobs.process_claimed(job, worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # process_claimed records a sanitized failure before re-raising.
                await asyncio.sleep(active.orchestration_poll_seconds)
    finally:
        activation_task.cancel()
        await asyncio.gather(activation_task, return_exceptions=True)
        await activation.aclose()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
