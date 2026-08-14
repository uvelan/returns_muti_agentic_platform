"""Confirmation starts the case's workflow. WF-01, from the production path in.

`ReturnCaseWorkflow` was a finished workflow that nothing started.
`tests/test_return_case_workflow_real_infra.py` proves the component
thoroughly and proves the *connection* not at all -- it starts the workflow
itself, which is exactly what production did not do. This file never calls
`start_workflow`. Every scenario enters through the compiled Order Discovery
graph, runs the real `confirm_order` node, the real `RepositoryCaseStore` and
the real `TemporalCaseWorkflowLauncher`, and asserts on what reached the
Temporal boundary.

**What is substituted, and only this.** Two datastores, each replaced by a
double that reproduces the property the code depends on rather than a
convenience:

* `FakeCaseRepository` reproduces Mongo's unique index on `confirmationKey` --
  the check and the insert are atomic with respect to the event loop, exactly
  as the index is atomic with respect to a second connection -- and the
  write-once semantics of `bind_case_workflow`.
* `FakeTemporalServer` reproduces execution-id uniqueness: a second start of a
  live id raises `WorkflowAlreadyStartedError`, which is the entire mechanism
  by which two confirmations converge on one workflow.

A double that let both writers win would make every idempotency assertion here
vacuous, so both are written to lose the race the same way the real thing does.

The end-to-end proof that a real Temporal execution appears for a real HTTP
turn is `tests/test_case_confirmation_starts_workflow_real_infra.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from return_platform.configuration.return_configuration import ReturnCaseTimingConfiguration
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.integration.case_store import RepositoryCaseStore
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.evidence import (
    QueryEvidence,
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
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentAction,
    AgentTurnContext,
    ModelInvocationResult,
    OrderConfirmation,
)
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.graph import build_order_agent_graph
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    GraphDependencies,
    TurnRuntimeContext,
)
from return_platform.dynamic_knowledge.order_agent.state import CandidateSet
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.workflows.return_case_launcher import TemporalCaseWorkflowLauncher
from return_platform.workflows.return_case_recovery import ReturnCaseWorkflowRecovery
from return_platform.workflows.return_case_workflow import (
    ReturnCaseWorkflowInput,
    return_case_workflow_id,
)

pytestmark = pytest.mark.asyncio

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
AGENT_ID = "order-discovery-agent"
CONVERSATION_ID = "conv-wf01"
CANDIDATE_SET_ID = "cs-wf01"
CANDIDATE_ID = "order:SO-9001"
ORDER_REFERENCE = "SO-9001"
TENANT_ID = "tenant-a"
PRINCIPAL_ID = "associate-1"
GRAPH_GENERATION_ID = "gen-wf01"
TASK_QUEUE = "return-platform-return-v1"


@pytest.fixture(scope="module")
def schema() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


# ---------------------------------------------------------------------------
# Doubles at the two datastore edges
# ---------------------------------------------------------------------------


class FakeTemporalServer:
    """Execution-id uniqueness, which is the whole idempotency mechanism.

    `start_failure` is how a Temporal outage is expressed: the client raises
    before any execution exists, which is precisely the window in which a case
    is durable and its workflow is not.
    """

    def __init__(self) -> None:
        self.executions: dict[str, ReturnCaseWorkflowInput] = {}
        self.start_attempts: list[str] = []
        self.task_queues: list[str] = []
        self.start_failure: Exception | None = None
        #: When set, every start waits here before touching the registry, and
        #: the gate opens only once `hold_until_attempts` starts have arrived.
        #: That is what puts two confirmations inside the same window on
        #: purpose rather than hoping the scheduler interleaves them.
        self.gate: asyncio.Event | None = None
        self.hold_until_attempts = 0

    async def start_workflow(
        self,
        run: Any,
        argument: ReturnCaseWorkflowInput,
        *,
        id: str,
        task_queue: str,
        **_: Any,
    ) -> Any:
        del run
        self.start_attempts.append(id)
        if self.start_failure is not None:
            raise self.start_failure
        if self.gate is not None:
            if len(self.start_attempts) >= self.hold_until_attempts:
                self.gate.set()
            await self.gate.wait()
        if id in self.executions:
            raise WorkflowAlreadyStartedError(id, "return-platform-return-case-v1")
        self.executions[id] = argument
        self.task_queues.append(task_queue)
        return object()


class FakeCaseRepository:
    """The slice of `OperationalRepository` the confirmation path touches.

    `create_case` models the unique partial index on `confirmationKey`:
    everything between reading the key and writing the document happens without
    an await, so a second coroutine cannot interleave into the middle of it.
    That is what makes the concurrency test below meaningful rather than
    decorative.
    """

    def __init__(self) -> None:
        self.cases: dict[str, dict[str, Any]] = {}
        self.facts: list[dict[str, Any]] = []
        self.bind_failure: Exception | None = None
        self._by_confirmation: dict[str, str] = {}

    async def find_case_by_confirmation(self, confirmation_key: str) -> dict[str, Any] | None:
        case_id = self._by_confirmation.get(confirmation_key)
        return self.cases.get(case_id) if case_id is not None else None

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        return {fact["fact_name"]: fact for fact in self.facts if fact["case_id"] == case_id}

    async def create_case(self, **fields: Any) -> dict[str, Any]:
        # Yield first, then decide-and-write atomically: the interleaving point
        # a real second connection gets is before the index is consulted, never
        # inside it.
        await asyncio.sleep(0)
        key = fields.get("confirmation_key")
        if key is not None and key in self._by_confirmation:
            return self.cases[self._by_confirmation[key]]
        case_id = str(fields["case_id"])
        document = {
            "caseId": case_id,
            "tenantId": fields.get("tenant_id"),
            "principalId": fields.get("principal_id"),
            "status": "GATHERING_INFO",
            "channelAConversationId": fields.get("channel_a_conversation_id"),
            "configurationReleaseId": fields.get("configuration_release_id"),
            "confirmationKey": key,
            "workflowId": None,
            "createdAt": datetime.now(UTC) - timedelta(minutes=5),
            "version": 0,
        }
        self.cases[case_id] = document
        if key is not None:
            self._by_confirmation[key] = case_id
        return document

    async def append_case_fact(self, **fields: Any) -> dict[str, Any]:
        self.facts.append(fields)
        return dict(fields)

    async def bind_case_workflow(self, case_id: str, *, workflow_id: str) -> bool:
        if self.bind_failure is not None:
            raise self.bind_failure
        case = self.cases[case_id]
        if case["workflowId"] is None:
            case["workflowId"] = workflow_id
            return True
        if case["workflowId"] != workflow_id:  # pragma: no cover - derived id cannot differ
            raise AssertionError("a case was repointed at a second workflow")
        return False

    async def list_cases_without_workflow(
        self, *, created_before: datetime, limit: int = 100
    ) -> list[dict[str, Any]]:
        return [
            case
            for case in self.cases.values()
            if case["workflowId"] is None and case["createdAt"] < created_before
        ][:limit]


class ScriptedModel:
    """A script, not a sampler. Deterministic without a seed."""

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = list(actions)
        self.dispatched: list[ActionType] = []

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        del context
        if not self._actions:  # pragma: no cover - a script that ran out is a test bug
            raise AssertionError("the scripted model ran out of actions")
        action = self._actions.pop(0)
        self.dispatched.append(action.action_type)
        return ModelInvocationResult(
            action=action,
            provider="scripted",
            model="scripted",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **_: Any) -> ModelInvocationResult:  # pragma: no cover
        raise AssertionError("no correction is expected in these scenarios")

    async def correct_response(self, **_: Any) -> ModelInvocationResult:  # pragma: no cover
        raise AssertionError("no correction is expected in these scenarios")


class UnusedKnowledge:
    """These scenarios confirm against a seeded candidate set; nothing queries."""

    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]:
        del schema, agent_id
        return {}

    async def schema_details(self, *_: Any, **__: Any) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def execute(self, **_: Any) -> Any:  # pragma: no cover
        raise AssertionError("no graph query is expected in these scenarios")


class MemoryEvidence:
    def __init__(self) -> None:
        self._records: dict[str, QueryEvidence] = {}

    async def put(self, *, run_id: str, evidence: QueryEvidence) -> None:
        del run_id
        self._records[evidence.query_execution_id] = evidence

    async def get_many(self, query_execution_ids: Any) -> tuple[QueryEvidence, ...]:
        return tuple(
            self._records[value] for value in query_execution_ids if value in self._records
        )


# ---------------------------------------------------------------------------
# The canonical path, assembled from real components
# ---------------------------------------------------------------------------


def _launcher(
    server: FakeTemporalServer, repository: FakeCaseRepository
) -> TemporalCaseWorkflowLauncher:
    """The real launcher. Only the client underneath it is a double."""
    return TemporalCaseWorkflowLauncher(
        client=server,  # type: ignore[arg-type]
        repository=repository,
        timings=ReturnCaseTimingConfiguration(),
        task_queue=TASK_QUEUE,
    )


def _dependencies(
    schema: ActiveSchema,
    model: ScriptedModel,
    repository: FakeCaseRepository,
    server: FakeTemporalServer,
    *,
    launcher: TemporalCaseWorkflowLauncher | None = None,
) -> GraphDependencies:
    return GraphDependencies(
        schema=schema,
        model_gateway=model,
        knowledge_gateway=UnusedKnowledge(),
        evidence_store=MemoryEvidence(),
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        response_safety_guard=ResponseSafetyGuard(),
        on_demand_sync=None,
        compiler=CypherCompiler(),
        case_store=RepositoryCaseStore(repository),
        case_workflow_launcher=launcher or _launcher(server, repository),
    )


def _guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies[AGENT_ID],
        principal=PrincipalContext(
            principal_id=PRINCIPAL_ID,
            tenant_id=TENANT_ID,
            roles=frozenset({"*"}),
            branch_ids=frozenset({"CHARLOTTE"}),
        ),
    )


def _candidate_set_cache(conversation_id: str = CONVERSATION_ID) -> dict[str, Any]:
    candidate_set = CandidateSet.create(
        candidate_set_id=CANDIDATE_SET_ID,
        conversation_id=conversation_id,
        turn_id="turn-1",
        principal_id=PRINCIPAL_ID,
        tenant_id=TENANT_ID,
        schema_version="2026.08.04",
        graph_generation_id=GRAPH_GENERATION_ID,
        query_execution_id="qe-1",
        candidate_ids=(CANDIDATE_ID,),
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    return {"candidateSet": candidate_set.model_dump(mode="json")}


def _confirm() -> AgentAction:
    return AgentAction(
        business_capability="return-context-collection",
        action_type=ActionType.CONFIRM_ORDER,
        decision_summary="The associate confirmed this order.",
        order_confirmation=OrderConfirmation(
            candidate_set_id=CANDIDATE_SET_ID,
            candidate_id=CANDIDATE_ID,
            order_reference=ORDER_REFERENCE,
            order_line_references=("L1", "L2"),
        ),
    )


def _respond(message: str = "Raising the return now.") -> AgentAction:
    return AgentAction(
        business_capability="return-context-collection",
        action_type=ActionType.RESPOND,
        decision_summary="Telling the associate the return is under way.",
        response=StructuredAgentResponse(
            status="DISCOVERY_COMPLETE",
            business_capability="return-context-collection",
            statements=(
                ResponseStatement(
                    statement_id="s1",
                    statement_type=StatementType.REASONED_SUGGESTION,
                    text=message,
                    evidence_refs=(),
                ),
            ),
        ),
    )


def _state(schema: ActiveSchema, conversation_id: str = CONVERSATION_ID) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "client_turn_id": "turn-1",
        "run_id": "run-1",
        "user_message": "Yes, that's the order.",
        "agent_id": AGENT_ID,
        "schema_version": schema.schema_version,
        "graph_generation_id": GRAPH_GENERATION_ID,
        "configuration_release_id": schema.configuration_release_id,
        "policy_version": schema.policy_version,
        "prompt_version": schema.prompt_version,
        "as_of": datetime(2026, 8, 13, 2, 15, tzinfo=UTC).isoformat(),
        "session_timezone": "America/New_York",
        "order_search_cache": _candidate_set_cache(conversation_id),
    }


async def _confirm_turn(
    schema: ActiveSchema,
    repository: FakeCaseRepository,
    server: FakeTemporalServer,
    *,
    conversation_id: str = CONVERSATION_ID,
    launcher: TemporalCaseWorkflowLauncher | None = None,
) -> dict[str, Any]:
    """One turn, through the compiled graph, exactly as the activity runs it."""
    model = ScriptedModel([_confirm(), _respond()])
    graph = build_order_agent_graph(
        _dependencies(schema, model, repository, server, launcher=launcher)
    )
    return await graph.ainvoke(
        _state(schema, conversation_id),
        context=TurnRuntimeContext(guard_context=_guard_context(schema)),
        config={"recursion_limit": 64},
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def test_a_confirmation_starts_exactly_one_case_workflow(schema: ActiveSchema) -> None:
    """The defect, stated as an assertion.

    Before this commit the node created a case and stopped. Support's RMA
    submission then signalled `return-case-<id>` and Temporal answered
    NOT_FOUND, so the reply was lost.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()

    final = await _confirm_turn(schema, repository, server)

    case_id = final["case_id"]
    assert case_id, "the turn confirmed and produced a case"
    workflow_id = return_case_workflow_id(case_id)
    assert list(server.executions) == [workflow_id], "exactly one execution, at the derived id"
    assert server.task_queues == [TASK_QUEUE], "started on the queue the case worker polls"

    # The input carries identity and policy, never business data -- and the
    # timings are the configured ones rather than constants.
    started = server.executions[workflow_id]
    assert started.case_id == case_id
    assert started.tenant_id == TENANT_ID
    assert started.principal_id == PRINCIPAL_ID
    assert started.conversation_id == CONVERSATION_ID
    defaults = ReturnCaseTimingConfiguration()
    assert started.timings.support_response_wait_seconds == defaults.support_response_wait_seconds
    assert started.timings.max_reminders == defaults.max_reminders

    assert repository.cases[case_id]["workflowId"] == workflow_id, "the case records its execution"


async def test_a_duplicate_confirmation_attaches_and_starts_no_second_workflow(
    schema: ActiveSchema,
) -> None:
    """Two confirmations of the same order in the same conversation.

    One case by the confirmation key, and therefore one workflow id -- the
    second start is refused by Temporal and adopted.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()

    first = await _confirm_turn(schema, repository, server)
    second = await _confirm_turn(schema, repository, server)

    assert first["case_id"] == second["case_id"], "one confirmation identity, one case"
    assert len(repository.cases) == 1
    assert len(server.executions) == 1, "no second execution"
    assert server.start_attempts == [return_case_workflow_id(first["case_id"])] * 2, (
        "the second turn did attempt the start and was refused, rather than skipping it"
    )


async def test_the_same_turn_retried_converges_on_one_workflow(schema: ActiveSchema) -> None:
    """A Temporal activity retry of one turn, which is what an HTTP retry becomes.

    `submit_turn` carries the client turn id and idempotency key, so a retried
    request re-executes the identical turn against the identical candidate set.
    Nothing about that may produce a second case or a second execution.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()

    attempts = [await _confirm_turn(schema, repository, server) for _ in range(3)]

    assert len({attempt["case_id"] for attempt in attempts}) == 1
    assert len(server.executions) == 1
    assert len(repository.facts) == 2, (
        "the confirmation facts were appended once, by the turn that created the case"
    )


async def test_two_simultaneous_confirmations_converge_on_one_case_and_one_workflow(
    schema: ActiveSchema,
) -> None:
    """CASE-01: the true concurrent confirmation, not a sequential stand-in.

    Both turns read no existing case, both create one and both start a
    workflow. The unique confirmation key collapses the first pair and the
    unique execution id collapses the second, and the two mechanisms have to
    agree -- a case store that returned two ids would produce two workflows
    however idempotent the launcher is.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()
    # Hold both starts open until both have been attempted, so the second one
    # cannot simply arrive after the first has finished registering.
    server.gate = asyncio.Event()
    server.hold_until_attempts = 2

    first, second = await asyncio.gather(
        _confirm_turn(schema, repository, server),
        _confirm_turn(schema, repository, server),
    )

    assert first["case_id"] == second["case_id"]
    assert len(repository.cases) == 1, "one case"
    assert len(server.executions) == 1, "one workflow"
    assert len(server.start_attempts) == 2, "both really raced for it"


async def test_confirming_against_an_already_running_workflow_adopts_it(
    schema: ActiveSchema,
) -> None:
    """The case's workflow is already running -- started earlier, or recovered.

    The confirmation must adopt it. Restarting would discard a live support
    wait and the reminder count with it.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()

    first = await _confirm_turn(schema, repository, server)
    workflow_id = return_case_workflow_id(first["case_id"])
    running = server.executions[workflow_id]

    await _confirm_turn(schema, repository, server)

    assert server.executions[workflow_id] is running, "the live execution was left untouched"


async def test_a_failed_start_fails_the_turn_and_leaves_a_recoverable_case(
    schema: ActiveSchema,
) -> None:
    """The crash window, and the path out of it.

    Temporal is unreachable at the moment the case has just been committed.
    The turn must fail -- reporting success would tell the associate a return
    is under way that nothing owns -- and the case must be left in a state the
    recovery sweep can finish. The sweep then starts the workflow the case was
    owed, through the same launcher, without the associate coming back.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()
    server.start_failure = RuntimeError("temporal is unreachable")

    with pytest.raises(OrderAgentFailure) as failure:
        await _confirm_turn(schema, repository, server)
    assert failure.value.code == "ORDER_AGENT_CASE_WORKFLOW_START_FAILED"
    assert failure.value.retryable is True

    assert len(repository.cases) == 1, "the case is durable; that is the whole problem"
    case_id = next(iter(repository.cases))
    assert repository.cases[case_id]["workflowId"] is None
    assert not server.executions

    # Temporal comes back. Nothing else happens -- no new turn, no associate.
    server.start_failure = None
    recovery = ReturnCaseWorkflowRecovery(
        launcher=_launcher(server, repository),
        repository=repository,
        grace_seconds=0.0,
    )
    assert await recovery.recover_once() == 1

    workflow_id = return_case_workflow_id(case_id)
    assert list(server.executions) == [workflow_id]
    assert repository.cases[case_id]["workflowId"] == workflow_id
    assert await recovery.recover_once() == 0, "a recovered case leaves the queue"


async def test_recovery_adopts_a_workflow_whose_link_write_was_lost(
    schema: ActiveSchema,
) -> None:
    """The narrower crash: the start landed and the link write did not.

    The case looks unstarted and is not. Recovery must converge on the running
    execution rather than replace it, and must repair the link so the case
    stops appearing in the queue.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()
    repository.bind_failure = RuntimeError("mongo write failed")

    final = await _confirm_turn(schema, repository, server)
    case_id = final["case_id"]
    workflow_id = return_case_workflow_id(case_id)
    assert list(server.executions) == [workflow_id], "the workflow really did start"
    assert repository.cases[case_id]["workflowId"] is None, "and the link really was lost"

    repository.bind_failure = None
    recovery = ReturnCaseWorkflowRecovery(
        launcher=_launcher(server, repository),
        repository=repository,
        grace_seconds=0.0,
    )
    assert await recovery.recover_once() == 1

    assert len(server.executions) == 1, "adopted, not replaced"
    assert repository.cases[case_id]["workflowId"] == workflow_id


async def test_a_restarted_confirmation_process_starts_no_second_workflow(
    schema: ActiveSchema,
) -> None:
    """The order-discovery worker restarts and the associate confirms again.

    A fresh process means a fresh launcher and a fresh Temporal client holding
    no memory of the first start. Convergence has to come from the derived
    execution id, which survives the restart because it is derived, not stored.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()

    first = await _confirm_turn(schema, repository, server)

    # A new process: new launcher, new graph, same durable stores.
    restarted = _launcher(server, repository)
    second = await _confirm_turn(schema, repository, server, launcher=restarted)

    assert first["case_id"] == second["case_id"]
    assert len(server.executions) == 1


async def test_a_process_that_cannot_start_the_workflow_creates_no_case(
    schema: ActiveSchema,
) -> None:
    """The refusal is before the write, not after it.

    A coordinator assembled without a launcher must not commit a case it has
    no way to make reachable -- that is the orphan WF-01 produced, arrived at
    from a different direction.
    """
    repository = FakeCaseRepository()
    server = FakeTemporalServer()
    model = ScriptedModel([_confirm(), _respond()])
    dependencies = _dependencies(schema, model, repository, server)
    graph = build_order_agent_graph(
        GraphDependencies(
            **{
                **{
                    field: getattr(dependencies, field)
                    for field in dependencies.__dataclass_fields__
                },
                "case_workflow_launcher": None,
            }
        )
    )

    with pytest.raises(OrderAgentFailure) as failure:
        await graph.ainvoke(
            _state(schema),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 64},
        )

    assert failure.value.code == "ORDER_AGENT_CASE_WORKFLOW_UNAVAILABLE"
    assert not repository.cases, "nothing durable was written"


async def test_recovery_ignores_a_case_that_is_not_a_channel_a_confirmation(
    schema: ActiveSchema,
) -> None:
    """A case without a conversation is not a confirmation this owns.

    `ReturnCaseWorkflow` opens a support thread with a person on the other end
    of it. Starting one for a document that reached the collection some other
    way would put a human in a conversation nobody asked for.
    """
    del schema
    repository = FakeCaseRepository()
    server = FakeTemporalServer()
    await repository.create_case(
        case_id="case-orphan",
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        channel_a_conversation_id=None,
        configuration_release_id="release-1",
        confirmation_key=None,
    )

    recovery = ReturnCaseWorkflowRecovery(
        launcher=_launcher(server, repository),
        repository=repository,
        grace_seconds=0.0,
    )

    assert await recovery.recover_once() == 0
    assert not server.start_attempts
