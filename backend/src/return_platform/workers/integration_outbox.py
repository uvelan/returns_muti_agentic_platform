"""Run the external-integration outbox worker."""

from __future__ import annotations

import asyncio

import httpx
from pymongo import AsyncMongoClient

from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.dependency_simulation.configuration import (
    load_dependency_simulation_configuration,
)
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


async def run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    loaded_simulation = load_dependency_simulation_configuration(
        settings.dependency_simulation_configuration_path
    )
    simulation_repository = MongoSimulationRepository(client, settings)
    await verify_runtime_validation_receipts(
        client,
        settings.mongo_database,
        runtime.return_configuration.configuration,
    )
    await simulation_repository.ensure_indexes()
    simulation_service = DependencySimulationService(
        simulation_repository, settings, loaded_simulation
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
        try:
            await worker.run_forever()
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(run())
