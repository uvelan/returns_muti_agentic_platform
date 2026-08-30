"""Run the external-integration outbox worker.

A deployed long-running process (`compose.yaml`), and therefore one that has to
reconcile configuration and report the release it adopted like the rest (T-16,
contract C5). It was missed by the first pass because it is a module rather than
a `scripts/run_*.py` entry point, which is exactly the way a process class goes
unnoticed -- it is not in the directory anyone looks at.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid

import httpx
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.runtime_activation import build_worker_runtime_activation
from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import (
    ResolvedProcessConfiguration,
    resolve_process_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.dependency_simulation.dispatchers import SimulationTopicDispatcher
from return_platform.dependency_simulation.models import DependencyKind
from return_platform.dependency_simulation.repository import MongoSimulationRepository
from return_platform.dependency_simulation.service import DependencySimulationService
from return_platform.operations.case_commands import (
    CASE_COMMAND_SIGNAL_TOPIC,
    CaseCommandSignalDispatcher,
)
from return_platform.operations.integrations.outbox import (
    HttpJsonDispatcher,
    HttpTicketDispatcher,
    IntegrationOutboxDispatcher,
    TopicDispatcher,
)
from return_platform.operations.integrations.temporal_signal import TemporalSignalDispatcher
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.support_events import SUPPORT_RESPONSE_SIGNAL_TOPIC
from return_platform.workflows.return_case_recovery import build_case_recovery_service

logger = logging.getLogger("return_platform.workers.integration_outbox")

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
        # The one internal destination on this worker: a Support event, already
        # committed to MongoDB by the API, delivered to its case workflow as a
        # Temporal signal.
        #
        # Connected lazily through a factory rather than here. Temporal being
        # unreachable is exactly the outage this delivery path exists to
        # survive, and a `Client.connect` at start-up would turn it into a crash
        # loop of the process that is supposed to be holding the queue.
        dispatchers[SUPPORT_RESPONSE_SIGNAL_TOPIC] = TemporalSignalDispatcher(
            client_factory=lambda: Client.connect(settings.temporal_target),
        )
        # The review plane's commands (contracts.md sect. 7), committed by the
        # API beside their command records and delivered here as workflow
        # signals from a closed kind->signal map. Same lazy factory, same
        # reasoning as above.
        dispatchers[CASE_COMMAND_SIGNAL_TOPIC] = CaseCommandSignalDispatcher(
            client_factory=lambda: Client.connect(settings.temporal_target),
        )
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
        # Phase 10. The dispatcher above *produces* dead letters; nothing
        # consumed them, so a Support reply against a case whose execution had
        # gone came to rest at `REQUIRES_RECONCILIATION` and stayed there. This
        # is the consumer, and it belongs on this process because this is the
        # process that owns the queue -- a second deployment unit for one sweep
        # over one collection would be infrastructure standing in for a loop.
        #
        # Its own task rather than a call inside `run_forever`: reconciliation
        # runs on a minute cadence and delivery runs continuously, and a slow
        # `describe` against Temporal must never hold up the dispatch loop.
        # `run_forever` already swallows a failed pass, so a reconciler that
        # cannot reach Temporal degrades to doing nothing rather than taking
        # delivery down with it.
        reconciliation = asyncio.create_task(_reconciliation_sweep(runtime, settings, client))
        try:
            await worker.run_forever()
        finally:
            reconciliation.cancel()
            for task in activation_tasks:
                task.cancel()
            await asyncio.gather(reconciliation, *activation_tasks, return_exceptions=True)
            await activation.aclose()
            await client.close()


async def _reconciliation_sweep(
    runtime: ResolvedProcessConfiguration,
    settings: Settings,
    client: AsyncMongoClient[dict[str, object]],
) -> None:
    """Connect to Temporal, then reconcile forever. Never kills its caller.

    The connection is made here rather than at boot for the same reason
    `TemporalSignalDispatcher` defers its own: Temporal being unreachable is one
    of the conditions that fills this queue, and a process that refused to start
    without it would be down in exactly the outage it exists to clean up after.
    A connection that never succeeds leaves this task waiting and the dispatcher
    loop untouched.
    """
    try:
        temporal = await Client.connect(settings.temporal_target)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - delivery must not depend on reconciliation
        logger.warning("case_reconciliation_not_started_no_temporal", exc_info=True)
        return
    repository = OperationalRepository(client, settings)
    service = build_case_recovery_service(
        temporal=temporal,
        repository=repository,
        database=client[settings.mongo_database],
        timings=runtime.return_configuration.configuration.return_case,
        task_queue=settings.return_workflow_task_queue,
    )
    await service.run_forever()


if __name__ == "__main__":
    asyncio.run(run())
