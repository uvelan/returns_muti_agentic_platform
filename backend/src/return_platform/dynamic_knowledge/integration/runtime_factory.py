"""Build the dynamic Order Agent from the branch's existing runtime resources.

`build_dynamic_order_agent_runtime` returns a real `DynamicOrderAgentCoordinator`
directly -- since Phase 7 / Wave C2, Commit 3, only the dedicated
`order-discovery-worker` process (see `workflows/order_discovery_activities.py`)
ever constructs one; the FastAPI process routes turns to that worker's
Temporal workflow instead of holding a coordinator of its own (see
`dynamic_knowledge/api/order_agent.py`).
"""

from __future__ import annotations

from uuid import uuid4

from neo4j import AsyncDriver
from pymongo import AsyncMongoClient

from return_platform.ai_gateway.configuration import LoadedAIGatewayConfiguration
from return_platform.ai_gateway.routing import AIRoutePool
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import resolve_active_schema
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.case_store import RepositoryCaseStore
from return_platform.dynamic_knowledge.integration.model_gateway import (
    RoutePoolReasoningModelGateway,
)
from return_platform.dynamic_knowledge.integration.mongo_store import (
    MongoAtomicConversationStore,
    MongoGraphStateProvider,
    MongoOnDemandSyncStore,
)
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.integration.on_demand_sync_adapters import (
    OnDemandNeo4jGraphWriter,
    targeted_connector_registry,
)
from return_platform.dynamic_knowledge.knowledge.guards import (
    CapabilityGuard,
    HallucinationGuard,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    ResponseSafetyGuard,
    SchemaQueryGuard,
    StrongAnchorGuard,
)
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.lifecycle.lease_store import (
    GENERATION_LEASES_COLLECTION,
    MongoGenerationLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION,
    MongoActiveRuntimeSnapshotStore,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import (
    OnDemandSyncCoordinator,
    TargetedSyncRunLedger,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
    contact_digest_secrets,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    AtomicConversationRepository,
)
from return_platform.dynamic_knowledge.order_agent.coordinator import DynamicOrderAgentCoordinator
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.operations.repository import OperationalRepository
from return_platform.platform.reasoning.evidence_store import QueryEvidenceStore
from return_platform.platform.secrets.envelope import EnvelopeEncryptor
from return_platform.platform.system_store.repository import SystemStore
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector
from return_platform.source_connectors.sqlserver import (
    SqlServerConnectionSettings,
    SqlServerSourceScanConnector,
)


def dynamic_order_agent_enabled(settings: Settings) -> bool:
    """Return whether the dynamic Order Discovery Agent runtime is enabled."""

    return settings.dynamic_order_agent_enabled


async def build_dynamic_order_agent_runtime(
    *,
    settings: Settings,
    platform_mongo: AsyncMongoClient[dict[str, object]],
    source_mongo: AsyncMongoClient[dict[str, object]],
    neo4j_driver: AsyncDriver,
    ai_gateway_configuration: LoadedAIGatewayConfiguration,
    route_pool: AIRoutePool,
    system_store: SystemStore,
    reasoning_encryptor: EnvelopeEncryptor,
    targeted_sync_runs: TargetedSyncRunLedger | None = None,
) -> DynamicOrderAgentCoordinator:
    # The published release if the analyzer has activated one, else the file.
    # This is the line that makes approving a schema in the console change what
    # the agent reasons over -- before it, an approved draft went nowhere.
    releases = SchemaReleaseStore(platform_mongo, settings.mongo_database)
    await releases.ensure_indexes()
    schema = await resolve_active_schema(settings.dynamic_knowledge_schema_path, releases)
    conversation_documents = MongoAtomicConversationStore(
        platform_mongo,
        settings.mongo_database,
    )
    graph_state = MongoGraphStateProvider(platform_mongo, settings.mongo_database)
    await conversation_documents.ensure_indexes()
    await graph_state.ensure_indexes()

    # Without this the coordinator still resolves a generation through the
    # handle, but takes no lease -- the drain in
    # GenerationLifecycleOrchestrator._retire would have nothing to wait for and
    # could retire a generation a live turn is still reading.
    generation_lease_store = MongoGenerationLeaseStore(
        platform_mongo[settings.mongo_database][GENERATION_LEASES_COLLECTION]
    )
    # Identifies the holder of a lease. There is no instance_id setting;
    # `bootstrap/system_store.py` establishes a per-process uuid as the
    # convention, and that is the right granularity -- a lease outlives a
    # request but never the process that took it.
    owner_instance_id = f"order-agent-{uuid4()}"
    # ActiveRuntimeSnapshot is the design's one atomic pointer, so the handle
    # resolves from it where one exists and only falls back to
    # MongoGraphStateProvider's older dynamic_graph_generations lookup until a
    # rebuild has ever run. See GenerationHandleProvider._resolve.
    active_snapshot_store = MongoActiveRuntimeSnapshotStore(
        platform_mongo[settings.mongo_database][ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION]
    )

    on_demand_sync_store = MongoOnDemandSyncStore(platform_mongo, settings.mongo_database)
    await on_demand_sync_store.ensure_indexes()
    on_demand_connectors = targeted_connector_registry(
        schema=schema,
        mongo=MongoDBSourceScanConnector(
            source_mongo[settings.source_mongo_database], schema=schema
        ),
        sqlserver=SqlServerSourceScanConnector(
            SqlServerConnectionSettings(
                server=settings.sqlserver_host,
                port=settings.sqlserver_port,
                user=settings.sqlserver_user,
                password=settings.sqlserver_password.get_secret_value(),
                database=settings.sqlserver_database,
                timeout_seconds=int(settings.operation_timeout_seconds),
            ),
            schema=schema,
        ),
    )
    on_demand_sync = OnDemandSyncCoordinator(
        connectors=on_demand_connectors,
        # The same resolved secrets the scheduled sync extracts with. Handing an
        # empty map here would make a derived field that needs one raise mid-turn
        # for a document the scheduled pipeline projects without complaint --
        # two extraction behaviours from one extractor, decided by which caller
        # built it.
        extractor=GenericSourceRecordExtractor(
            resolved_secrets=contact_digest_secrets(
                schema, hmac_key=settings.contact_lookup_hmac_key.get_secret_value()
            )
        ),
        projector=GenericGraphProjector(),
        writer=OnDemandNeo4jGraphWriter(
            Neo4jDynamicGraphWriter(neo4j_driver, database=settings.neo4j_database),
            Neo4jGenerationWriter(neo4j_driver, database=settings.neo4j_database),
        ),
        store=on_demand_sync_store,
        # The one run ledger the sync control screen reads. Supplied by the
        # composition root rather than built here: it writes a `data_platform`
        # collection, and `dynamic_knowledge` does not depend on that package.
        run_ledger=targeted_sync_runs,
        # Shares the coordinator's lease store, so a read lease and the write
        # reservation taken inside that same turn are counted against one
        # generation document and one drain.
        generation_handles=GenerationHandleProvider(
            graph_state,
            lease_store=generation_lease_store,
            owner_instance_id=owner_instance_id,
            snapshot_store=active_snapshot_store,
        ),
    )

    coordinator = DynamicOrderAgentCoordinator(
        schema=schema,
        model_gateway=RoutePoolReasoningModelGateway(
            settings=settings,
            configuration=ai_gateway_configuration.configuration,
            route_pool=route_pool,
        ),
        knowledge_gateway=Neo4jKnowledgeGateway(
            neo4j_driver,
            database=settings.neo4j_database,
        ),
        conversation_store=AtomicConversationRepository(conversation_documents),
        graph_state=graph_state,
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        response_safety_guard=ResponseSafetyGuard(),
        on_demand_sync=on_demand_sync,
        evidence_store=QueryEvidenceStore(system_store, reasoning_encryptor),
        system_store=system_store,
        envelope_encryptor=reasoning_encryptor,
        mongo_client=platform_mongo,
        generation_lease_store=generation_lease_store,
        owner_instance_id=owner_instance_id,
        # CONFIRM_ORDER's one write outside the conversation. The repository is
        # constructed here rather than passed in because this factory already
        # owns the platform Mongo client, and the agent must not be handed the
        # repository itself -- only the one-method port over it.
        case_store=RepositoryCaseStore(OperationalRepository(platform_mongo, settings)),
        active_snapshot_store=active_snapshot_store,
    )
    return coordinator
