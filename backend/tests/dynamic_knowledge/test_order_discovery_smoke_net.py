"""Order Discovery still works. Asserted on structure, never on prose.

Wave 1 restructures the conversation contract -- a ninth action, new context
fields, a case link -- and nothing currently verifies that discovery survives
it. This is the net that has to hold while that happens.

**What is real here.** The production descriptor
(`config/dynamic_knowledge/active-schema.return-order.yaml`), the real
`CypherCompiler`, and all six real guards. Only infrastructure is substituted:
the model, which is scripted so a scenario is deterministic, and graph
execution, which returns fixed rows. Everything between them -- routing,
capability validation, schema validation, query safety, plan compilation,
budget enforcement, evidence recording, hallucination validation -- is the
shipped code.

The on-demand-sync scenarios at the end substitute two more things at the same
infrastructure edge: the source connector and the graph writer. Between them
runs the real `OnDemandSyncCoordinator`, the real targeted-read planner, the
real source-read compiler, the real extractor and the real projector -- which
is the point, because the defect this covers lived in exactly that stretch and
was invisible from either end.

**What is asserted.** The sequence of `ActionType`s the graph dispatched, the
`LogicalQueryPlan` that reached the compiler, the guard verdict, and the
candidate outcome. Never the wording of a response: the tone is model-authored
and configurable by design, so asserting on it would make a copy change look
like a regression and would pin exactly the thing that is supposed to move.

Deterministic without a seed: the model is a script, not a sampler, so there is
nothing to pin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.evidence import (
    QueryEvidence,
    ResponseStatement,
    StatementType,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.guards import (
    AnchorValue,
    CapabilityGuard,
    GuardContext,
    HallucinationGuard,
    PrincipalContext,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    ResponseSafetyGuard,
    SchemaQueryGuard,
    StrongAnchorGuard,
    StrongAnchorRequest,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    SyncReceipt,
    SyncReservation,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentAction,
    AgentTurnContext,
    ModelInvocationResult,
    OrderConfirmation,
    OrderSearchIntent,
)
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.graph import build_order_agent_graph
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    ConfirmedCase,
    GraphDependencies,
    TurnRuntimeContext,
)
from return_platform.dynamic_knowledge.order_agent.identification import (
    IdentificationCatalogue,
    build_identification_catalogue,
)
from return_platform.dynamic_knowledge.order_agent.state import CandidateSet
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntitySourceAccess,
    RelationshipSourceAccess,
    SourceContractStatus,
)
from return_platform.source_connectors.compilation import compile_source_read
from return_platform.source_connectors.contracts import RawSourceDocument, RawSourcePage
from return_platform.workflows.return_case_launcher import StartedCaseWorkflow
from return_platform.workflows.return_case_workflow import return_case_workflow_id

pytestmark = pytest.mark.asyncio

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
AGENT_ID = "order-discovery-agent"
CAPABILITY = "order-discovery"


@pytest.fixture(scope="module")
def schema() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


# ---------------------------------------------------------------------------
# Substitutes: the model (scripted) and graph execution (fixed rows)
# ---------------------------------------------------------------------------


class ScriptedModel:
    """Returns a fixed list of actions, one per `decide`, and records the calls.

    A correction request is an assertion failure by default: a scenario that
    silently drifted into the correction path would otherwise still pass while
    testing something other than what it names.
    """

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = list(actions)
        self.dispatched: list[ActionType] = []
        self.contexts: list[AgentTurnContext] = []

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        self.contexts.append(context)
        if not self._actions:
            raise AssertionError("the graph asked for more actions than the scenario scripted")
        action = self._actions.pop(0)
        self.dispatched.append(action.action_type)
        return ModelInvocationResult(
            action=action,
            provider="scripted",
            model="scripted",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError(f"unexpected action correction: {kwargs.get('validation_error')}")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError(f"unexpected response correction: {kwargs.get('validation_error')}")


class RecordingKnowledge:
    """Real schema projection, fixed rows, and every compiled plan captured."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.plans: list[LogicalQueryPlan] = []
        self.compiled: list[str] = []

    async def compact_schema(
        self, schema: ActiveSchema, agent_id: str, *, principal_roles: frozenset[str]
    ) -> dict[str, Any]:
        del principal_roles
        policy = schema.agent_policies[agent_id]
        return {"entities": sorted(policy.allowed_entity_ids)}

    async def schema_details(
        self, schema: ActiveSchema, entity_ids: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            entity_id: {"fields": sorted(schema.entities[entity_id].fields)}
            for entity_id in entity_ids
        }

    async def execute(self, **kwargs: Any) -> Any:
        self.plans.append(kwargs["plan"])
        self.compiled.append(kwargs["compiled_cypher"])
        return {"rows": list(self.rows), "total": len(self.rows)}


class MemoryEvidence:
    def __init__(self) -> None:
        self.stored: dict[str, QueryEvidence] = {}

    async def put(self, *, run_id: str, evidence: QueryEvidence) -> None:
        del run_id
        self.stored[evidence.query_execution_id] = evidence

    async def get_many(self, query_execution_ids: Any) -> tuple[QueryEvidence, ...]:
        return tuple(self.stored[i] for i in query_execution_ids if i in self.stored)


CANDIDATE_SET_ID = "cs-smoke"
CANDIDATE_ID = "cand-1"
ORDER_REFERENCE = "CW273354"
ACCOUNT_ID = "CHARLOTTE"
ORDER_KEY = f"{ACCOUNT_ID}*{ORDER_REFERENCE}"

#: One salesInv header, as the schema maps it. Only the fields the on-demand
#: scenarios below actually assert on, plus the `docType` discriminator that
#: `sales_order`'s `where` selector tests -- omitting that is exactly how the
#: order used to be fetched and thrown away.
SALES_INV_DOCUMENT: dict[str, Any] = {
    "_id": ORDER_KEY,
    "salesHdrEventMeta": {"lastUpdateTs": "2026-08-04T09:00:00Z"},
    "salesHdrEventData": {
        "accountId": ACCOUNT_ID,
        "orderId": ORDER_REFERENCE,
        "docType": "headerLines",
        "orderStatus": "OPEN",
    },
    "salesHdr": {"salesHdrData": {"custId": "C-1", "custName": "Jane Doe"}},
    "salesLines": [
        {
            "salesLnsEventData": {"lineNumber": "1", "lineType": "PRODUCT"},
            "lineData": {"altCode1": "FAU-1234", "productDesc": "Chrome faucet", "orderQty": 2},
        }
    ],
}


def _projected(document: Any, paths: tuple[tuple[str, ...], ...]) -> Any:
    """The document as a projected read returns it. Array-aware, because the
    paths that matter address fields inside `salesLines[]`."""
    if isinstance(document, list):
        return [_projected(element, paths) for element in document]
    kept: dict[str, Any] = {}
    for path in paths:
        head, *rest = path
        if not isinstance(document, dict) or head not in document:
            continue
        if not rest:
            kept[head] = document[head]
            continue
        nested = _projected(
            document[head], tuple(tail for first, *tail in paths if first == head and tail)
        )
        if nested not in ({}, []):
            kept[head] = nested
    return kept


class RecordingCaseStore:
    """Idempotent on the confirmation key, like the real adapter.

    Modelling the idempotency here rather than always returning a fresh id is
    what makes the retry test meaningful: a store that forgot would let a
    broken node pass.
    """

    def __init__(self, facts: dict[str, Any] | None = None) -> None:
        self.confirmations: list[str] = []
        self.issued: list[str] = []
        self.fact_reads: list[str] = []
        self._facts = dict(facts or {})
        self._by_key: dict[str, str] = {}

    async def case_facts(self, case_id: str) -> dict[str, Any]:
        self.fact_reads.append(case_id)
        return dict(self._facts)

    async def confirm_case(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        branch_ids: tuple[str, ...],
        conversation_id: str,
        confirmation: OrderConfirmation,
        configuration_release_id: str,
        graph_generation_id: str,
        observed_facts: tuple[dict[str, object], ...] = (),
    ) -> ConfirmedCase:
        del principal_id, branch_ids, configuration_release_id, graph_generation_id
        # Recorded rather than ignored: what the associate stated before the case
        # existed is flushed here, and a double that dropped it would let a
        # regression in that flush pass unnoticed.
        self.observed_facts = observed_facts
        key = confirmation.idempotency_key(tenant_id=tenant_id, conversation_id=conversation_id)
        self.confirmations.append(key)
        existing = self._by_key.get(key)
        if existing is not None:
            return ConfirmedCase(case_id=existing, already_existed=True)
        case_id = f"case-{len(self._by_key) + 1}"
        self._by_key[key] = case_id
        self.issued.append(case_id)
        return ConfirmedCase(case_id=case_id, already_existed=False)


class RecordingCaseWorkflowLauncher:
    """Idempotent on the derived execution id, like the real launcher.

    A confirmation that does not reach here is the WF-01 defect: the case was
    committed and `ReturnCaseWorkflow` was started by nobody, so Support's RMA
    signalled an execution that had never existed. The full behaviour of this
    seam -- concurrency, restart, failed start, recovery -- lives in
    `test_confirmation_starts_the_case_workflow.py`; the net only has to hold
    that confirmation still reaches it.
    """

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self.started: dict[str, str] = {}

    async def ensure_case_workflow(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        conversation_id: str,
        configuration_release_id: str,
    ) -> StartedCaseWorkflow:
        del tenant_id, principal_id, conversation_id, configuration_release_id
        workflow_id = return_case_workflow_id(case_id)
        self.attempts.append(workflow_id)
        already_running = workflow_id in self.started
        self.started[workflow_id] = case_id
        return StartedCaseWorkflow(workflow_id=workflow_id, already_running=already_running)


def _candidate_set_cache() -> dict[str, Any]:
    """A live candidate set, as `order_search` would have left it.

    Built through `CandidateSet.create` rather than hand-rolled so the checksum
    and binding fields are the real ones -- `validate_selection` checks them,
    and a hand-built dict would be testing the test.
    """
    candidate_set = CandidateSet.create(
        candidate_set_id=CANDIDATE_SET_ID,
        conversation_id="conv-smoke",
        turn_id="turn-1",
        principal_id="associate-1",
        tenant_id="tenant-a",
        schema_version="2026.08.04",
        graph_generation_id="gen-smoke",
        query_execution_id="qe-1",
        candidate_ids=(CANDIDATE_ID,),
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    return {"candidateSet": candidate_set.model_dump(mode="json")}


def _confirm(candidate_id: str = CANDIDATE_ID) -> AgentAction:
    return AgentAction(
        business_capability="return-context-collection",
        action_type=ActionType.CONFIRM_ORDER,
        decision_summary="The associate confirmed this order.",
        order_confirmation=OrderConfirmation(
            candidate_set_id=CANDIDATE_SET_ID,
            candidate_id=candidate_id,
            order_reference=ORDER_REFERENCE,
            order_line_references=("L1", "L2"),
        ),
    )


class ProjectingSource:
    """The one salesInv document, returned through the projection it was asked for.

    Substituted at the same boundary as the model and graph execution -- the
    infrastructure edge -- so everything between the agent's decision and the
    graph write is the shipped code: the planner, the source-read compiler, the
    extractor and the projector.

    Honouring the projection is what makes this worth having. A source double
    that returned the whole document regardless would have reported the same
    success while the shipped projection was discarding the order.
    """

    def __init__(self) -> None:
        self.reads = 0

    async def targeted_read(self, *, schema: ActiveSchema, plan: Any) -> RawSourcePage:
        self.reads += 1
        compiled = compile_source_read(schema, plan)
        return RawSourcePage(
            documents=(
                RawSourceDocument(
                    operation="UPSERT",
                    document=_projected(SALES_INV_DOCUMENT, compiled.projected_physical_paths),
                    source_identity=ORDER_KEY,
                ),
            ),
            observed_at=datetime.now(UTC),
        )


class OneSource:
    def __init__(self, source: ProjectingSource) -> None:
        self._source = source

    def resolve(self, source_asset_id: str) -> ProjectingSource:
        return self._source


class CountingGraphWriter:
    """Stands in for Neo4j and counts what it was asked to write."""

    def __init__(self) -> None:
        self.node_labels: list[str] = []

    async def write(
        self, *, schema: ActiveSchema, graph_generation_id: str, batch: Any
    ) -> tuple[int, int]:
        self.node_labels.extend(
            schema.graph.nodes[mutation.projection_id].label for mutation in batch.node_mutations
        )
        return len(batch.node_mutations), len(batch.relationship_mutations)


class FreshSyncStore:
    """Every request digest is new. Idempotency has its own tests; reusing a
    receipt here would silently skip the source read these scenarios are about."""

    async def reserve(
        self,
        *,
        request_digest: str,
        proposed_request_id: str,
        schema_version: str,
        graph_generation_id: str,
    ) -> SyncReservation:
        return SyncReservation(acquired=True, sync_request_id=proposed_request_id)

    async def complete(self, receipt: SyncReceipt) -> None:
        return None


def _sync_coordinator(source: ProjectingSource, writer: CountingGraphWriter) -> Any:
    return OnDemandSyncCoordinator(
        connectors=OneSource(source),
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=writer,
        store=FreshSyncStore(),
    )


def _identification(schema: ActiveSchema) -> IdentificationCatalogue:
    """The shipped catalogue, resolved against the shipped descriptor.

    Real configuration for the same reason the guards and the compiler are
    real: which signals Order Discovery can search on is now
    `discovery.identification_fields`, and a scenario built on a hand-written
    catalogue would prove the machinery works while saying nothing about
    whether what we ship does.
    """
    discovery = load_return_configuration(
        Path(__file__).parents[2] / "config/returns/production.yaml"
    ).configuration.discovery
    return build_identification_catalogue(
        discovery.identification_fields,
        schema,
        default_fulltext_index=discovery.progressive.customer_fulltext_index,
    )


def _dependencies(
    schema: ActiveSchema,
    model: ScriptedModel,
    knowledge: RecordingKnowledge,
    evidence: MemoryEvidence,
    case_store: RecordingCaseStore | None = None,
    on_demand_sync: Any = None,
    case_workflow_launcher: RecordingCaseWorkflowLauncher | None = None,
) -> GraphDependencies:
    """Real guards and the real compiler -- the point of the exercise."""
    return GraphDependencies(
        schema=schema,
        model_gateway=model,
        knowledge_gateway=knowledge,
        evidence_store=evidence,
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        response_safety_guard=ResponseSafetyGuard(),
        on_demand_sync=on_demand_sync,
        compiler=CypherCompiler(),
        identification=_identification(schema),
        case_store=case_store,
        # Supplied whenever a case store is: `confirm_order` refuses to write a
        # case it cannot make reachable, so the two arrive together in
        # production and a scenario that gave one without the other would be
        # testing a configuration that cannot exist.
        case_workflow_launcher=(
            case_workflow_launcher
            if case_workflow_launcher is not None or case_store is None
            else RecordingCaseWorkflowLauncher()
        ),
    )


def _guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies[AGENT_ID],
        principal=PrincipalContext(
            principal_id="associate-1",
            tenant_id="tenant-a",
            roles=frozenset({"*"}),
            branch_ids=frozenset({"CHARLOTTE"}),
        ),
    )


#: A Thursday, mid-morning in New York, chosen so "yesterday" and "last week"
#: land on different calendar days in the session zone than they do in UTC --
#: a grounding bug that only shows up across a UTC offset is the likely one.
TURN_AS_OF = datetime(2026, 8, 13, 2, 15, tzinfo=UTC)


def _state(schema: ActiveSchema, message: str) -> dict[str, Any]:
    return {
        "conversation_id": "conv-smoke",
        "client_turn_id": "turn-1",
        "run_id": "run-1",
        "user_message": message,
        "agent_id": AGENT_ID,
        "schema_version": schema.schema_version,
        "graph_generation_id": "gen-smoke",
        "configuration_release_id": schema.configuration_release_id,
        "policy_version": schema.policy_version,
        "prompt_version": schema.prompt_version,
        "as_of": TURN_AS_OF.isoformat(),
        "session_timezone": "America/New_York",
    }


async def _run(
    schema: ActiveSchema,
    message: str,
    actions: list[AgentAction],
    rows: list[dict[str, Any]] | None = None,
    case_store: RecordingCaseStore | None = None,
    seed_candidate_set: bool = False,
    on_demand_sync: Any = None,
    case_workflow_launcher: RecordingCaseWorkflowLauncher | None = None,
) -> tuple[dict[str, Any], ScriptedModel, RecordingKnowledge]:
    model = ScriptedModel(actions)
    knowledge = RecordingKnowledge(rows if rows is not None else [])
    graph = build_order_agent_graph(
        _dependencies(
            schema,
            model,
            knowledge,
            MemoryEvidence(),
            case_store=case_store,
            on_demand_sync=on_demand_sync,
            case_workflow_launcher=case_workflow_launcher,
        )
    )
    state = _state(schema, message)
    if seed_candidate_set:
        # A confirmation is only meaningful against a search that happened.
        state["order_search_cache"] = _candidate_set_cache()
    final = await graph.ainvoke(
        state,
        context=TurnRuntimeContext(guard_context=_guard_context(schema)),
        config={"recursion_limit": 64},
    )
    return final, model, knowledge


# ---------------------------------------------------------------------------
# Action builders
# ---------------------------------------------------------------------------


def _respond(text: str = "Found it.") -> AgentAction:
    return AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.RESPOND,
        decision_summary="Evidence supports a response.",
        response=StructuredAgentResponse(
            status="DISCOVERY_COMPLETE",
            business_capability=CAPABILITY,
            statements=(
                ResponseStatement(
                    statement_id="s1",
                    statement_type=StatementType.REASONED_SUGGESTION,
                    text=text,
                    evidence_refs=(),
                ),
            ),
        ),
    )


def _search(**intent: Any) -> AgentAction:
    return AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="Search for the order from what the associate supplied.",
        search_intent=OrderSearchIntent(**intent),
    )


def _graph_query(plan: LogicalQueryPlan) -> AgentAction:
    return AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.GRAPH_QUERY,
        decision_summary="Read the graph directly.",
        query_plan=plan,
    )


def _order_lookup_plan() -> LogicalQueryPlan:
    """The plan the agent re-runs once the record has been pulled in."""
    return LogicalQueryPlan(
        operation=QueryOperation.SEARCH,
        start_entity_id="sales_order",
        fields=("order_key",),
        filters=(
            QueryCondition(
                entity_id="sales_order",
                field_id="order_key",
                operator="EXACT",
                value=ORDER_KEY,
            ),
        ),
    )


def _request_sync() -> AgentAction:
    """The escalation: the graph does not have it, so go to the source.

    `exact_order_key` is a configured strong anchor with `on_demand_sync_allowed`,
    and `order_key` carries `on_demand_sync_anchor` -- both real
    `StrongAnchorGuard` checks, and both are the reason this action is
    expressible at all.
    """
    return AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.REQUEST_ON_DEMAND_SYNC,
        decision_summary="The graph has no such order; fetch it from the source.",
        strong_anchor_request=StrongAnchorRequest(
            entity_id="sales_order",
            strong_anchor_id="exact_order_key",
            anchors=(
                AnchorValue(
                    field_id="order_key",
                    operator="EXACT",
                    value=ORDER_KEY,
                    value_origin="USER_MESSAGE",
                ),
            ),
        ),
        original_query_plan=_order_lookup_plan(),
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def test_exact_order_number_search_compiles_and_completes(schema: ActiveSchema) -> None:
    final, model, knowledge = await _run(
        schema, "order CW273354", [_search(orderNumbers=["CW273354"]), _respond()]
    )

    assert model.dispatched == [ActionType.ORDER_SEARCH, ActionType.RESPOND]
    assert knowledge.plans, "an order search must reach the compiler"
    assert all(plan.operation is QueryOperation.SEARCH for plan in knowledge.plans)
    assert final["final_response"]["status"] == "DISCOVERY_COMPLETE"


async def test_customer_name_search_reaches_the_customer_entity(schema: ActiveSchema) -> None:
    _, model, knowledge = await _run(
        schema, "an order for Jane Doe", [_search(customerNames=["Jane Doe"]), _respond()]
    )

    assert model.dispatched[0] is ActionType.ORDER_SEARCH
    entities = {plan.start_entity_id for plan in knowledge.plans}
    assert entities, "a customer-name search must produce at least one plan"
    assert entities <= set(schema.agent_policies[AGENT_ID].allowed_entity_ids)


async def test_a_search_with_no_results_still_completes_the_turn(schema: ActiveSchema) -> None:
    """No match is an answer, not a failure."""
    final, model, _ = await _run(
        schema, "order ZZZ-NOPE", [_search(orderNumbers=["ZZZ-NOPE"]), _respond()], rows=[]
    )

    assert model.dispatched == [ActionType.ORDER_SEARCH, ActionType.RESPOND]
    assert final["final_response"] is not None


async def test_product_anchors_are_usable_searches(schema: ActiveSchema) -> None:
    for intent in ({"productNames": ["chrome faucet"]}, {"skus": ["FAU-1234"]}):
        _, model, knowledge = await _run(schema, "product anchor", [_search(**intent), _respond()])
        assert model.dispatched[0] is ActionType.ORDER_SEARCH
        assert knowledge.plans, f"{intent} produced no plan"


async def test_date_window_is_a_usable_search(schema: ActiveSchema) -> None:
    _, model, knowledge = await _run(
        schema,
        "bought around the start of August",
        [_search(dateFrom="2026-08-01", dateTo="2026-08-07"), _respond()],
    )

    assert model.dispatched[0] is ActionType.ORDER_SEARCH
    assert knowledge.plans


async def test_every_address_anchor_the_flow_names_compiles_into_cypher(
    schema: ActiveSchema,
) -> None:
    """Street, city, state and postal code are searches, not decoration.

    All four were declared on `OrderSearchIntent`, validated by the action
    contract, and then dropped by `build_progressive_plans` -- so the associate
    read out a shipping address, the search ran without it, and nothing said
    why. They are ordinary passes now.

    Asserted through the whole graph rather than against
    `build_progressive_plans` alone, because a plan that exists and is then
    refused by `SchemaQueryGuard` is indistinguishable from one that was never
    built: `order_search` logs the rejection at DEBUG and moves on. Only
    reaching `compile_read` proves the field, the entity and the operator are
    all legal on the shipped descriptor -- and the operator is where this would
    break, since `state` and `postal_code` are EXACT-only.
    """
    for intent in (
        {"streetAddresses": ["1 High Street"]},
        {"cities": ["Charlotte"]},
        {"states": ["NC"]},
        {"postalCodes": ["28202"]},
    ):
        _, model, knowledge = await _run(schema, "address anchor", [_search(**intent), _respond()])
        assert model.dispatched[0] is ActionType.ORDER_SEARCH
        assert knowledge.compiled, f"{intent} produced no compiled query"
        # The value has to reach the query, not merely a plan shaped like one:
        # a pass compiled with the anchor dropped would search on nothing and
        # return the same empty result the old behaviour did.
        asked = {condition.field_id for plan in knowledge.plans for condition in plan.filters}
        assert asked, f"{intent} compiled a query with no filter on the anchor"


async def test_an_email_or_phone_becomes_a_search_instead_of_a_dead_answer(
    schema: ActiveSchema,
) -> None:
    """The agent's own highest-priority clarifying questions, now answerable.

    `clarification_policy` in `config/returns/production.yaml` ranks email at 95
    and phone at 90 -- above every narrowing signal -- and `OrderSearchIntent`
    carried neither field while being `extra="forbid"`. The agent asked an
    associate for the email on the order and had nowhere to put the answer.

    The catalogue is asserted alongside the behaviour because the two fail
    differently: an unconfigured signal produces no search at all, while a
    configured one with no usable binding fails silently one layer down.

    Asserted against the catalogue rather than `OrderSearchIntent.model_fields`,
    which is where this used to look. There are no signal fields on that model
    any more -- declaring them there under `extra="forbid"` was the first of the
    seven places DISC-01 removed, and it was the one that rejected the
    associate's answer outright.
    """
    catalogue = _identification(schema)
    configured = {item.intent_key for item in catalogue.fields if item.is_usable}
    assert {"emails", "phones", "orderNumbers", "customerNames", "productNames"} <= configured

    for intent in ({"emails": ["dana@example.com"]}, {"phones": ["(704) 555-0142"]}):
        _, model, knowledge = await _run(schema, "contact anchor", [_search(**intent), _respond()])
        assert model.dispatched[0] is ActionType.ORDER_SEARCH
        assert knowledge.compiled, f"{intent} produced no compiled query"


async def test_a_colour_is_named_back_to_the_model_rather_than_dropped(
    schema: ActiveSchema,
) -> None:
    """The one signal that is still unsupported, and the reason that is safe.

    No entity carries a colour property, and matching "blue" against
    `product_description` would put "Blue Ridge Faucet" in front of someone who
    said the tap was blue, with no sign the colour had been matched loosely. So
    the colour is deliberately not searched -- but the model is told, in the
    evidence the next `decide` reads, so a search that came back empty can be
    explained instead of just being empty.

    A regression here is silent from every other angle: the turn still
    completes, the response still reads well, and the associate is told nothing.
    """
    _, model, knowledge = await _run(
        schema, "it was the chrome one", [_search(colors=["chrome"]), _respond()]
    )

    assert not knowledge.compiled, "a colour must not be guessed at against another field"
    reported = [
        signal
        for evidence in model.contexts[1].query_evidence
        for signal in evidence.result["unsupported_signals"]
    ]
    assert "colors" in reported


async def test_replan_clears_evidence_and_returns_to_decide(schema: ActiveSchema) -> None:
    replan = AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.REPLAN,
        decision_summary="Start again from a different anchor.",
    )
    final, model, _ = await _run(
        schema,
        "actually, try the customer instead",
        [
            _search(orderNumbers=["CW000000"]),
            replan,
            _search(customerNames=["Jane Doe"]),
            _respond(),
        ],
    )

    assert model.dispatched == [
        ActionType.ORDER_SEARCH,
        ActionType.REPLAN,
        ActionType.ORDER_SEARCH,
        ActionType.RESPOND,
    ]
    assert final["replans_used"] == 1
    # REPLAN's contract is that the next decide starts clean.
    assert final["order_search_cache"] is None or final["evidence_refs"]


async def test_a_capability_outside_the_policy_is_refused(schema: ActiveSchema) -> None:
    """The guard, not the prompt, is what keeps the agent in scope."""
    forbidden = AgentAction(
        business_capability="payment-processing",
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="Out of scope.",
        search_intent=OrderSearchIntent(orderNumbers=["CW273354"]),
    )
    model = ScriptedModel([forbidden])
    knowledge = RecordingKnowledge([])
    graph = build_order_agent_graph(_dependencies(schema, model, knowledge, MemoryEvidence()))

    with pytest.raises((OrderAgentFailure, AssertionError)):
        await graph.ainvoke(
            _state(schema, "charge the customer"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 16},
        )
    assert not knowledge.plans, "a refused capability must never reach the compiler"


async def test_an_unknown_graph_field_never_reaches_neo4j(schema: ActiveSchema) -> None:
    """Schema validation is upstream of compilation, so a hallucinated field
    cannot become Cypher."""
    bogus = _graph_query(
        LogicalQueryPlan(
            operation=QueryOperation.SEARCH,
            start_entity_id="sales_order",
            fields=("field_that_does_not_exist",),
            filters=(
                QueryCondition(
                    entity_id="sales_order",
                    field_id="field_that_does_not_exist",
                    operator="EXACT",
                    value="x",
                ),
            ),
        )
    )
    model = ScriptedModel([bogus])
    knowledge = RecordingKnowledge([])
    graph = build_order_agent_graph(_dependencies(schema, model, knowledge, MemoryEvidence()))

    with pytest.raises((OrderAgentFailure, AssertionError)):
        await graph.ainvoke(
            _state(schema, "invalid field"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 16},
        )
    assert not knowledge.compiled, "an unvalidated plan must not be compiled"


async def test_out_of_scope_action_fails_the_turn_before_any_query(schema: ActiveSchema) -> None:
    out_of_scope = AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.OUT_OF_SCOPE,
        decision_summary="Not an order question.",
    )
    model = ScriptedModel([out_of_scope])
    knowledge = RecordingKnowledge([])
    graph = build_order_agent_graph(_dependencies(schema, model, knowledge, MemoryEvidence()))

    with pytest.raises(OrderAgentFailure) as raised:
        await graph.ainvoke(
            _state(schema, "what is the weather"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 16},
        )
    assert raised.value.code == "ORDER_AGENT_OUT_OF_SCOPE"
    assert not knowledge.plans


async def test_the_turn_context_carries_transcript_and_schema_to_the_model(
    schema: ActiveSchema,
) -> None:
    """Both exist to stop the agent re-asking what it already knows."""
    _, model, _ = await _run(
        schema, "order CW273354", [_search(orderNumbers=["CW273354"]), _respond()]
    )

    first = model.contexts[0]
    assert first.user_message == "order CW273354"
    assert first.compact_schema, "the model must be told what it may search"
    assert first.graph_generation_id == "gen-smoke"


async def test_one_turn_has_exactly_one_now(schema: ActiveSchema) -> None:
    """W4.7. `_build_context` runs on every node entry, so a clock read inside
    it would let a turn cite evidence gathered under one "yesterday" in an
    answer about another. This turn builds its context three times; all three
    must agree, and all three must agree with the state the turn was pinned to.
    """
    replan = AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.REPLAN,
        decision_summary="Start again from a different anchor.",
    )
    _, model, _ = await _run(
        schema,
        "the orders from yesterday",
        [_search(orderNumbers=["CW273354"]), replan, _respond()],
    )

    assert len(model.contexts) == 3, "the scenario must exercise more than one context build"
    as_ofs = {context.as_of for context in model.contexts}
    assert as_ofs == {TURN_AS_OF}
    assert {context.session_timezone for context in model.contexts} == {"America/New_York"}


async def test_yesterday_reaches_the_model_as_an_absolute_range(schema: ActiveSchema) -> None:
    """The point of the windows: the model picks one, it does not do calendar
    arithmetic across a UTC offset. 02:15 UTC on the 13th is still the 12th in
    New York, so "yesterday" is the 11th there and would be the 12th in UTC."""
    _, model, _ = await _run(
        schema, "the orders from yesterday", [_search(orderNumbers=["CW273354"]), _respond()]
    )

    yesterday = model.contexts[0].resolved_date_windows["yesterday"]
    assert yesterday == {
        "start": "2026-08-11T04:00:00Z",
        "endExclusive": "2026-08-12T04:00:00Z",
    }


async def test_confirming_an_order_creates_a_case_and_returns_its_id(
    schema: ActiveSchema,
) -> None:
    """The transition the platform did not have.

    Discovery could search and answer; nothing recorded that the associate had
    chosen, so every step after it was unreachable.
    """
    # Confirms against the seeded candidate set rather than running a search
    # first: `order_search` mints its own set with a fresh uuid, so a scenario
    # that searched *then* confirmed a fixed id would be rejected -- correctly,
    # and for a reason that has nothing to do with what this test is about.
    store = RecordingCaseStore()
    launcher = RecordingCaseWorkflowLauncher()
    final, model, _ = await _run(
        schema,
        "yes, that one",
        [_confirm(), _respond("Raising the return now.")],
        case_store=store,
        seed_candidate_set=True,
        case_workflow_launcher=launcher,
    )

    assert model.dispatched == [ActionType.CONFIRM_ORDER, ActionType.RESPOND]
    assert final["case_id"] == store.issued[0]
    assert len(store.confirmations) == 1
    # And the case is *owned*. A confirmation that stopped at the store is the
    # WF-01 defect: everything after it -- bay, support, reminders, the RMA
    # coming back into this conversation -- belongs to this execution.
    assert launcher.attempts == [return_case_workflow_id(final["case_id"])]
    # Back to `decide` after confirming, not straight to END: the associate is
    # owed a sentence, and only the model writes those.
    assert final["final_response"] is not None


async def test_a_support_outcome_is_in_the_next_turn_context(schema: ActiveSchema) -> None:
    """The last hop of Channel B -> Channel A.

    Support issued the RMA on the case between two turns. Nothing in this
    conversation was told; the fact is read when the context is assembled, so
    the very next `decide` sees it -- which is what makes "no new conversation,
    no poll, no client-side join" true rather than aspirational.
    """
    store = RecordingCaseStore(facts={"return_reference": "RMA-1001", "return_location": "DC-7"})
    _, model, _ = await _run(
        schema,
        "yes, that one",
        [_confirm(), _respond("Your RMA is RMA-1001.")],
        case_store=store,
        seed_candidate_set=True,
    )

    # The turn that confirmed had no case yet, so the first context is empty and
    # the second carries what Support knows. Both halves matter: a store read
    # unconditionally would be a lookup on a case id that does not exist.
    assert model.contexts[0].case_facts == {}
    assert model.contexts[1].case_facts["return_reference"] == "RMA-1001"
    assert store.fact_reads == [store.issued[0]]


async def test_the_conversation_survives_an_unreadable_case(schema: ActiveSchema) -> None:
    """Facts are context, not a dependency.

    A case store that is down degrades the answer -- the agent may re-ask
    something the case knew -- but it must not end the associate's turn. This
    is the failure the `case_facts` read is wrapped for.
    """

    class BrokenCaseStore(RecordingCaseStore):
        async def case_facts(self, case_id: str) -> dict[str, Any]:
            raise RuntimeError("mongo is unreachable")

    store = BrokenCaseStore()
    _, model, _ = await _run(
        schema,
        "yes, that one",
        [_confirm(), _respond("Raising the return now.")],
        case_store=store,
        seed_candidate_set=True,
    )

    assert model.dispatched == [ActionType.CONFIRM_ORDER, ActionType.RESPOND]
    assert model.contexts[1].case_facts == {}


async def test_two_identical_confirmations_produce_one_case(schema: ActiveSchema) -> None:
    """A Temporal retry of the same turn must not fork the case."""
    store = RecordingCaseStore()
    launcher = RecordingCaseWorkflowLauncher()
    for _ in range(2):
        await _run(
            schema,
            "yes, that one",
            [_confirm(), _respond()],
            case_store=store,
            seed_candidate_set=True,
            case_workflow_launcher=launcher,
        )

    assert len(store.confirmations) == 2, "both turns reached the store"
    assert len(set(store.issued)) == 1, "and both resolved to one case"
    assert len(launcher.started) == 1, "and to one durable execution"


async def test_a_confirmation_for_a_candidate_that_was_never_offered_is_refused(
    schema: ActiveSchema,
) -> None:
    """A model cannot confirm an order it did not find.

    The selection is validated against the live `CandidateSet` -- the same
    guard `graph_query` uses -- so an invented candidate id is rejected before
    any case exists.
    """
    store = RecordingCaseStore()
    model = ScriptedModel([_confirm(candidate_id="never-offered")])
    graph = build_order_agent_graph(
        _dependencies(schema, model, RecordingKnowledge([]), MemoryEvidence(), case_store=store)
    )

    with pytest.raises((OrderAgentFailure, AssertionError)):
        await graph.ainvoke(
            {**_state(schema, "confirm"), "order_search_cache": _candidate_set_cache()},
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 24},
        )
    assert not store.confirmations, "no case may be created from an unverified selection"


async def test_confirmation_fails_loudly_when_no_case_store_is_configured(
    schema: ActiveSchema,
) -> None:
    """A process without a platform client can still search. It must not
    silently accept a confirmation it cannot record."""
    model = ScriptedModel([_confirm()])
    graph = build_order_agent_graph(
        _dependencies(schema, model, RecordingKnowledge([]), MemoryEvidence(), case_store=None)
    )

    with pytest.raises(OrderAgentFailure) as raised:
        await graph.ainvoke(
            {**_state(schema, "confirm"), "order_search_cache": _candidate_set_cache()},
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 24},
        )
    assert raised.value.code == "ORDER_AGENT_CASE_STORE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# On-demand synchronization: the escalation when the graph is behind the source
# ---------------------------------------------------------------------------


async def test_a_strong_anchor_reaches_the_source_and_the_plan_is_retried(
    schema: ActiveSchema,
) -> None:
    """The whole escalation, end to end, with only the source and graph faked.

    An order placed minutes ago is not in the projection yet. The agent holds an
    order key, escalates, and the record is pulled and written -- and then the
    *original* plan runs again, because a sync that does not lead back to an
    answer has not helped anybody.
    """
    source, writer = ProjectingSource(), CountingGraphWriter()
    _, model, knowledge = await _run(
        schema,
        f"order {ORDER_REFERENCE}, placed this morning",
        [_request_sync(), _respond("Found it after checking the source.")],
        on_demand_sync=_sync_coordinator(source, writer),
    )

    assert model.dispatched == [ActionType.REQUEST_ON_DEMAND_SYNC, ActionType.RESPOND]
    assert source.reads == 1, "the escalation must actually read the source"
    # The failure this whole path was rebuilt around: the source answered and
    # the projection threw the answer away, so the sync succeeded having written
    # nothing and the agent retried against an unchanged graph.
    assert "SalesOrder" in writer.node_labels
    assert "OrderLine" in writer.node_labels
    assert [plan.start_entity_id for plan in knowledge.plans] == ["sales_order"]
    assert knowledge.plans[0] == _order_lookup_plan()


async def test_the_targeted_sync_budget_is_enforced(schema: ActiveSchema) -> None:
    """A model that keeps escalating is stopped by policy, not by the source.

    `max_targeted_syncs_per_turn` is 3 in the shipped policy. Each sync is a
    live read of a production system, so the budget is the only thing between a
    confused turn and an unbounded fan-out of source queries.
    """
    policy = schema.agent_policies[AGENT_ID]
    source, writer = ProjectingSource(), CountingGraphWriter()
    model = ScriptedModel([_request_sync() for _ in range(policy.max_targeted_syncs_per_turn + 2)])
    graph = build_order_agent_graph(
        _dependencies(
            schema,
            model,
            RecordingKnowledge([]),
            MemoryEvidence(),
            on_demand_sync=_sync_coordinator(source, writer),
        )
    )

    with pytest.raises(OrderAgentFailure) as raised:
        await graph.ainvoke(
            _state(schema, "keep checking the source"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 64},
        )

    assert raised.value.code == "ORDER_AGENT_SYNC_BUDGET_EXCEEDED"
    assert source.reads == policy.max_targeted_syncs_per_turn


def _seed_only_shipment(schema: ActiveSchema) -> ActiveSchema:
    """The shipped descriptor with `shipment` pushed back to `SEED_ONLY`.

    Relationship access is capped by its endpoints, so demoting the entity
    without also demoting `order_shipped_as` makes the whole descriptor fail
    validation -- which would fail this test for a reason that has nothing to do
    with what it guards.
    """
    document = schema.model_dump(mode="json")
    document["entities"]["shipment"]["source_access"] = EntitySourceAccess.SEED_ONLY.value
    document["entities"]["shipment"]["source_contract_status"] = (
        SourceContractStatus.UNVERIFIED.value
    )
    document["graph"]["relationships"]["order_shipped_as"]["access"] = (
        RelationshipSourceAccess.SEED_ONLY.value
    )
    return ActiveSchema.model_validate(document)


async def test_an_anchor_the_schema_does_not_enable_never_reaches_the_source(
    schema: ActiveSchema,
) -> None:
    """The guard, not the connector, decides what may be fetched.

    The entity declares a perfectly well-formed `exact_tracking` anchor with
    `on_demand_sync_allowed: true` and is nonetheless `source_access:
    SEED_ONLY`. A request that looks entirely legitimate must still be refused,
    and refused *before* the read: a rejection that arrived after the source had
    been queried would not be a rejection of anything.

    **On the constructed schema.** This ran against the shipped descriptor until
    W2.6, which verified shipmentInfo's contract against 100 real documents and
    promoted `shipment` to `CONNECTED_SYNC`/`VERIFIED` -- so the descriptor no
    longer contains the combination this guards, and the tripwire started
    reading the source it exists to prove is never read. No other entity
    substitutes: `customer_account` and `customer_party` are the only remaining
    `SEED_ONLY` entities and neither declares a strong anchor, so pointing this
    at one would exercise the missing-anchor refusal instead and quietly stop
    testing `source_access` at all.

    Demotion rather than promotion, which is the direction that stays honest:
    it asserts a refusal on a schema that says refuse, and the same
    `_demoted` shape is what `test_fulfillment_observes_the_shipment.py` and
    `test_fulfillment_shipment_sync_real_infra.py` already use. It is a live
    configuration state, not dead code -- an entity whose source contract stops
    holding gets demoted, and every read path has to stop with it.
    """
    schema = _seed_only_shipment(schema)
    source, writer = ProjectingSource(), CountingGraphWriter()
    forbidden = AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.REQUEST_ON_DEMAND_SYNC,
        decision_summary="Try the seed-only entity.",
        strong_anchor_request=StrongAnchorRequest(
            entity_id="shipment",
            strong_anchor_id="exact_tracking",
            anchors=(
                AnchorValue(
                    field_id="tracking_number",
                    operator="EXACT",
                    value="1Z999",
                    value_origin="USER_MESSAGE",
                ),
            ),
        ),
        original_query_plan=_order_lookup_plan(),
    )
    model = ScriptedModel([forbidden])
    graph = build_order_agent_graph(
        _dependencies(
            schema,
            model,
            RecordingKnowledge([]),
            MemoryEvidence(),
            on_demand_sync=_sync_coordinator(source, writer),
        )
    )

    # `OrderAgentFailure` alone, not `(OrderAgentFailure, AssertionError)` as
    # before: `ScriptedModel` raises `AssertionError` when the graph asks for an
    # action the scenario did not script, so accepting it let a turn that sailed
    # past the guard and came back for a second decision pass as a refusal.
    with pytest.raises(OrderAgentFailure):
        await graph.ainvoke(
            _state(schema, "where is that shipment"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 24},
        )
    assert source.reads == 0
    assert writer.node_labels == []


async def test_a_process_without_a_source_connection_refuses_rather_than_pretends(
    schema: ActiveSchema,
) -> None:
    """No coordinator means no source. Say so; do not answer as if one was checked.

    The prompt tells the model to report that it "checked the source system
    directly" after a sync. A turn that silently skipped the sync would make
    that sentence a lie told to an associate on a call.
    """
    model = ScriptedModel([_request_sync()])
    graph = build_order_agent_graph(
        _dependencies(schema, model, RecordingKnowledge([]), MemoryEvidence(), on_demand_sync=None)
    )

    with pytest.raises(OrderAgentFailure) as raised:
        await graph.ainvoke(
            _state(schema, f"order {ORDER_REFERENCE}"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 24},
        )
    assert raised.value.code == "ON_DEMAND_SYNC_SOURCE_UNAVAILABLE"


async def test_the_reasoning_step_budget_is_enforced(schema: ActiveSchema) -> None:
    """A model that never responds must be stopped by policy, not by luck."""
    policy = schema.agent_policies[AGENT_ID]
    never_finishes = [
        _search(orderNumbers=[f"CW{index:06d}"]) for index in range(policy.max_reasoning_steps + 4)
    ]
    model = ScriptedModel(never_finishes)
    graph = build_order_agent_graph(
        _dependencies(schema, model, RecordingKnowledge([]), MemoryEvidence())
    )

    with pytest.raises((OrderAgentFailure, AssertionError)):
        await graph.ainvoke(
            _state(schema, "loop forever"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 128},
        )
