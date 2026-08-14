"""Run the housekeeping worker as a dedicated Docker process.

Reclaims the three debris classes measured on this platform: orphaned Temporal
executions stranded on queues nothing polls, RETIRED graph generations whose
nodes retirement deliberately leaves behind, and the `*_probe` SQL Server
databases test fixtures create and never drop. The rules live in
`return_platform.housekeeping`; this script is composition and the loop.

Like every other worker here it hot-adopts configuration through
`RuntimeConfigurationActivator` and reports adoption (contract C5) so an operator
can see which release it is running. `housekeeping-worker` is deliberately **not**
in `REQUIRED_PROCESS_CLASSES`: two of its three reclaimers are gated to
development and test, so this service is not deployed everywhere, and a required
class that is not always deployed leaves every release at ACTIVATING forever.

The reclaimers are rebuilt from the activated configuration on every pass rather
than held from startup, so a released change to a retention window takes effect
here without a restart -- and, more importantly, so the construction-time safety
checks re-run against the release that is actually live.
"""

import asyncio
import socket
import uuid

from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.return_configuration import HousekeepingConfiguration
from return_platform.configuration.runtime_activation import build_worker_runtime_activation
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.configuration.settings import Settings
from return_platform.housekeeping.composition import build_housekeeping_cycle
from return_platform.housekeeping.cycle import run_housekeeping_loop
from return_platform.operations.repository import OperationalRepository

_PROCESS_CLASS = "housekeeping-worker"


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        await mongo.admin.command("ping")
        await neo4j_driver.verify_connectivity()
        temporal = await Client.connect(settings.temporal_target)

        repository = OperationalRepository(mongo, settings)
        instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        activation = await build_worker_runtime_activation(
            runtime=runtime,
            process_class=_PROCESS_CLASS,
            instance_id=instance_id,
            mongo=mongo,
            neo4j_driver=neo4j_driver,
        )
        state = activation.state

        def current_settings() -> Settings:
            # Off the activated state, not the startup `settings`: the activator
            # replaces this field when a release lands, and reading the boot
            # value would pin the protected task-queue set to whatever it was at
            # container start.
            return state.settings

        def current_configuration() -> HousekeepingConfiguration:
            return state.return_configuration.configuration.housekeeping

        cycle = build_housekeeping_cycle(
            settings_provider=current_settings,
            configuration_provider=current_configuration,
            temporal_client=temporal,
            mongo=mongo,
            neo4j_driver=neo4j_driver,
        )

        async def heartbeat() -> None:
            while True:
                await repository.heartbeat(
                    _PROCESS_CLASS,
                    instance_id,
                    ttl_seconds=current_settings().worker_readiness_ttl_seconds,
                )
                await asyncio.sleep(max(1.0, current_settings().worker_readiness_ttl_seconds / 3))

        background = (asyncio.create_task(heartbeat()), *activation.start())
        try:
            await run_housekeeping_loop(
                cycle,
                interval_seconds=lambda: float(current_configuration().interval_seconds),
            )
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await activation.aclose()
    finally:
        await neo4j_driver.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
