"""WF-01's closure test: an HTTP turn, and a real Temporal execution.

`POST /api/v2/order-agent/conversations/{id}/turns` is driven to a
confirmation, and then Temporal is asked directly whether an execution exists
at `return_case_workflow_id(case_id)`. Nothing here calls `start_workflow`;
nothing here asserts that a function was called or a mock was invoked. The
assertion is against the Temporal server.

That single question is the one nothing in the suite asked. `ReturnCaseWorkflow`
had a thorough real-Temporal test that started the workflow itself, so it proved
the component and never the connection -- and the connection was missing:
`api/return_support.py` signalled `return-case-<id>` for an execution that had
never been created, and Support's RMA was lost to a NOT_FOUND.

**The whole canonical path runs.** The FastAPI route, the real
`OrderDiscoveryWorkflow`, the real `run_order_discovery_turn` activity on a
real worker, the real `DynamicOrderAgentCoordinator`, the real compiled graph,
the real `RepositoryCaseStore` against real Mongo and the real
`TemporalCaseWorkflowLauncher` against real Temporal. Two things are scripted,
both outside the connection under test: the reasoning model (so the turn is
deterministic and costs nothing) and graph query execution (so no Neo4j
fixture data is required to make a candidate exist). Everything between the
HTTP request and the Temporal start is shipped code.

The case workflow is deliberately left with no worker polling its task queue.
The claim is that the execution *exists* -- which is exactly what Support's
signal needs and exactly what was absent -- and running it would drag Mongo
writes and a support service into a test about a start.
"""

from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI, Request
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.return_configuration import (
    ReturnCaseTimingConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import (
    DEFAULT_SYSTEM_STORE_MANIFEST_PATH,
    DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64,
    Settings,
)
from return_platform.dynamic_knowledge.api.order_agent import (
    DynamicOrderAgentRuntime,
)
from return_platform.dynamic_knowledge.api.order_agent import (
    router as order_agent_router,
)
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.integration.case_store import RepositoryCaseStore
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
    HallucinationGuard,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    ResponseSafetyGuard,
    SchemaQueryGuard,
    StrongAnchorGuard,
)
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentAction,
    AgentTurnContext,
    ModelInvocationResult,
    OrderConfirmation,
    OrderSearchIntent,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    AtomicConversationRepository,
)
from return_platform.dynamic_knowledge.order_agent.coordinator import DynamicOrderAgentCoordinator
from return_platform.dynamic_knowledge.order_agent.facts import build_fact_catalogue
from return_platform.dynamic_knowledge.order_agent.identification import (
    build_identification_catalogue,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.operations.repository import OperationalRepository
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
from return_platform.security.principal import Principal
from return_platform.workflows.order_discovery_activities import OrderDiscoveryActivities
from return_platform.workflows.order_discovery_worker import create_order_discovery_worker
from return_platform.workflows.return_case_launcher import TemporalCaseWorkflowLauncher
from return_platform.workflows.return_case_workflow import return_case_workflow_id

pytestmark = pytest.mark.asyncio

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)

RETURNS_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "returns" / "production.yaml"
)

AGENT_ID = "order-discovery-agent"
CAPABILITY = "return-context-collection"
ORDER_REFERENCE = "CW273354"
TENANT_ID = "tenant-a"
PRINCIPAL_ID = "associate-1"
_TEMPORAL_TARGET = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- see `tests/conftest.py::test_settings`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/return_platform"
        "?authSource=admin&directConnection=true"
    )


class SearchThenConfirmModel:
    """Search, confirm what the search actually offered, then say so.

    The confirmation reads its candidate out of the turn context rather than
    naming a fixed id: `order_search` mints a fresh `CandidateSet` with a real
    checksum and binding, and `validate_selection` would reject anything else --
    correctly, and for a reason unrelated to what this test is about.
    """

    def __init__(self) -> None:
        self.dispatched: list[ActionType] = []

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        cache = context.conversation_state.get("orderSearchCache")
        if cache is None:
            action = AgentAction(
                business_capability=CAPABILITY,
                action_type=ActionType.ORDER_SEARCH,
                decision_summary="Search for the order the associate named.",
                search_intent=OrderSearchIntent(orderNumbers=[ORDER_REFERENCE]),
            )
        elif context.case_id is None:
            candidate_set = cache["candidateSet"]
            action = AgentAction(
                business_capability=CAPABILITY,
                action_type=ActionType.CONFIRM_ORDER,
                decision_summary="The associate confirmed this order.",
                order_confirmation=OrderConfirmation(
                    candidate_set_id=candidate_set["candidate_set_id"],
                    candidate_id=candidate_set["candidate_ids"][0],
                    order_reference=ORDER_REFERENCE,
                    order_line_references=(),
                ),
            )
        else:
            action = AgentAction(
                business_capability=CAPABILITY,
                action_type=ActionType.RESPOND,
                decision_summary="Tell the associate the return is under way.",
                response=StructuredAgentResponse(
                    status="DISCOVERY_COMPLETE",
                    business_capability=CAPABILITY,
                    statements=(
                        ResponseStatement(
                            statement_id="s1",
                            statement_type=StatementType.REASONED_SUGGESTION,
                            text="Raising the return now.",
                            evidence_refs=(),
                        ),
                    ),
                ),
            )
        self.dispatched.append(action.action_type)
        return ModelInvocationResult(
            action=action,
            provider="scripted",
            model="scripted",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **_: Any) -> ModelInvocationResult:  # pragma: no cover
        raise AssertionError("no correction is expected")

    async def correct_response(self, **_: Any) -> ModelInvocationResult:  # pragma: no cover
        raise AssertionError("no correction is expected")


class OneOrderKnowledge:
    """Real schema projection; one fixed row, so a real candidate exists."""

    def __init__(self) -> None:
        self.executions = 0

    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]:
        return {"entities": sorted(schema.agent_policies[agent_id].allowed_entity_ids)}

    async def schema_details(
        self, schema: ActiveSchema, entity_ids: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            entity_id: {"fields": sorted(schema.entities[entity_id].fields)}
            for entity_id in entity_ids
        }

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        self.executions += 1
        row = {"sales_order_number": ORDER_REFERENCE}
        return {"rows": [row], "total": 1}


def _returns_configuration() -> Any:
    """The shipped returns configuration, loaded the way the runtime loads it."""
    return load_return_configuration(RETURNS_CONFIG_PATH).configuration


def _build_app(*, temporal: Client, task_queue: str) -> FastAPI:
    """The production router, with the identity the auth middleware would set."""
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            subject=PRINCIPAL_ID, roles=frozenset({"associate"})
        )
        request.state.tenant_id = TENANT_ID
        request.state.branch_ids = ("CHARLOTTE",)
        request.state.correlation_id = "corr-wf01"
        return await call_next(request)

    app.include_router(order_agent_router)
    app.state.dynamic_order_agent_runtime = DynamicOrderAgentRuntime(
        temporal_client=temporal, task_queue=task_queue
    )
    return app


async def test_confirming_through_the_api_creates_the_case_workflow_in_temporal(
    test_settings: Settings,
) -> None:
    schema = load_active_schema(SCHEMA_PATH)
    suffix = uuid.uuid4().hex[:12]
    conversation_id = f"conv-{suffix}"
    discovery_queue = f"test-order-discovery-{suffix}"
    case_queue = f"test-return-case-{suffix}"

    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    temporal = await Client.connect(_TEMPORAL_TARGET)

    config = load_system_store_config(DEFAULT_SYSTEM_STORE_MANIFEST_PATH)
    structures = tuple(
        definition.model_copy(update={"physical_name": f"{definition.physical_name}_test_{suffix}"})
        for definition in structure_definitions(config)
    )
    conversation_collection = f"dynamic_order_agent_conversations_test_{suffix}"
    generations_collection = f"dynamic_graph_generations_test_{suffix}"
    case_ids: list[str] = []
    try:
        bootstrapper = SystemStoreBootstrapper(
            lease_store=MongoLeaseStore(mongo, database="platform"),
            adapter=MongoSystemStoreAdapter(PymongoStructureGateway(mongo, database="platform")),
            migration_runner=MigrationRunner(MongoVersionLedger(mongo, database="platform")),
            bootstrap_state=MongoBootstrapStateStore(mongo, database="platform"),
            guard=FencedMongoTransactionGuard(mongo, database="platform"),
            owner_instance_id=f"test-{suffix}",
            fail_closed_on_drift=config.fail_closed_on_drift,
        )
        await bootstrapper.bootstrap(
            list(structures), auto_bootstrap_missing=config.auto_bootstrap_missing_structures
        )
        system_store = SystemStore(
            mongo,
            {definition.logical_name: definition for definition in structures},
            database="platform",
        )
        encryptor = AesGcmEnvelopeEncryptor(
            key=base64.b64decode(DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64), key_ref="test-key"
        )
        conversation_documents = MongoAtomicConversationStore(
            mongo, test_settings.mongo_database, collection=conversation_collection
        )
        await conversation_documents.ensure_indexes()

        # The real repository, so `create_case`'s unique confirmation index is
        # the one that decides -- a double could not prove the retry below.
        repository = OperationalRepository(mongo, test_settings)
        await repository.ensure_indexes()

        # DISC-01 moved the identification signals out of `OrderSearchIntent` and
        # into runtime configuration. `IdentificationCatalogue()` -- the default
        # this construction site used to accept -- recognises nothing, so
        # `orderNumbers` parsed as an unknown key, no search was planned, and the
        # candidate set came back empty. Load the shipped catalogue, the way
        # `integration/runtime_factory.py` does.
        returns_configuration = _returns_configuration()
        discovery = returns_configuration.discovery
        model = SearchThenConfirmModel()
        coordinator = DynamicOrderAgentCoordinator(
            schema=schema,
            model_gateway=model,
            knowledge_gateway=OneOrderKnowledge(),
            conversation_store=AtomicConversationRepository(conversation_documents),
            graph_state=MongoGraphStateProvider(
                mongo, test_settings.mongo_database, collection=generations_collection
            ),
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
            mongo_client=mongo,
            case_store=RepositoryCaseStore(repository),
            case_workflow_launcher=TemporalCaseWorkflowLauncher(
                client=temporal,
                repository=repository,
                timings=ReturnCaseTimingConfiguration(),
                task_queue=case_queue,
            ),
            identification=build_identification_catalogue(
                discovery.identification_fields,
                schema,
                default_fulltext_index=discovery.progressive.customer_fulltext_index,
            ),
            facts=build_fact_catalogue(returns_configuration.clarification_policy.fields),
        )

        worker = create_order_discovery_worker(
            temporal,
            OrderDiscoveryActivities(coordinator=coordinator, schema=schema),
            task_queue=discovery_queue,
        )
        app = _build_app(temporal=temporal, task_queue=discovery_queue)
        payload = {
            "conversation_id": conversation_id,
            "expected_conversation_version": 0,
            "client_turn_id": f"turn-{suffix}",
            "idempotency_key": f"idem-{suffix}",
            "message_id": f"msg-{suffix}",
            "message": f"Raise a return on order {ORDER_REFERENCE}",
            "agent_id": AGENT_ID,
        }

        async with worker:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=120.0
            ) as http:
                first = await http.post(
                    f"/api/v2/order-agent/conversations/{conversation_id}/turns", json=payload
                )
                assert first.status_code == 200, first.text

                # The identical request again -- the retry a client or a proxy
                # makes, carrying the same idempotency key and turn id.
                retried = await http.post(
                    f"/api/v2/order-agent/conversations/{conversation_id}/turns", json=payload
                )
                assert retried.status_code == 200, retried.text

        assert ActionType.CONFIRM_ORDER in model.dispatched

        case = await repository.cases.find_one({"channelAConversationId": conversation_id})
        assert case is not None, "the turn confirmed an order and committed a case"
        case_id = str(case["caseId"])
        case_ids.append(case_id)

        # The question WF-01 is: does the execution exist? Asked of Temporal.
        workflow_id = return_case_workflow_id(case_id)
        description = await temporal.get_workflow_handle(workflow_id).describe()
        assert description.workflow_type == "return-platform-return-case-v1"
        assert description.status is not None

        # And exactly one of them, for exactly one case.
        assert case["workflowId"] == workflow_id
        assert (
            await repository.cases.count_documents({"channelAConversationId": conversation_id}) == 1
        ), "the retried request produced no second case"

        executions = [
            execution
            async for execution in temporal.list_workflows(f"WorkflowId = '{workflow_id}'")
        ]
        assert len(executions) == 1, "the retried request produced no second execution"
    finally:
        for case_id in case_ids:
            try:
                await temporal.get_workflow_handle(
                    return_case_workflow_id(case_id)
                ).terminate("test cleanup")
            except Exception:  # noqa: BLE001 - never started, or already gone
                pass
            await mongo[test_settings.mongo_database]["cases"].delete_many({"caseId": case_id})
            await mongo[test_settings.mongo_database]["case_facts"].delete_many(
                {"caseId": case_id}
            )
        database = mongo.get_database("platform")
        for definition in structures:
            await database.get_collection(definition.physical_name).drop()
        await database.get_collection("platform_bootstrap_locks").delete_many(
            {"_id": "system_store_bootstrap"}
        )
        await database.get_collection("platform_bootstrap_state").delete_many(
            {"manifest_fingerprint": compute_manifest_fingerprint(list(structures))}
        )
        platform_database = mongo.get_database(test_settings.mongo_database)
        await platform_database.get_collection(conversation_collection).drop()
        await platform_database.get_collection(generations_collection).drop()
        await mongo.close()
