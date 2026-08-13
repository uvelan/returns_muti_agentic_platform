"""Run the E2E return orchestrator as a dedicated process.

Runs the configuration reconciler so this process's settings, secrets and AI
gateway release stay level with the API's rather than freezing at container
start (T-16). The orchestrator's own `return_configuration` stays as it was
handed over: a session runs to completion on the release it started under, the
same case-pinning rule the return workflow worker follows.
"""

import asyncio
import logging
import socket
import uuid

from neo4j import AsyncDriver, AsyncGraphDatabase
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.bootstrap.system_store import bootstrap_system_store
from return_platform.configuration.runtime_activation import (
    build_worker_runtime_activation,
    run_runtime_activation_loop,
)
from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.configuration.settings import Settings
from return_platform.data_platform.graph.sync_service import MongoTargetedSyncRunLedger
from return_platform.dynamic_knowledge.integration.shipment_observations import (
    GraphShipmentObservations,
)
from return_platform.dynamic_knowledge.integration.targeted_sync import (
    build_targeted_graph_access,
)
from return_platform.operations.orchestrator import ReturnOrchestrator
from return_platform.operations.repository import OperationalRepository
from return_platform.platform.governance.kernel import ProposalKernel
from return_platform.platform.governance.store import SystemStoreProposalStore
from return_platform.workflows.fulfillment_tracking import ShipmentObservationPort

logger = logging.getLogger("return_platform.scripts.run_return_orchestrator")

_PROCESS_CLASS = "return-orchestrator"


async def _shipment_observations(
    settings: Settings,
    platform_mongo: AsyncMongoClient[dict[str, object]],
    source_mongo: AsyncMongoClient[dict[str, object]],
    neo4j_driver: AsyncDriver,
) -> ShipmentObservationPort | None:
    """Build the graph reader, or run without one.

    Fulfillment is `best_effort`, and that policy is worth nothing if the
    process refuses to start when the graph is unreachable: the orchestrator
    would stop driving returns entirely over a stage that is permitted to be
    skipped. Absent this port the fulfillment state degrades to
    `AWAITING_HANDOFF` with `SHIPMENT_UNAVAILABLE:NOT_ATTEMPTED` on the evidence
    trail, which is the same honest reading as a failed lookup.
    """
    try:
        access = await build_targeted_graph_access(
            settings=settings,
            platform_mongo=platform_mongo,
            source_mongo=source_mongo,
            neo4j_driver=neo4j_driver,
            owner_role="return-orchestrator",
            # The same ledger the scheduled sync and the agent's targeted syncs
            # write to. A fulfillment sync appearing in its own place would give
            # operators one history per mechanism.
            targeted_sync_runs=MongoTargetedSyncRunLedger(platform_mongo, settings.mongo_database),
        )
    except Exception:  # noqa: BLE001 - see docstring; the stage is best_effort
        logger.exception("shipment_observations_unavailable_continuing_without_graph_reads")
        return None
    return GraphShipmentObservations.from_access(access)


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    platform_dsn = settings.mongo_dsn.get_secret_value()
    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else platform_dsn
    )
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(platform_dsn)
    source_mongo = mongo if source_dsn == platform_dsn else AsyncMongoClient(source_dsn)
    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        await verify_runtime_validation_receipts(
            mongo,
            settings.mongo_database,
            runtime.return_configuration.configuration,
        )
        temporal = await Client.connect(settings.temporal_target)
        repository = OperationalRepository(mongo, settings, source_mongo)
        worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        # The proposal kernel, so a completed return that produced a
        # machine-applicable recommendation lands in the one review queue rather
        # than in a `REVIEW_PENDING` field nothing transitions (W4.4).
        #
        # No activators are registered here on purpose: this process *proposes*
        # and never approves or applies. Activation publishes a configuration
        # release and belongs to an operator on `/api/proposals`, not to the
        # worker that noticed the pattern.
        system_store, _ = await bootstrap_system_store(settings, mongo)
        orchestrator = ReturnOrchestrator(
            repository=repository,
            temporal=temporal,
            settings=settings,
            worker_id=worker_id,
            return_configuration=runtime.return_configuration.configuration,
            proposal_kernel=ProposalKernel(
                SystemStoreProposalStore(system_store),
                activators={},
                audit=repository,
            ),
            shipment_observations=await _shipment_observations(
                settings, mongo, source_mongo, neo4j_driver
            ),
        )
        activation = await build_worker_runtime_activation(
            runtime=runtime,
            process_class=_PROCESS_CLASS,
            instance_id=worker_id,
            mongo=mongo,
            source_mongo=source_mongo,
            neo4j_driver=neo4j_driver,
        )
        activation_task = asyncio.create_task(run_runtime_activation_loop(activation.activator))
        try:
            await orchestrator.run_forever()
        finally:
            activation_task.cancel()
            await asyncio.gather(activation_task, return_exceptions=True)
            await activation.aclose()
    finally:
        await neo4j_driver.close()
        if source_mongo is not mongo:
            await source_mongo.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
