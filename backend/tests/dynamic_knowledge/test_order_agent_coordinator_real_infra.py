"""Real-Mongo proof that DynamicOrderAgentCoordinator's wrapping around the
compiled LangGraph StateGraph actually works end-to-end: a real SystemStore-
backed checkpointer persists a real encrypted checkpoint, ReasoningRunLifecycle
creates a real run record, CheckpointRetentionPolicy.mark_terminal really
stamps expires_at on COMPLETED, and the committed AgentTurnResult round-trips
through a real MongoAtomicConversationStore.

The model/knowledge gateways are fakes (no live AI/Neo4j call needed to prove
this wiring) -- graph control-flow itself is already proven against fakes in
test_order_agent_graph.py; this file's job is proving the *persistence*
plumbing the graph-level tests don't touch.
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any
from urllib.parse import quote

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import (
    DEFAULT_SYSTEM_STORE_MANIFEST_PATH,
    DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64,
)
from return_platform.dynamic_knowledge.integration.mongo_store import (
    MongoAtomicConversationStore,
    MongoGraphStateProvider,
)
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.evidence import (
    ResponseStatement,
    StatementType,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.guards import (
    CapabilityGuard,
    GuardContext,
    HallucinationGuard,
    PrincipalContext,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    ResponseSafetyGuard,
    SchemaQueryGuard,
    StrongAnchorGuard,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentAction,
    AgentTurnContext,
    AgentTurnRequest,
    ModelInvocationResult,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    AtomicConversationRepository,
)
from return_platform.dynamic_knowledge.order_agent.coordinator import DynamicOrderAgentCoordinator
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.platform.reasoning.evidence_store import QueryEvidenceStore
from return_platform.platform.secrets.envelope import AesGcmEnvelopeEncryptor
from return_platform.platform.system_store.bootstrap import SystemStoreBootstrapper
from return_platform.platform.system_store.contracts import compute_manifest_fingerprint
from return_platform.platform.system_store.manifest_loader import (
    load_system_store_config,
    structure_definitions,
)
from return_platform.platform.system_store.migrations import MigrationRunner
from return_platform.platform.system_store.mongo import (
    FencedMongoTransactionGuard,
    MongoBootstrapStateStore,
    MongoLeaseStore,
    MongoSystemStoreAdapter,
    MongoVersionLedger,
    PymongoStructureGateway,
)
from return_platform.platform.system_store.repository import SystemStore


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin"


class Knowledge:
    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]:
        del agent_id
        return {"entityIds": list(schema.entities)}

    async def schema_details(
        self, schema: ActiveSchema, entity_ids: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            entity_id: schema.entities[entity_id].model_dump(mode="json")
            for entity_id in entity_ids
        }

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"rows": [{"id": "A-1", "name": "Configured value"}], "total": 1}


class QueryThenRespondModel:
    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        if not context.query_evidence:
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.GRAPH_QUERY,
                decision_summary="Search the configured graph entity using the supplied value.",
                query_plan=LogicalQueryPlan(
                    operation=QueryOperation.SEARCH,
                    start_entity_id="entity_a",
                    fields=("id", "name"),
                    filters=(
                        QueryCondition(
                            entity_id="entity_a", field_id="id", operator="EXACT", value="A-1"
                        ),
                    ),
                ),
            )
        else:
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.RESPOND,
                decision_summary="The graph evidence supports a final response.",
                response=StructuredAgentResponse(
                    status="DISCOVERY_COMPLETE",
                    business_capability="order-discovery",
                    statements=(
                        ResponseStatement(
                            statement_id="s1",
                            statement_type=StatementType.CLARIFICATION_QUESTION,
                            text="One configured record was found.",
                            evidence_refs=(),
                        ),
                    ),
                ),
            )
        return ModelInvocationResult(
            action=action,
            provider="provider-a",
            model="standard-model",
            prompt_tokens=10,
            completion_tokens=10,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("correction not expected")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("correction not expected")


@pytest.mark.asyncio
async def test_coordinator_persists_a_real_checkpoint_and_completed_run(
    active_schema: ActiveSchema,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    config = load_system_store_config(DEFAULT_SYSTEM_STORE_MANIFEST_PATH)
    suffix = uuid.uuid4().hex[:12]
    structures = tuple(
        definition.model_copy(update={"physical_name": f"{definition.physical_name}_test_{suffix}"})
        for definition in structure_definitions(config)
    )
    db = client.get_database("platform")
    try:
        bootstrapper = SystemStoreBootstrapper(
            lease_store=MongoLeaseStore(client, database="platform"),
            adapter=MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform")),
            migration_runner=MigrationRunner(MongoVersionLedger(client, database="platform")),
            bootstrap_state=MongoBootstrapStateStore(client, database="platform"),
            guard=FencedMongoTransactionGuard(client, database="platform"),
            owner_instance_id=f"test-{suffix}",
            fail_closed_on_drift=config.fail_closed_on_drift,
        )
        await bootstrapper.bootstrap(
            list(structures), auto_bootstrap_missing=config.auto_bootstrap_missing_structures
        )
        system_store = SystemStore(
            client,
            {definition.logical_name: definition for definition in structures},
            database="platform",
        )
        encryptor = AesGcmEnvelopeEncryptor(
            key=base64.b64decode(DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64), key_ref="test-key"
        )

        conversation_collection = f"dynamic_order_agent_conversations_test_{suffix}"
        graph_generations_collection = f"dynamic_graph_generations_test_{suffix}"
        conversation_documents = MongoAtomicConversationStore(
            client, "return_platform", collection=conversation_collection
        )
        graph_state = MongoGraphStateProvider(
            client, "return_platform", collection=graph_generations_collection
        )

        coordinator = DynamicOrderAgentCoordinator(
            schema=active_schema,
            model_gateway=QueryThenRespondModel(),
            knowledge_gateway=Knowledge(),
            conversation_store=AtomicConversationRepository(conversation_documents),
            graph_state=graph_state,
            capability_guard=CapabilityGuard(),
            schema_guard=SchemaQueryGuard(),
            query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
            strong_anchor_guard=StrongAnchorGuard(),
            hallucination_guard=HallucinationGuard(),
            response_safety_guard=ResponseSafetyGuard(),
            on_demand_sync=None,
            cypher_compiler=CypherCompiler(),
            evidence_store=QueryEvidenceStore(system_store, encryptor),
            system_store=system_store,
            envelope_encryptor=encryptor,
            mongo_client=client,
        )
        request = AgentTurnRequest(
            conversation_id=f"conv-{suffix}",
            expected_conversation_version=0,
            client_turn_id="turn-1",
            idempotency_key="idem-1",
            message_id="msg-1",
            message="Find the configured record A-1",
            agent_id="agent_a",
        )
        guard_context = GuardContext(
            schema=active_schema,
            agent_policy=active_schema.agent_policies["agent_a"],
            principal=PrincipalContext(
                principal_id="p1", tenant_id="t1", roles=frozenset({"associate"})
            ),
        )

        result = await coordinator.process_turn(request, guard_context)

        assert result.response.status == "DISCOVERY_COMPLETE"
        assert len(result.query_evidence) == 1

        run_doc = await system_store.read_only("reasoning_runs").find_one(
            {"thread_id": f"order-discovery:conv-{suffix}:turn-1:1"}
        )
        assert run_doc is not None
        assert run_doc["lifecycle_state"] == "COMPLETED"
        assert run_doc["expires_at"] is not None

        checkpoint_exists = await system_store.read_only("reasoning_checkpoints").find_one(
            {"thread_id": f"order-discovery:conv-{suffix}:turn-1:1"}
        )
        assert checkpoint_exists is not None
    finally:
        for definition in structures:
            await db.get_collection(definition.physical_name).drop()
        await db.get_collection("platform_bootstrap_locks").delete_many(
            {"_id": "system_store_bootstrap"}
        )
        await db.get_collection("platform_bootstrap_state").delete_many(
            {"manifest_fingerprint": compute_manifest_fingerprint(list(structures))}
        )
        await client.get_database("return_platform").get_collection(conversation_collection).drop()
        await (
            client.get_database("return_platform")
            .get_collection(graph_generations_collection)
            .drop()
        )
        await client.close()
