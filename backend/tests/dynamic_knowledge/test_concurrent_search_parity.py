"""Concurrent progressive search must produce exactly what serial produced.

Progressive search decomposes one intent into several narrow reads that share no
state and write nothing, so they can run at once. The risk in doing that is not
that the rows come back wrong; it is that something *about the ordering* changes
and nobody notices, because a search that returns the same set of candidates in
a different order still looks like it works.

Three things could shift, and each has a test here rather than a reassurance:

* which searches run, when the per-turn budget truncates the set;
* which order results are collected in, when they complete out of order;
* which failure surfaces, when more than one read fails at once.

Parity is asserted against serial execution computed with the production ranker,
not against a recorded expectation -- a golden file would pin whatever the code
did on the day it was written, including a bug.

The shipped catalogue and the real guards, compiler and ranker are used
throughout. Only the graph itself is a double, because the property under test
is the shape of the fan-out and not what Neo4j returns.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from langgraph.runtime import Runtime

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.evidence import QueryEvidence
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
    OrderSearchIntent,
)
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    GraphDependencies,
    TurnRuntimeContext,
    make_order_search_node,
)
from return_platform.dynamic_knowledge.order_agent.identification import (
    IdentificationCatalogue,
    build_identification_catalogue,
)
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    build_search_program,
    rank_search_results,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

pytestmark = pytest.mark.asyncio

REPOSITORY_BACKEND = Path(__file__).parents[2]

#: An intent that fans out widely on purpose: six signals across four entities,
#: which is the shape that made an associate wait for six serial round trips.
WIDE_INTENT: dict[str, Any] = {
    "orderNumbers": ["CW273354"],
    "customerNames": ["Melgon"],
    "emails": ["dana@example.com"],
    "cities": ["Dallas"],
    "states": ["TX"],
    "postalCodes": ["75201"],
}


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    return load_active_schema(
        REPOSITORY_BACKEND / "config/dynamic_knowledge/active-schema.return-order.yaml"
    )


@pytest.fixture(scope="module")
def catalogue(production_schema: ActiveSchema) -> IdentificationCatalogue:
    discovery = load_return_configuration(
        REPOSITORY_BACKEND / "config/returns/production.yaml"
    ).configuration.discovery
    return build_identification_catalogue(
        discovery.identification_fields,
        production_schema,
        default_fulltext_index=discovery.progressive.customer_fulltext_index,
    )


class _Graph:
    """A graph double that can be told how slowly each read completes.

    `delays` is keyed by the first filter's field id, so a test can make the
    first-issued plan the last to finish and see whether anything downstream
    depended on completion order.
    """

    def __init__(
        self,
        rows_by_field: dict[str, list[dict[str, Any]]] | None = None,
        *,
        delays: dict[str, float] | None = None,
        fail: set[str] | None = None,
    ) -> None:
        self.rows_by_field = rows_by_field or {}
        self.delays = delays or {}
        self.fail = fail or set()
        self.executed: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]:
        del schema, agent_id
        return {}

    async def schema_details(
        self, schema: ActiveSchema, entity_ids: tuple[str, ...]
    ) -> dict[str, Any]:
        del schema, entity_ids
        return {}

    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any:
        del schema, graph_generation_id, compiled_cypher, parameters
        key = plan.filters[0].field_id if plan.filters else (plan.fulltext_field_id or "?")
        self.executed.append(key)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delays.get(key, 0.0))
            if key in self.fail:
                raise RuntimeError(f"graph read failed for {key}")
            rows = self.rows_by_field.get(key, [])
            return {"rows": rows, "count": len(rows)}
        finally:
            self.in_flight -= 1


class _Evidence:
    def __init__(self) -> None:
        self.by_id: dict[str, QueryEvidence] = {}

    async def put(self, *, run_id: str, evidence: QueryEvidence) -> None:
        del run_id
        self.by_id[evidence.query_execution_id] = evidence

    async def get_many(self, query_execution_ids: Any) -> tuple[QueryEvidence, ...]:
        return tuple(self.by_id[identifier] for identifier in query_execution_ids)


class _UnusedModel:
    async def decide(self, context: Any) -> Any:
        raise AssertionError("order_search must not invoke the reasoning model")

    async def correct_action(self, **kwargs: Any) -> Any:
        raise AssertionError("order_search must not invoke the reasoning model")

    async def correct_response(self, **kwargs: Any) -> Any:
        raise AssertionError("order_search must not invoke the reasoning model")


def _dependencies(
    schema: ActiveSchema, catalogue: IdentificationCatalogue, graph: _Graph, evidence: _Evidence
) -> GraphDependencies:
    """Real guards, real compiler, real catalogue, real ranker."""
    return GraphDependencies(
        schema=schema,
        model_gateway=_UnusedModel(),
        knowledge_gateway=graph,
        evidence_store=evidence,
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        response_safety_guard=ResponseSafetyGuard(),
        on_demand_sync=None,
        compiler=CypherCompiler(),
        identification=catalogue,
    )


def _guard_context(schema: ActiveSchema, *, budget: int | None = None) -> GuardContext:
    policy = schema.agent_policies["order-discovery-agent"]
    if budget is not None:
        policy = policy.model_copy(update={"max_graph_queries_per_turn": budget})
    return GuardContext(
        schema=schema,
        agent_policy=policy,
        principal=PrincipalContext(
            principal_id="assoc-1", tenant_id="tenant-1", roles=frozenset({"associate"})
        ),
    )


def _state(intent: dict[str, Any]) -> dict[str, Any]:
    action = AgentAction(
        business_capability="order-discovery",
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="Search on everything the associate gave.",
        search_intent=OrderSearchIntent.model_validate(intent),
    )
    return {
        "conversation_id": f"c-{uuid4().hex[:8]}",
        "client_turn_id": "ct1",
        "run_id": str(uuid4()),
        "agent_id": "order-discovery-agent",
        "graph_generation_id": "generation-1",
        "as_of": datetime(2026, 8, 14, 9, 30, tzinfo=UTC).isoformat(),
        "session_timezone": "UTC",
        "evidence_refs": (),
        "order_search_cache": None,
        "action": action.model_dump(mode="json"),
        "queries_used": 0,
    }


async def _run(
    schema: ActiveSchema,
    catalogue: IdentificationCatalogue,
    graph: _Graph,
    intent: dict[str, Any],
    *,
    budget: int | None = None,
) -> tuple[dict[str, Any], _Evidence]:
    evidence = _Evidence()
    node = make_order_search_node(_dependencies(schema, catalogue, graph, evidence))
    runtime = Runtime(
        context=TurnRuntimeContext(guard_context=_guard_context(schema, budget=budget))
    )
    result = await node(_state(intent), runtime)
    return result, evidence


def _ranked_from(result: dict[str, Any], evidence: _Evidence) -> dict[str, Any]:
    return evidence.by_id[result["order_search_cache"]["evidenceRef"]].result


def _serial_reference(
    schema: ActiveSchema,
    catalogue: IdentificationCatalogue,
    rows_by_field: dict[str, list[dict[str, Any]]],
    intent: dict[str, Any],
) -> dict[str, Any]:
    """What serial execution produces, computed with the production ranker.

    Deliberately not a recorded expectation: a golden file pins whatever the
    code did the day it was written, bug included. This re-derives the answer
    from the same plan order the serial loop walked and the same ranker the node
    uses, so the comparison is against the semantics rather than against a
    snapshot of them.
    """
    parsed = OrderSearchIntent.model_validate(intent)
    program = build_search_program(parsed, catalogue)
    raw_results = []
    for item in program.primary:
        key = (
            item.plan.filters[0].field_id
            if item.plan.filters
            else (item.plan.fulltext_field_id or "?")
        )
        rows = rows_by_field.get(key, [])
        raw_results.append({"rows": rows, "count": len(rows)})
    return rank_search_results(parsed, raw_results, program=program)


ROWS: dict[str, list[dict[str, Any]]] = {
    "sales_order_number": [{"sales_order_number": "CW273354", "customer_id": "C1"}],
    "customer_name": [{"customer_id": "C2", "customer_name": "Melgon Heating"}],
    "email": [{"customer_id": "C3", "email": "dana@example.com"}],
    "city": [{"customer_id": "C4", "city": "Dallas"}],
    "ship_to_city": [{"sales_order_number": "SO-9", "ship_to_city": "Dallas"}],
    "state": [{"customer_id": "C5", "state": "TX"}],
    "ship_to_state": [{"sales_order_number": "SO-8", "ship_to_state": "TX"}],
    "postal_code": [{"customer_id": "C6", "postal_code": "75201"}],
    "ship_to_postal_code": [{"sales_order_number": "SO-7", "ship_to_postal_code": "75201"}],
}


# --- parity ------------------------------------------------------------------


async def test_concurrent_execution_matches_serial_execution_exactly(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """The gate. Identical ranked output, including order.

    Not "same set" and not "similar": ranking decides which candidate the
    associate reads first, so a reordering is a behaviour change even when every
    row is present.
    """
    graph = _Graph(ROWS)
    result, evidence = await _run(production_schema, catalogue, graph, WIDE_INTENT)

    actual = _ranked_from(result, evidence)
    expected = _serial_reference(production_schema, catalogue, ROWS, WIDE_INTENT)

    assert actual["total_found"] == expected["total_found"]
    assert [candidate["candidate_id"] for candidate in actual["candidates"]] == [
        candidate["candidate_id"] for candidate in expected["candidates"]
    ]
    assert actual["candidates"] == expected["candidates"]


async def test_results_are_collected_in_plan_order_not_completion_order(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """The read issued first finishes last, and nothing downstream notices.

    This is the failure a naive `gather` introduces and the one that would be
    hardest to see in production: every row is present, and the candidate the
    associate is shown first is whichever query happened to win the race.
    """
    inverted = _Graph(
        ROWS,
        delays={
            "sales_order_number": 0.05,
            "customer_name": 0.04,
            "email": 0.03,
            "postal_code": 0.0,
        },
    )
    delayed_result, delayed_evidence = await _run(
        production_schema, catalogue, inverted, WIDE_INTENT
    )
    instant_result, instant_evidence = await _run(
        production_schema, catalogue, _Graph(ROWS), WIDE_INTENT
    )

    assert _ranked_from(delayed_result, delayed_evidence) == _ranked_from(
        instant_result, instant_evidence
    )


async def test_the_reads_really_do_overlap(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """Otherwise every parity test above passes against unchanged serial code."""
    graph = _Graph(ROWS, delays=dict.fromkeys(ROWS, 0.02))

    await _run(production_schema, catalogue, graph, WIDE_INTENT)

    assert graph.max_in_flight > 1


async def test_the_whole_fan_out_costs_about_one_round_trip(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """PERF-01's actual claim, stated as time rather than as structure.

    Nine reads at 30ms each is 270ms serially. Asserted generously -- this is a
    test of concurrency, not of the event loop's scheduling latency -- but it
    fails outright if the fan-out ever goes back to being serial.
    """
    graph = _Graph(ROWS, delays=dict.fromkeys(ROWS, 0.03))

    started = asyncio.get_running_loop().time()
    await _run(production_schema, catalogue, graph, WIDE_INTENT)
    elapsed = asyncio.get_running_loop().time() - started

    assert len(graph.executed) >= 6
    assert elapsed < 0.15


# --- which searches run ------------------------------------------------------


async def test_the_budget_still_truncates_and_keeps_the_same_prefix(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """Concurrency must not smuggle extra queries past the per-turn budget.

    And it must truncate at the same place: DISC-03 orders the plans most
    discriminating first, so the surviving prefix is the one that matters.
    """
    graph = _Graph(ROWS)
    program = build_search_program(OrderSearchIntent.model_validate(WIDE_INTENT), catalogue)
    expected_prefix = [
        item.plan.filters[0].field_id for item in program.primary[:3] if item.plan.filters
    ]

    result, _ = await _run(production_schema, catalogue, graph, WIDE_INTENT, budget=3)

    assert len(graph.executed) == 3
    assert graph.executed == expected_prefix
    assert result["queries_used"] == 3


async def test_a_plan_the_guard_rejects_still_costs_no_budget(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """Unchanged from serial, and easy to lose when compilation moves.

    If a rejected plan consumed budget, an associate who supplied one signal the
    schema cannot answer would silently lose a search that could have.
    """
    restricted = production_schema.model_copy(
        update={
            "agent_policies": {
                **production_schema.agent_policies,
                "order-discovery-agent": production_schema.agent_policies[
                    "order-discovery-agent"
                ].model_copy(update={"allowed_entity_ids": frozenset({"sales_order", "customer"})}),
            }
        }
    )
    graph = _Graph(ROWS)

    await _run(restricted, catalogue, graph, WIDE_INTENT, budget=3)

    # contact_point is outside the policy now, so those passes are rejected --
    # and the budget went to three plans that could actually run.
    assert len(graph.executed) == 3
    assert "email" not in graph.executed


# --- failure ------------------------------------------------------------------


async def test_a_failing_read_fails_the_turn_the_way_it_always_did(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    graph = _Graph(ROWS, fail={"city"})

    with pytest.raises(OrderAgentFailure) as error:
        await _run(production_schema, catalogue, graph, WIDE_INTENT)

    assert error.value.code == "ORDER_AGENT_SEARCH_EXECUTION_FAILED"
    assert error.value.retryable is True


async def test_the_failure_reported_is_the_first_in_plan_order(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """Two reads fail at once; the turn must not report whichever lost the race.

    Serial raised on the first failure in plan order. Reporting a different one
    would make the same broken search produce different diagnostics run to run,
    which is how an intermittent-looking bug gets closed as unreproducible.
    """
    program = build_search_program(OrderSearchIntent.model_validate(WIDE_INTENT), catalogue)
    ordered_fields = [
        item.plan.filters[0].field_id for item in program.primary if item.plan.filters
    ]
    first, second = ordered_fields[0], ordered_fields[1]
    graph = _Graph(ROWS, fail={first, second}, delays={first: 0.05})

    with pytest.raises(OrderAgentFailure) as error:
        await _run(production_schema, catalogue, graph, WIDE_INTENT)

    # The slower of the two failures is the one in plan order, and it is the one
    # whose exception is chained -- not the one that failed first in wall time.
    assert error.value.code == "ORDER_AGENT_SEARCH_EXECUTION_FAILED"
    assert str(error.value.__cause__) == f"graph read failed for {first}"


async def test_a_sibling_failure_does_not_lose_the_reads_that_succeeded(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """The deferred path is best-effort, so one bad read must not void the rest.

    `return_exceptions=True` is what makes this true: without it the first
    exception cancels its siblings mid-flight, and a fallback that found the
    customer would be discarded because an unrelated read failed beside it.
    """
    graph = _Graph(
        {"customer_name": [{"customer_id": "C2", "customer_name": "Melgon Heating"}]},
        fail={"email"},
    )
    intent = {"customerNames": ["Melgon"], "emails": ["dana@example.com"]}

    with pytest.raises(OrderAgentFailure):
        await _run(production_schema, catalogue, graph, intent)

    # Both were issued -- the failing one did not cancel its sibling.
    assert "customer_name" in graph.executed
    assert "email" in graph.executed


# --- the invariants this must not break --------------------------------------


async def test_the_deferred_search_still_waits_for_everything_else_to_fail(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """SRCH-01's ordering, unchanged.

    The misspelling search is imprecise beside an exact one. Running it
    concurrently *with* the primary passes would be faster and would dilute
    them, so it stays behind the zero-result gate -- concurrency applies within
    a phase, never across the gate that separates them.
    """
    graph = _Graph(ROWS)

    await _run(production_schema, catalogue, graph, WIDE_INTENT)

    assert "customer_name" in graph.executed
    # The full-text pass is keyed by its own field and only runs on zero results.
    assert graph.executed.count("customer_name") == 1


async def test_a_search_that_finds_nothing_still_reaches_the_deferred_pass(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    graph = _Graph({})
    intent = {"customerNames": ["Jhon Smi"]}

    await _run(production_schema, catalogue, graph, intent)

    # Two reads on customer_name: the CONTAINS pass, then the deferred index.
    assert graph.executed.count("customer_name") == 2


async def test_concurrency_adds_no_graph_interactions_beyond_the_admitted_plans(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    """The lease is held once per turn, around the whole graph invocation.

    Reads take it in `coordinator.process_turn`, not per query, so running the
    admitted plans at once acquires no additional lease and cannot conflict with
    a drain. What has to stay true here is the thing that would break that: the
    node's only graph interaction is one `execute` per admitted plan, and
    concurrency did not introduce a second kind of access.
    """
    graph = _Graph(ROWS)
    program = build_search_program(OrderSearchIntent.model_validate(WIDE_INTENT), catalogue)

    result, _ = await _run(production_schema, catalogue, graph, WIDE_INTENT)

    assert len(graph.executed) == len(program.primary)
    assert result["queries_used"] == len(program.primary)
