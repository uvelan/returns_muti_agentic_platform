"""What `Neo4jKnowledgeGateway.execute` will and will not send to the database.

The gateway is the last check before compiled Cypher reaches Neo4j, and it was
untested. That mattered little while the rule was "starts with MATCH and
contains no write keyword", because the rule was one line and obviously closed.
Admitting `db.index.fulltext.queryNodes` (SRCH-01) opens it by exactly one
procedure, and an opening that nothing tests is an opening nobody notices
widening.

No database is involved: the refusal happens before a session is opened, and
the admissions are proven by observing what the session was asked to run.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import (
    GENERATION_PARAMETER,
    CypherCompiler,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryOperation,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


class _EmptyResult:
    async def __aiter__(self) -> Any:
        return
        yield  # pragma: no cover - makes this an async generator


class _RecordingSession:
    def __init__(self, executed: list[tuple[str, dict[str, Any]]]) -> None:
        self._executed = executed

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def run(self, cypher: str, parameters: dict[str, Any]) -> _EmptyResult:
        self._executed.append((cypher, parameters))
        return _EmptyResult()


class _RecordingDriver:
    """Stands in for AsyncDriver, and fails loudly if a session is opened for a
    query the boundary should have refused."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def session(self, **kwargs: Any) -> _RecordingSession:
        del kwargs
        return _RecordingSession(self.executed)


async def _execute(driver: _RecordingDriver, schema: ActiveSchema, cypher: str) -> None:
    gateway = Neo4jKnowledgeGateway(driver, database="neo4j")  # type: ignore[arg-type]
    await gateway.execute(
        schema=schema,
        graph_generation_id="generation-1",
        plan=None,
        compiled_cypher=cypher,
        parameters={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cypher",
    (
        "MATCH (n:ConfiguredAlpha) SET n.name = 'x' RETURN n",
        "MATCH (n:ConfiguredAlpha) DETACH DELETE n",
        "CREATE (n:ConfiguredAlpha) RETURN n",
        "CALL dbms.components() YIELD name RETURN name",
        "CALL apoc.meta.schema() YIELD value RETURN value",
        # The allowlisted procedure name is a prefix match, so a name that
        # merely starts the same way must not slip past it.
        "CALL db.index.fulltext.queryRelationships('i', 'q') YIELD relationship RETURN relationship",
    ),
)
async def test_a_query_outside_the_two_permitted_shapes_never_reaches_a_session(
    active_schema: ActiveSchema, cypher: str
) -> None:
    driver = _RecordingDriver()

    with pytest.raises(ValueError):
        await _execute(driver, active_schema, cypher)

    assert driver.executed == []


@pytest.mark.asyncio
async def test_the_compiled_full_text_query_is_admitted(active_schema: ActiveSchema) -> None:
    """The one procedure call the platform needs, exactly as the compiler emits it.

    Asserting on the compiler's own output rather than a hand-written string:
    a boundary that admits a query nobody compiles, or refuses the one everybody
    compiles, is the failure this test exists to catch.
    """
    plan = LogicalQueryPlan(
        operation=QueryOperation.FULLTEXT_SEARCH,
        start_entity_id="entity_a",
        fields=("id", "name"),
        fulltext_index="configured_name_search",
        fulltext_field_id="name",
        fulltext_query="(Jhon* OR Jhon~1) AND Smi*",
        limit=25,
    )
    compiled = CypherCompiler().compile_read(active_schema, plan)
    driver = _RecordingDriver()

    await _execute(driver, active_schema, compiled.cypher)

    assert [cypher for cypher, _ in driver.executed] == [compiled.cypher]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tail",
    (
        "CALL apoc.meta.schema() YIELD value RETURN value",
        "MATCH (n:ConfiguredAlpha) DETACH DELETE n",
        "MERGE (n:ConfiguredAlpha {id: 'x'}) RETURN n",
    ),
)
async def test_the_full_text_exception_buys_one_call_and_nothing_else(
    active_schema: ActiveSchema, tail: str
) -> None:
    """Everything after the permitted procedure is held to the original rule.

    Otherwise the allowlist would be a prefix anyone could write past, and the
    ban on `CALL` -- which is what keeps arbitrary procedures, file reads and
    writes out -- would be worth nothing.
    """
    driver = _RecordingDriver()
    smuggled = (
        "CALL db.index.fulltext.queryNodes($fulltext_index, $fulltext_query, {limit: $limit})\n"
        "YIELD node AS n0, score\n"
        f"{tail}"
    )

    with pytest.raises(ValueError):
        await _execute(driver, active_schema, smuggled)

    assert driver.executed == []


@pytest.mark.asyncio
async def test_the_generation_is_bound_rather_than_discarded(
    active_schema: ActiveSchema,
) -> None:
    """`execute` used to open with `del graph_generation_id`.

    Every layer above it did its job -- the snapshot was resolved, the lease was
    taken, the id was threaded down -- and then the one call that reached the
    database dropped it, so the read spanned every generation Neo4j held. The
    compiler now emits the predicate; this is the half that gives it a value.
    """
    plan = LogicalQueryPlan(
        operation=QueryOperation.SEARCH,
        start_entity_id="entity_a",
        fields=("id", "name"),
        limit=10,
    )
    compiled = CypherCompiler().compile_read(active_schema, plan)
    driver = _RecordingDriver()

    gateway = Neo4jKnowledgeGateway(driver, database="neo4j")  # type: ignore[arg-type]
    await gateway.execute(
        schema=active_schema,
        graph_generation_id="generation-77",
        plan=None,
        compiled_cypher=compiled.cypher,
        parameters=compiled.parameters,
    )

    ((_, bound),) = driver.executed
    assert bound[GENERATION_PARAMETER] == "generation-77"


@pytest.mark.asyncio
async def test_a_generation_smuggled_in_the_parameters_cannot_override_the_handle(
    active_schema: ActiveSchema,
) -> None:
    """A compiled query supplying its own generation would be a second opinion
    about which one serves this request, which is exactly what resolving through
    a handle exists to make impossible."""
    plan = LogicalQueryPlan(
        operation=QueryOperation.SEARCH,
        start_entity_id="entity_a",
        fields=("id", "name"),
        limit=10,
    )
    compiled = CypherCompiler().compile_read(active_schema, plan)
    driver = _RecordingDriver()

    gateway = Neo4jKnowledgeGateway(driver, database="neo4j")  # type: ignore[arg-type]
    await gateway.execute(
        schema=active_schema,
        graph_generation_id="generation-77",
        plan=None,
        parameters={**compiled.parameters, GENERATION_PARAMETER: "some-other-generation"},
        compiled_cypher=compiled.cypher,
    )

    ((_, bound),) = driver.executed
    assert bound[GENERATION_PARAMETER] == "generation-77"
