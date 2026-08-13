"""Build the dynamic Order Agent from the branch's existing runtime resources.

`build_dynamic_order_agent_runtime` returns a real `DynamicOrderAgentCoordinator`
directly -- since Phase 7 / Wave C2, Commit 3, only the dedicated
`order-discovery-worker` process (see `workflows/order_discovery_activities.py`)
ever constructs one; the FastAPI process routes turns to that worker's
Temporal workflow instead of holding a coordinator of its own (see
`dynamic_knowledge/api/order_agent.py`).

The targeted-sync half of the assembly moved to `targeted_sync.py` when the
orchestrator (W2.6) and the return-case worker (W2.5) needed the same stack.
It is imported rather than duplicated so all three processes resolve the same
schema, route platform-store sources to the same connector and share one
generation-lease wiring.
"""

from __future__ import annotations

from neo4j import AsyncDriver
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.ai.gateway.telemetry import RepositoryAIAttemptRecorder
from return_platform.ai_gateway.configuration import LoadedAIGatewayConfiguration
from return_platform.ai_gateway.routing import AIRoutePool
from return_platform.configuration.return_configuration import ReturnCaseTimingConfiguration
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.case_store import RepositoryCaseStore
from return_platform.dynamic_knowledge.integration.model_gateway import (
    RoutePoolReasoningModelGateway,
)
from return_platform.dynamic_knowledge.integration.mongo_store import (
    MongoAtomicConversationStore,
)
from return_platform.dynamic_knowledge.integration.targeted_sync import (
    build_targeted_graph_access,
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
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import TargetedSyncRunLedger
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    AtomicConversationRepository,
)
from return_platform.dynamic_knowledge.order_agent.coordinator import DynamicOrderAgentCoordinator
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.operations.repository import OperationalRepository
from return_platform.platform.reasoning.evidence_store import QueryEvidenceStore
from return_platform.platform.secrets.envelope import EnvelopeEncryptor
from return_platform.platform.system_store.repository import SystemStore
from return_platform.workflows.return_case_launcher import TemporalCaseWorkflowLauncher


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
    temporal_client: Client,
    return_case_timings: ReturnCaseTimingConfiguration,
    schema: ActiveSchema | None = None,
) -> DynamicOrderAgentCoordinator:
    # The published release if the analyzer has activated one, else the file.
    # This is the line that makes approving a schema in the console change what
    # the agent reasons over -- before it, an approved draft went nowhere.
    #
    # `schema` is supplied only when the caller has already resolved the release
    # this rebuild is *for* (the worker's configuration reconciler); see
    # `build_targeted_graph_access` for why resolving twice is not equivalent.
    graph = await build_targeted_graph_access(
        settings=settings,
        platform_mongo=platform_mongo,
        source_mongo=source_mongo,
        neo4j_driver=neo4j_driver,
        owner_role="order-agent",
        targeted_sync_runs=targeted_sync_runs,
        schema=schema,
    )
    conversation_documents = MongoAtomicConversationStore(
        platform_mongo,
        settings.mongo_database,
    )
    await conversation_documents.ensure_indexes()

    # One repository, two consumers: the case store below and the AI attempt
    # recorder here. Constructed once so the reasoning path's telemetry lands in
    # the same `ai_usage_attempts` collection the gateway's own attempts use --
    # a second store for the second dispatch path would make every cost query
    # depend on remembering there are two (W4.12).
    operational_repository = OperationalRepository(platform_mongo, settings)

    coordinator = DynamicOrderAgentCoordinator(
        schema=graph.schema,
        model_gateway=RoutePoolReasoningModelGateway(
            settings=settings,
            configuration=ai_gateway_configuration.configuration,
            route_pool=route_pool,
            recorder=RepositoryAIAttemptRecorder(operational_repository),
        ),
        knowledge_gateway=graph.knowledge_gateway,
        conversation_store=AtomicConversationRepository(conversation_documents),
        graph_state=graph.graph_state,
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        response_safety_guard=ResponseSafetyGuard(),
        on_demand_sync=graph.on_demand_sync,
        evidence_store=QueryEvidenceStore(system_store, reasoning_encryptor),
        system_store=system_store,
        envelope_encryptor=reasoning_encryptor,
        mongo_client=platform_mongo,
        # Shares the targeted-sync stack's lease store, so a read lease and the
        # write reservation taken inside that same turn are counted against one
        # generation document and one drain.
        generation_lease_store=graph.generation_lease_store,
        owner_instance_id=graph.owner_instance_id,
        # CONFIRM_ORDER's one write outside the conversation. The repository is
        # constructed here rather than passed in because this factory already
        # owns the platform Mongo client, and the agent must not be handed the
        # repository itself -- only the one-method port over it.
        case_store=RepositoryCaseStore(operational_repository),
        # The other half of that write, and the reason WF-01 existed: a case
        # was committed here and its `ReturnCaseWorkflow` was started nowhere,
        # so Support's RMA signalled an execution that had never been created.
        # Supplied unconditionally -- a coordinator that can create cases and
        # cannot start their workflows is the broken configuration, not a
        # supported one, and `confirm_order` refuses before writing anything
        # when this is absent.
        case_workflow_launcher=TemporalCaseWorkflowLauncher(
            client=temporal_client,
            repository=operational_repository,
            timings=return_case_timings,
            task_queue=settings.return_workflow_task_queue,
        ),
        active_snapshot_store=graph.active_snapshot_store,
    )
    return coordinator
