"""The misspelled customer, at production scale, against a real full-text index.

This is the test the previous implementation could not have passed and the
previous test suite could not have failed. The fallback used to read an
unfiltered, unordered `MATCH (c:Customer) ... LIMIT 100` and compare strings
with `difflib`; the unit tests around it fed it hand-written lists of three to
five rows, where every row is inside the window and the window is therefore
invisible. Whether the right customer is *reachable* is a property of the
corpus, the index and the ranking -- none of which a fake can hold -- so it is
proven here or not at all.

The scenario is the audited one. A quarter of a million customers, the
associate types `Jhon Smi`, and `John Smith` sits at row 84,000 in storage
order: outside any window a client could plausibly fetch, and far enough out
that the old path's ~100/N recovery odds were about one in twenty-five hundred.

`test_the_window_the_old_probe_read_does_not_contain_the_customer` runs the
predecessor's own query against the same corpus and asserts it misses. Without
it the passing test above could be read as "both approaches work at this
scale", which is exactly the reading that let the defect ship.

The corpus is the audit's own 250,000 rows and seeds in about half a minute;
`ORDER_DISCOVERY_FULLTEXT_CORPUS_SIZE` shrinks it for a developer iterating on
this file, holding every named row at the same relative depth. Every seeded
node carries this run's `graph_generation_id` and is deleted in the fixture's
teardown; nothing here mutates data it did not create.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from langgraph.runtime import Runtime
from neo4j import AsyncDriver, AsyncGraphDatabase

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
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
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    GraphDependencies,
    TurnRuntimeContext,
    make_order_search_node,
)
from return_platform.dynamic_knowledge.order_agent.identification import (
    build_identification_catalogue,
)
from return_platform.dynamic_knowledge.order_agent.search_strategy import CustomerFulltextPolicy
from return_platform.dynamic_knowledge.schema import ActiveSchema

pytestmark = pytest.mark.asyncio(loop_scope="module")

#: The audit's scenario. Overridable because a developer iterating on this file
#: does not need a quarter million rows to see it fail -- but the committed
#: default is the number the finding was written against, and a smaller run
#: proves proportionally less.
AUDITED_CORPUS_SIZE = 250_000
CORPUS_SIZE = int(os.getenv("ORDER_DISCOVERY_FULLTEXT_CORPUS_SIZE", str(AUDITED_CORPUS_SIZE)))


def _seeded_position(audited: int) -> int:
    """Where a named row goes, held in proportion when the corpus is shrunk.

    The audit's positions are absolute. A developer running this file with a
    smaller corpus still needs the row to exist and to sit at the same relative
    depth -- pinning the absolute number would put it past the end and turn a
    genuine failure into a missing fixture.
    """
    if audited < CORPUS_SIZE:
        return audited
    return max(1, audited * CORPUS_SIZE // AUDITED_CORPUS_SIZE - 1)


#: Where the customer the associate is looking for ends up. Deep enough that no
#: client-side window reaches it, and specific because the audit named it.
TARGET_POSITION = _seeded_position(84_000)

TARGET_NAME = "John Smith"
TARGET_TYPED = "Jhon Smi"

#: A second real-world shape: a misspelt trade name whose untyped suffix used to
#: dilute a whole-string similarity below its own threshold. It was the case the
#: removed `difflib` scorer was specifically tuned for, so parity is not assumed.
#:
#: Not the audit's literal "MELGON HEATING & COOLING", which is a real row in
#: the seeded development graph: two near-identical names competing for the same
#: query would make a pass or a failure here say something about the fixture
#: rather than about the search. Same shape, one edit per typed word plus a
#: suffix nobody says out loud.
TRADE_NAME = "ARLINGTON HEATING AND COOLING"
TRADE_NAME_TYPED = "arlingtn heatng"
TRADE_NAME_POSITION = _seeded_position(201_500)

#: The bound the deleted `build_customer_fuzzy_probe_plan` carried.
OLD_PROBE_LIMIT = 100

_BATCH_SIZE = 10_000


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _neo4j_uri() -> str:
    # `PLATFORM_TEST_NEO4J_URI` first -- the same variable
    # `tests/conftest.py::test_settings` reads -- because the published port is
    # not always 7687. Windows dynamically reserves 7454-7553 and 7679-7778,
    # which contain Neo4j's 7474 and 7687, so Docker cannot publish them
    # ("socket forbidden by its access permissions") and the stack maps Bolt to
    # 17687 instead. A helper that let only the *host* move could not be pointed
    # at it at all, and every test in this module failed to connect.
    uri = os.getenv("PLATFORM_TEST_NEO4J_URI")
    if uri and uri.strip():
        return uri.strip()
    host = os.getenv("PLATFORM_TEST_NEO4J_HOST", "localhost")
    return f"bolt://{host}:7687"


def _neo4j_auth() -> tuple[str, str]:
    # Same variable the other Neo4j real-infra modules use; a guessed default
    # trips Neo4j's authentication rate limiter and fails every Neo4j test in
    # the run, not just this one.
    return ("neo4j", _required_env("GRAPH_PASSWORD"))


def _customer_name(position: int) -> str:
    """A distractor name that cannot be confused with the target.

    Deliberately not drawn from a name list: a generated "Jon Smyth" 300 rows in
    would make a passing test ambiguous about whether the index found the right
    row or a near-enough one.
    """
    return f"Northgate Supply Depot {position:07d}"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def corpus() -> AsyncIterator[tuple[AsyncDriver, str, str]]:
    """A quarter-million customers with the target buried in the middle.

    Yields `(driver, account_id, graph_generation_id)`. The account id
    namespaces this run's rows so the assertions can identify them among
    whatever else the shared database holds, and the generation id is what the
    teardown deletes by.
    """
    driver = AsyncGraphDatabase.driver(_neo4j_uri(), auth=_neo4j_auth())
    run_token = uuid.uuid4().hex[:10]
    account_id = f"FTXT{run_token.upper()}"
    generation_id = f"fulltext-test-{run_token}"
    try:
        async with driver.session() as session:
            for start in range(0, CORPUS_SIZE, _BATCH_SIZE):
                rows = []
                for position in range(start, min(start + _BATCH_SIZE, CORPUS_SIZE)):
                    if position == TARGET_POSITION:
                        name = TARGET_NAME
                    elif position == TRADE_NAME_POSITION:
                        name = TRADE_NAME
                    else:
                        name = _customer_name(position)
                    rows.append({"customer_id": f"{run_token}-{position:07d}", "name": name})
                written = await session.run(
                    "UNWIND $rows AS row "
                    "CREATE (c:Customer {account_id: $account_id, "
                    "customer_id: row.customer_id, customer_name: row.name, "
                    "graph_generation_id: $generation_id})",
                    rows=rows,
                    account_id=account_id,
                    generation_id=generation_id,
                )
                # Consumed rather than left streaming: an unconsumed result
                # holds its connection open, and fifty of them in a row is how
                # a seed loop ends in "failed to read from defunct connection"
                # that reads like an infrastructure fault.
                await written.consume()
        yield driver, account_id, generation_id
    finally:
        async with driver.session() as session:
            removed = await session.run(
                "MATCH (c:Customer {graph_generation_id: $generation_id}) "
                "CALL (c) { DETACH DELETE c } IN TRANSACTIONS OF 10000 ROWS",
                generation_id=generation_id,
            )
            await removed.consume()
        await driver.close()


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    root = Path(__file__).parents[2]
    return load_active_schema(root / "config/dynamic_knowledge/active-schema.return-order.yaml")


def _guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies["order-discovery-agent"],
        principal=PrincipalContext(
            principal_id="assoc-1", tenant_id="tenant-1", roles=frozenset({"associate"})
        ),
    )


class _CollectingEvidenceStore:
    def __init__(self) -> None:
        self.by_id: dict[str, QueryEvidence] = {}

    async def put(self, *, run_id: str, evidence: QueryEvidence) -> None:
        del run_id
        self.by_id[evidence.query_execution_id] = evidence

    async def get_many(self, query_execution_ids: Any) -> tuple[QueryEvidence, ...]:
        return tuple(self.by_id[identifier] for identifier in query_execution_ids)


class _UnusedModel:
    """The search node must not need a model. If it reaches for one, say so."""

    async def decide(self, context: Any) -> Any:
        raise AssertionError("order_search must not invoke the reasoning model")

    async def correct_action(self, **kwargs: Any) -> Any:
        raise AssertionError("order_search must not invoke the reasoning model")

    async def correct_response(self, **kwargs: Any) -> Any:
        raise AssertionError("order_search must not invoke the reasoning model")


def _discovery() -> Any:
    root = Path(__file__).parents[2]
    return load_return_configuration(
        root / "config/returns/production.yaml"
    ).configuration.discovery


def _dependencies(schema: ActiveSchema, driver: AsyncDriver) -> GraphDependencies:
    return GraphDependencies(
        schema=schema,
        model_gateway=_UnusedModel(),
        knowledge_gateway=Neo4jKnowledgeGateway(
            driver, database=os.getenv("PLATFORM_NEO4J_DATABASE", "neo4j")
        ),
        evidence_store=_CollectingEvidenceStore(),
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        response_safety_guard=ResponseSafetyGuard(),
        on_demand_sync=None,
        compiler=CypherCompiler(),
        customer_fulltext=CustomerFulltextPolicy(),
        # The shipped catalogue. Which signals exist and which searches answer
        # them is configuration now, so a test that hand-built one would prove
        # the index works for a catalogue nobody ships.
        identification=build_identification_catalogue(
            _discovery().identification_fields,
            schema,
            default_fulltext_index=_discovery().progressive.customer_fulltext_index,
        ),
    )


def _search_state(typed_name: str, graph_generation_id: str) -> dict[str, Any]:
    action = AgentAction(
        business_capability="order-discovery",
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="Search for the customer the associate named.",
        search_intent=OrderSearchIntent.model_validate({"customerNames": [typed_name]}),
    )
    return {
        "conversation_id": f"c-{uuid.uuid4().hex[:8]}",
        "client_turn_id": "ct1",
        "run_id": str(uuid.uuid4()),
        "agent_id": "order-discovery-agent",
        # The corpus fixture namespaces its generation per run, and every read
        # this node compiles is pinned to the generation in the state. A
        # constant here searched a generation that holds no rows: the two
        # assertions that require a hit failed, and the two that require an
        # absence passed without the corpus being consulted at all.
        "graph_generation_id": graph_generation_id,
        "as_of": datetime(2026, 8, 13, 9, 30, tzinfo=UTC).isoformat(),
        "session_timezone": "UTC",
        "evidence_refs": (),
        "order_search_cache": None,
        "action": action.model_dump(mode="json"),
        "queries_used": 0,
    }


async def _run_order_search(
    schema: ActiveSchema, driver: AsyncDriver, typed_name: str, graph_generation_id: str
) -> tuple[dict[str, Any], _CollectingEvidenceStore]:
    """Drive the production `order_search` node, not a helper beside it.

    Entering through the node is the point: the defect was reachable only
    because the live node called the bounded probe, and a test that called the
    replacement directly would prove the replacement works without proving
    anything about what production runs.
    """
    deps = _dependencies(schema, driver)
    node = make_order_search_node(deps)
    runtime = Runtime(context=TurnRuntimeContext(guard_context=_guard_context(schema)))
    result = await node(_search_state(typed_name, graph_generation_id), runtime)
    assert isinstance(deps.evidence_store, _CollectingEvidenceStore)
    return result, deps.evidence_store


def _candidates(result: dict[str, Any], store: _CollectingEvidenceStore) -> list[dict[str, Any]]:
    cache = result["order_search_cache"]
    evidence = store.by_id[cache["evidenceRef"]]
    return list(evidence.result["candidates"])


async def test_the_misspelled_customer_is_found_far_outside_any_window(
    corpus: tuple[AsyncDriver, str, str], production_schema: ActiveSchema
) -> None:
    """The P0 scenario end to end, through the live search node.

    `Jhon Smi` matches no customer exactly and no customer by CONTAINS, so the
    turn falls through to the misspelling path. The correct row is 84,000 deep;
    it comes back because the index ranked the whole set, and it ranks first
    because nothing else in a quarter million rows is close.
    """
    driver, _, generation = corpus

    result, store = await _run_order_search(production_schema, driver, TARGET_TYPED, generation)
    candidates = _candidates(result, store)

    assert candidates, "the misspelled customer was not recovered at all"
    assert candidates[0]["data"]["customer_name"] == TARGET_NAME
    assert candidates[0]["matches"] == ["customer_name_fuzzy"]
    # Not promoted to a confirmed fact: a half-remembered name is a candidate to
    # show, never something to act on.
    assert candidates[0]["score"] <= 0.6


async def test_the_window_the_old_probe_read_does_not_contain_the_customer(
    corpus: tuple[AsyncDriver, str, str], production_schema: ActiveSchema
) -> None:
    """The inversion of the test that pinned this defect as intended behaviour.

    This is the query `build_customer_fuzzy_probe_plan()` compiled to: no
    filter, no ordering, a hundred rows. Against this corpus it cannot contain
    the target, and `difflib` cannot score a row it was never given -- so the
    associate was told the order does not exist.
    """
    driver, account_id, _ = corpus

    async with driver.session() as session:
        cursor = await session.run(
            "MATCH (c:Customer) RETURN c.customer_name AS customer_name LIMIT $limit",
            limit=OLD_PROBE_LIMIT,
        )
        window = [record["customer_name"] async for record in cursor]
        counted = await session.run(
            "MATCH (c:Customer {account_id: $account_id}) RETURN count(c) AS total",
            account_id=account_id,
        )
        total = (await counted.single())["total"]

    assert total == CORPUS_SIZE
    assert len(window) == OLD_PROBE_LIMIT
    assert TARGET_NAME not in window


async def test_the_trade_name_the_removed_scorer_handled_still_resolves(
    corpus: tuple[AsyncDriver, str, str], production_schema: ActiveSchema
) -> None:
    """Parity with the client-side scorer that was deleted.

    "MELGON HEATING AND COOLING" typed as "melgan heatng" is the case the old
    similarity function had to grow a sliding window for, because the untyped
    suffix diluted a whole-string ratio below its own threshold. Per-token
    matching makes the suffix cost nothing -- but only a real index can show
    that, so it is asserted here rather than assumed.
    """
    driver, _, generation = corpus

    result, store = await _run_order_search(production_schema, driver, TRADE_NAME_TYPED, generation)
    names = [candidate["data"]["customer_name"] for candidate in _candidates(result, store)]

    assert TRADE_NAME in names


async def test_an_unrelated_name_returns_nothing_rather_than_the_nearest_row(
    corpus: tuple[AsyncDriver, str, str], production_schema: ActiveSchema
) -> None:
    """Recall bought with precision would be the same defect wearing a hat.

    A search that always returns its best row hands the associate a wrong
    customer to confirm, which is worse than "not found" -- confirmation is the
    step that binds a case to an order.
    """
    driver, _, generation = corpus

    result, store = await _run_order_search(
        production_schema, driver, "Zephyrine Okonkwo", generation
    )

    assert _candidates(result, store) == []


async def test_the_configured_index_is_the_one_that_is_queried(
    corpus: tuple[AsyncDriver, str, str],
) -> None:
    """The index the search depends on exists, is online, and covers this field.

    Named rather than assumed: the whole remedy rests on migration 0013 having
    run, and an index that is missing or still POPULATING degrades every
    misspelled name to "no such order" with no other symptom.
    """
    driver, _, _ = corpus
    configured = CustomerFulltextPolicy().index_name

    async with driver.session() as session:
        cursor = await session.run(
            "SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties "
            "WHERE name = $name RETURN type, state, labelsOrTypes, properties",
            name=configured,
        )
        record = await cursor.single()

    assert record is not None, f"full-text index {configured!r} does not exist"
    assert record["type"] == "FULLTEXT"
    assert record["state"] == "ONLINE"
    assert record["labelsOrTypes"] == ["Customer"]
    assert record["properties"] == ["customer_name"]
