"""Publish authoritative MongoDB events to Valkey Streams."""

import asyncio
import socket
import uuid
from typing import cast

import redis.asyncio as redis
from pymongo import AsyncMongoClient

from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.operations.events import flush_outbox
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import AsyncValkeyClient


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
    try:
        await verify_runtime_validation_receipts(
            mongo,
            settings.mongo_database,
            runtime.return_configuration.configuration,
        )
        await repository.ensure_indexes()
        while True:
            await repository.heartbeat(
                "outbox-publisher",
                instance_id,
                ttl_seconds=settings.worker_readiness_ttl_seconds,
            )
            published = await flush_outbox(
                client,
                repository,
                maxlen=settings.event_stream_retention,
            )
            if published == 0:
                await asyncio.sleep(0.5)
    finally:
        await client.aclose()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
