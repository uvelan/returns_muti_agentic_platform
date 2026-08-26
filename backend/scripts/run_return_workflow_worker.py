"""Run the Return workflow worker as a dedicated Docker process.

Runs the same configuration reconciler the API process runs, so this worker's
settings, secrets and AI gateway release stay level with the API's rather than
freezing at whatever was live when the container started (T-16).

What it deliberately does *not* hot-swap is the return configuration the case
activities were built from. `ReturnCaseWorkflow` carries a
`configuration_release_id` on its input, which is the platform's statement that
this family is case-pinned: a case runs to completion on the release it started
under, and a new case starts on the new one. Replacing the support service's
configuration underneath a running case would break that, not honour it.

The reconciler runs as a plain asyncio task in this process. It is on the worker
side of the Temporal boundary and never inside a workflow body, so no
configuration read it performs can reach replay.
"""

import asyncio
import socket
import uuid

from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.runtime_activation import build_worker_runtime_activation
from return_platform.configuration.runtime_integrations import verify_runtime_validation_receipts
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.data_platform.graph.sync_service import MongoTargetedSyncRunLedger
from return_platform.dynamic_knowledge.config_loader import resolve_active_schema
from return_platform.dynamic_knowledge.integration.bay_observations import (
    GraphWarehouseBayObservations,
)
from return_platform.dynamic_knowledge.integration.order_placement_observations import (
    GraphOrderPlacementObservations,
)
from return_platform.dynamic_knowledge.integration.return_record_sync import GraphReturnRecordSync
from return_platform.dynamic_knowledge.integration.shipment_state_sync import GraphShipmentStateSync
from return_platform.dynamic_knowledge.integration.targeted_sync import (
    build_targeted_graph_access,
)
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.operations.order_lines.case_detail import SourceOrderLineDetails
from return_platform.operations.shipment_tracking import ShipmentTrackingStore
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.service import ReturnSupportService
from return_platform.operations.seed_manifest import (
    SOURCE_PRODUCTS_DATASET,
    SOURCE_SALES_DATASET,
)
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.operations.warehouse.case_placement import CaseBayPlacement
from return_platform.workflows.persistence import ReturnSessionRepository
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_launcher import TemporalCaseWorkflowLauncher
from return_platform.workflows.return_case_recovery import ReturnCaseWorkflowRecovery
from return_platform.workflows.worker import create_return_workflow_worker

_SESSIONS_COLLECTION = "return_sessions"
_AUDITS_COLLECTION = "return_session_audit_events"
_OUTBOX_COLLECTION = "return_session_outbox_events"
_DECISIONS_COLLECTION = "return_session_agent_decisions"
_PERSISTENCE_TIMEOUT_SECONDS = 5.0
_PROCESS_CLASS = "return-workflow-worker"


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    source_mongo: AsyncMongoClient[dict[str, object]] = (
        mongo
        if source_dsn == settings.mongo_dsn.get_secret_value()
        else AsyncMongoClient[dict[str, object]](source_dsn)
    )
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
        repository = ReturnSessionRepository.from_client(
            mongo,
            database=settings.mongo_database,
            sessions_collection=_SESSIONS_COLLECTION,
            audits_collection=_AUDITS_COLLECTION,
            outbox_collection=_OUTBOX_COLLECTION,
            decisions_collection=_DECISIONS_COLLECTION,
            operation_timeout_seconds=_PERSISTENCE_TIMEOUT_SECONDS,
        )
        operational_repository = OperationalRepository(mongo, settings, source_mongo)
        # The Support handoff has to say what is coming back, not only which line
        # reference it is. That needs the sales document for the line's own
        # description and the product catalogue for its colour, both through the
        # active release's bindings -- so the schema is resolved here, once, and
        # handed to the activities as a port.
        active_schema = await resolve_active_schema(
            settings.dynamic_knowledge_schema_path,
            SchemaReleaseStore(mongo, settings.mongo_database),
        )
        order_line_details = SourceOrderLineDetails(
            sales=await operational_repository.source_dataset(SOURCE_SALES_DATASET),
            catalogue=await operational_repository.source_dataset(SOURCE_PRODUCTS_DATASET),
            schema=active_schema,
            colour_paths=runtime.return_configuration.configuration.source_resolution.product_colour_paths,
        )
        # `ReturnCaseWorkflow` was registered nowhere in a deployed process:
        # `create_return_workflow_worker` takes its activities optionally and
        # this script never supplied them, so a case could be started and would
        # then stall on its first activity. Supplying them here is what makes
        # the case lifecycle -- and the record-scoped graph sync that follows an
        # RMA -- actually run outside a test.
        graph = await build_targeted_graph_access(
            settings=settings,
            platform_mongo=mongo,
            source_mongo=source_mongo,
            neo4j_driver=neo4j_driver,
            owner_role="return-case-worker",
            # The same ledger the scheduled sync and the agent's targeted syncs
            # write to, so an operator sees one sync history rather than one per
            # mechanism.
            targeted_sync_runs=MongoTargetedSyncRunLedger(mongo, settings.mongo_database),
        )
        # One repository for both consumers below. RMA-01 needs it as the
        # authoritative return store; BAY-02 needs it for the live reservation
        # aggregate. It resolves the process-wide bounded pool per operation, so
        # a single instance opens no connection until something actually writes.
        # SHIP-01 job 2. Without the sync port the repository still writes
        # `dbo.return_tracking` correctly but returns `graph_generation_id=None`
        # and tells the graph nothing, so no case ever learns the shipment moved.
        # Only an APPLIED update syncs -- a DUPLICATE or STALE changed no stored
        # truth, and spending a targeted sync on one would write the graph a value
        # it already holds and make a refused stale update indistinguishable from
        # an accepted one in the sync log.
        sql_business_state = SQLBusinessStateRepository(
            settings,
            shipment_graph_sync=GraphShipmentStateSync.from_access(graph),
        )
        case_activities = ReturnCaseActivities(
            repository=operational_repository,
            support_service=ReturnSupportService(
                client=mongo,
                settings=settings,
                configuration=runtime.return_configuration.configuration,
                operational_repository=operational_repository,
            ),
            graph_sync=GraphReturnRecordSync.from_access(graph),
            # RMA-01. The authoritative SQL return store. Wired here because this
            # is the process that owns `ReturnCaseWorkflow`. Without it the
            # activity raises rather than silently writing only to MongoDB --
            # the same fail-loud stance `synchronize_return_records` takes about
            # its graph port.
            return_store=sql_business_state,
            # BAY-01. `WarehousePlacementService` was graph-oriented, real and
            # keyed by session, so the case flow -- which has no session --
            # never called it and `request_bay_assignment` acknowledged a
            # request nothing served. Same observation port, same ranking
            # agent, re-keyed onto the case.
            bay_placement=CaseBayPlacement(
                repository=operational_repository,
                configuration=runtime.return_configuration.configuration,
                observations=GraphWarehouseBayObservations.from_access(graph),
                order_observations=GraphOrderPlacementObservations.from_access(graph),
                # BAY-02: the live reservation aggregate, from the same table
                # the transactional reservation locks. Bay configuration still
                # comes from the graph -- this is the one figure a graph node
                # cannot hold, because it moves with the clock.
                live_capacity=sql_business_state,
            ),
            # What a selected line actually *is* -- the product name, the SKU and
            # the colour -- for the Support handoff. A port rather than a call
            # inside the activity, for the same reason bay placement is one: the
            # activity's dependencies are the surface a reader checks.
            order_line_details=order_line_details,
            # SLA-01: the business calendar the support wait and the reminder
            # cadence are counted against. Read per call through the activated
            # state rather than captured, so a corrected holiday list reaches a
            # case already waiting without a restart. `activation` is bound
            # below and this closure is only ever called from a running
            # activity, which is well after `worker.run()` starts.
            configuration=lambda: activation.state.return_configuration.configuration,
            # Return-shipment documents, seeded when a Support outcome lands.
            # The collection name and the field names come from the activated
            # release (`shipment_tracking`), so a rename is a release edit --
            # nothing here knows the collection is called shipmentInfo today.
            shipment_tracking=ShipmentTrackingStore(
                collection_of=lambda name: source_mongo[
                    settings.source_mongo_database or settings.mongo_database
                ][name],
                configuration=lambda: (
                    activation.state.return_configuration.configuration.shipment_tracking
                ),
            ),
        )
        worker = create_return_workflow_worker(
            temporal, repository, case_activities=case_activities
        )
        # The durable retry path behind confirmation (WF-01). Confirmation
        # commits the case and then starts its workflow; if the second step
        # cannot be reached the turn fails, but nothing about a failed turn
        # repairs the case. This sweep runs in the process that owns
        # `ReturnCaseWorkflow`, finds cases whose `workflowId` was never
        # recorded and starts the execution they are owed -- through the same
        # launcher and therefore the same idempotency the node uses.
        case_recovery = ReturnCaseWorkflowRecovery(
            launcher=TemporalCaseWorkflowLauncher(
                client=temporal,
                repository=operational_repository,
                timings=runtime.return_configuration.configuration.return_case,
                task_queue=settings.return_workflow_task_queue,
            ),
            repository=operational_repository,
        )
        instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        activation = await build_worker_runtime_activation(
            runtime=runtime,
            process_class=_PROCESS_CLASS,
            instance_id=instance_id,
            mongo=mongo,
            source_mongo=source_mongo,
            neo4j_driver=neo4j_driver,
        )
        # Heartbeating moved into `activation.start()`. It was a loop each
        # worker hand-rolled here, and `integration-outbox-worker` never got
        # one -- five workers reported adoption while `worker_heartbeats`
        # held four documents. A signal every process must emit does not
        # belong in a per-process loop a new worker has to remember to copy.
        recovery_task = asyncio.create_task(case_recovery.run_forever())
        background = (recovery_task, *activation.start())
        try:
            await worker.run()
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await activation.aclose()
    finally:
        await neo4j_driver.close()
        if source_mongo is not mongo:
            await source_mongo.close()
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
