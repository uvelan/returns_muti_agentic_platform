"""Run the external-integration outbox worker.

A deployed long-running process (`compose.yaml`), and therefore one that has to
reconcile configuration and report the release it adopted like the rest (T-16,
contract C5). It was missed by the first pass because it is a module rather than
a `scripts/run_*.py` entry point, which is exactly the way a process class goes
unnoticed -- it is not in the directory anyone looks at.
"""

from __future__ import annotations

import asyncio
import socket
import uuid

import httpx
from pymongo import AsyncMongoClient

from return_platform.configuration.runtime_activation import build_worker_runtime_activation
from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.dependency_simulation.dispatchers import SimulationTopicDispatcher
from return_platform.dependency_simulation.models import DependencyKind
from return_platform.dependency_simulation.repository import MongoSimulationRepository
from return_platform.dependency_simulation.service import DependencySimulationService
from return_platform.operations.integrations.outbox import (
    HttpJsonDispatcher,
    HttpTicketDispatcher,
    IntegrationOutboxDispatcher,
    TopicDispatcher,
)

_PROCESS_CLASS = "integration-outbox-worker"


async def run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    loaded_simulation = runtime.dependency_simulation_configuration
    simulation_repository = MongoSimulationRepository(client, settings)
    await verify_runtime_validation_receipts(
        client,
        settings.mongo_database,
        runtime.return_configuration.configuration,
    )
    await simulation_repository.ensure_indexes()
    simulation_service = DependencySimulationService(
        simulation_repository,
        settings,
        loaded_simulation,
        loaded_ai_gateway=runtime.ai_gateway_configuration,
    )
    async with httpx.AsyncClient() as http_client:
        dispatchers: dict[str, TopicDispatcher] = {}
        if (
            settings.support_ticket_mode == "INTERNAL_WITH_EXTERNAL_MIRROR"
            and settings.support_ticket_base_url is not None
        ):
            dispatchers["return-support.ticket.create"] = HttpTicketDispatcher(
                settings,
                http_client,
            )
        if settings.omc_dependency_mode == "SIMULATED":
            dispatchers["omc.return.create"] = SimulationTopicDispatcher(
                simulation_service, DependencyKind.OMC, "CREATE_RMA"
            )
        elif settings.omc_command_base_url is not None:
            dispatchers["omc.return.create"] = HttpJsonDispatcher(
                base_url=settings.omc_command_base_url,
                resource_path="commands/returns",
                client=http_client,
                timeout_seconds=settings.operation_timeout_seconds,
                api_key=(
                    settings.omc_command_api_key.get_secret_value()
                    if settings.omc_command_api_key is not None
                    else None
                ),
            )
        if settings.freight_dependency_mode == "SIMULATED":
            dispatchers["carrier.return.book"] = SimulationTopicDispatcher(
                simulation_service, DependencyKind.FREIGHT, "CONFIRM_BOOKING"
            )
        elif settings.carrier_booking_base_url is not None:
            dispatchers["carrier.return.book"] = HttpJsonDispatcher(
                base_url=settings.carrier_booking_base_url,
                resource_path="return-bookings",
                client=http_client,
                timeout_seconds=settings.operation_timeout_seconds,
                api_key=(
                    settings.carrier_booking_api_key.get_secret_value()
                    if settings.carrier_booking_api_key is not None
                    else None
                ),
            )
        if settings.customer_notification_base_url is not None:
            dispatchers["customer.return.notify"] = HttpJsonDispatcher(
                base_url=settings.customer_notification_base_url,
                resource_path="return-notifications",
                client=http_client,
                timeout_seconds=settings.operation_timeout_seconds,
                api_key=(
                    settings.customer_notification_api_key.get_secret_value()
                    if settings.customer_notification_api_key is not None
                    else None
                ),
            )
        worker = IntegrationOutboxDispatcher(client, settings, dispatchers)
        activation = await build_worker_runtime_activation(
            runtime=runtime,
            process_class=_PROCESS_CLASS,
            instance_id=f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}",
            mongo=client,
        )
        activation_tasks = activation.start()
        try:
            await worker.run_forever()
        finally:
            for task in activation_tasks:
                task.cancel()
            await asyncio.gather(*activation_tasks, return_exceptions=True)
            await activation.aclose()
            await client.close()


if __name__ == "__main__":
    asyncio.run(run())
