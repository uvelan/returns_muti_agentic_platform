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
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
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
    OnDemandConnectorRegistry,
    OnDemandNeo4jGraphWriter,
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
from return_platform.dynamic_knowledge.lifecycle.lease_store import (
    GENERATION_LEASES_COLLECTION,
    MongoGenerationLeaseStore,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import GenericSourceRecordExtractor
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    AtomicConversationRepository,
)
from return_platform.dynamic_knowledge.order_agent.coordinator import DynamicOrderAgentCoordinator
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
) -> DynamicOrderAgentCoordinator:
    schema = load_active_schema(settings.dynamic_knowledge_schema_path)
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

    on_demand_sync_store = MongoOnDemandSyncStore(platform_mongo, settings.mongo_database)
    await on_demand_sync_store.ensure_indexes()
    on_demand_connectors = OnDemandConnectorRegistry(
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
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=OnDemandNeo4jGraphWriter(
            Neo4jDynamicGraphWriter(neo4j_driver, database=settings.neo4j_database),
            Neo4jGenerationWriter(neo4j_driver, database=settings.neo4j_database),
        ),
        store=on_demand_sync_store,
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
        # Identifies the holder of a read lease. There is no instance_id
        # setting; `bootstrap/system_store.py` establishes a per-process uuid as
        # the convention, and that is the right granularity here -- a lease
        # outlives a request but never the process that took it.
        owner_instance_id=f"order-agent-{uuid4()}",
    )
    return coordinator
