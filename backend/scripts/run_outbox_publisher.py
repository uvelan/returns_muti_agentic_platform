"""Publish authoritative MongoDB events to Valkey Streams.

Runs the configuration reconciler alongside the flush loop so a released change
to the stream retention or the readiness TTL reaches this process without a
restart, and so it has an adopted release to report (T-16). The Valkey endpoint
itself is restart-required and the reconciler refuses a release that moves it.
"""

import asyncio
import socket
import uuid
from typing import cast

import redis.asyncio as redis
from pymongo import AsyncMongoClient

from return_platform.configuration.runtime_activation import build_worker_runtime_activation
from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.operations.events import flush_outbox
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import AsyncValkeyClient

_PROCESS_CLASS = "outbox-publisher"


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    valkey = redis.Redis(
        host=settings.valkey_host,
        port=settings.valkey_port,
        password=settings.valkey_password.get_secret_value(),
        decode_responses=True,
    )
    client = cast(AsyncValkeyClient, valkey)
    repository = OperationalRepository(mongo, settings)
    instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    activation = await build_worker_runtime_activation(
        runtime=runtime,
        process_class=_PROCESS_CLASS,
        instance_id=instance_id,
        mongo=mongo,
    )
    state = activation.state
    activation_tasks = activation.start()
    try:
        await verify_runtime_validation_receipts(
            mongo,
            settings.mongo_database,
            runtime.return_configuration.configuration,
        )
        await repository.ensure_indexes()
        while True:
            # One read per flush: a batch already being published keeps the
            # retention it started under, and the next batch adopts the new one.
            active = state.settings
            # The heartbeat used to be written here, inline in the flush loop --
            # so a slow flush stalled it and a healthy worker reported stale.
            # It runs on its own task now, started by `activation.start()`:
            # liveness must not be hostage to the thing whose liveness it
            # reports.
            published = await flush_outbox(
                client,
                repository,
                maxlen=active.event_stream_retention,
            )
            if published == 0:
                await asyncio.sleep(0.5)
    finally:
        for task in activation_tasks:
            task.cancel()
        await asyncio.gather(*activation_tasks, return_exceptions=True)
        await activation.aclose()
        await client.aclose()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
