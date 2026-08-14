"""The validation Cypher, executed by a real Neo4j.

The unit tests around `evaluate()` prove the polarity logic. They cannot prove
the queries are *valid Cypher*, that the property names match what the writer
actually stores, or that a cross-generation edge is detected -- a typo in a
label or a wrong property name yields a syntactically fine query that counts
zero and reports every generation healthy. That is the failure mode this file
exists to catch, so it runs the compiled checks unmodified against a real
database.

Nodes are written directly rather than via a full sync: the subject under test
is the validator, and driving a whole rebuild to produce three nodes would make
this a slow integration test of everything else.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from return_platform.dynamic_knowledge.graph.validation import (
    ValidationCheckId,
    ValidationSeverity,
    compile_validation_checks,
)
from return_platform.dynamic_knowledge.lifecycle.neo4j_validator import Neo4jGenerationValidator
from return_platform.dynamic_knowledge.schema import ActiveSchema


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


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _neo4j_auth() -> tuple[str, str]:
    # Same variable tests/dynamic_knowledge/test_on_demand_sync_production_wiring.py
    # uses; guessing a default here silently locks the account out via Neo4j's
    # authentication rate limiter, which then fails every other Neo4j test in
    # the run.
    return ("neo4j", _required_env("GRAPH_PASSWORD"))


@pytest_asyncio.fixture
async def driver() -> AsyncIterator[object]:
    instance = AsyncGraphDatabase.driver(_neo4j_uri(), auth=_neo4j_auth())
    try:
        yield instance
    finally:
        await instance.close()


async def _run(driver: object, cypher: str, **parameters: object) -> None:
    async with driver.session() as session:  # type: ignore[attr-defined]
        await session.run(cypher, parameters)


async def _seed_healthy(driver: object, schema: ActiveSchema, generation_id: str) -> None:
    """One node per declared label, with every key property set, plus one edge
    per declared relationship whose endpoints are in this generation."""
    for node in schema.graph.nodes.values():
        entity = schema.entities[node.entity_id]
        properties = {
            entity.fields[field_id].graph_property: f"{node.label}-{field_id}-1"
            for field_id in node.key_fields
        }
        assignments = ", ".join(f"n.{name} = ${name}" for name in properties)
        await _run(
            driver,
            f"CREATE (n:{node.label} {{graph_generation_id: $generationId}}) "
            + (f"SET {assignments}" if assignments else ""),
            generationId=generation_id,
            **properties,
        )

    for relationship in schema.graph.relationships.values():
        source_label = schema.entity_node(relationship.source_entity_id).label
        target_label = schema.entity_node(relationship.target_entity_id).label
        await _run(
            driver,
            f"MATCH (s:{source_label} {{graph_generation_id: $generationId}}) "
            f"MATCH (t:{target_label} {{graph_generation_id: $generationId}}) "
            "WITH s, t LIMIT 1 "
            f"CREATE (s)-[:{relationship.relationship_type} "
            "{graph_generation_id: $generationId}]->(t)",
            generationId=generation_id,
        )


async def _drop(driver: object, generation_id: str) -> None:
    await _run(
        driver,
        "MATCH (n {graph_generation_id: $generationId}) DETACH DELETE n",
        generationId=generation_id,
    )


@pytest.mark.asyncio
async def test_every_compiled_check_is_valid_cypher(
    driver: object, active_schema: ActiveSchema
) -> None:
    """Executes every check against an empty generation. Not asserting the
    findings -- only that nothing raises, which is what catches a malformed
    query or a bad label interpolation."""
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    checks = compile_validation_checks(active_schema, graph_generation_id=generation_id)
    assert checks, "the schema should imply at least one check"

    async with driver.session() as session:  # type: ignore[attr-defined]
        for check in checks:
            result = await session.run(check.statement.cypher, check.statement.parameters)
            rows = [row async for row in result]
            assert rows, f"{check.check_id} returned no row for a count query"


@pytest.mark.asyncio
async def test_a_healthy_generation_reports_no_errors(
    driver: object, active_schema: ActiveSchema
) -> None:
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    try:
        await _seed_healthy(driver, active_schema, generation_id)
        report = await Neo4jGenerationValidator(driver).validate(  # type: ignore[arg-type]
            schema=active_schema, graph_generation_id=generation_id
        )
        assert report.passed, report.summary()
    finally:
        await _drop(driver, generation_id)


@pytest.mark.asyncio
async def test_an_empty_generation_is_rejected(driver: object, active_schema: ActiveSchema) -> None:
    """The headline case: a build that projected nothing must not activate."""
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    report = await Neo4jGenerationValidator(driver).validate(  # type: ignore[arg-type]
        schema=active_schema, graph_generation_id=generation_id
    )

    assert not report.passed
    assert {f.check_id for f in report.errors} == {ValidationCheckId.NODE_LABEL_POPULATED}


@pytest.mark.asyncio
async def test_an_edge_into_the_previous_generation_is_detected(
    driver: object, active_schema: ActiveSchema
) -> None:
    """The blue/green bleed. Invisible to every other check: the edge exists,
    both endpoints exist, the labels are right -- only the generation on one
    endpoint is wrong, and once the old generation retires it dangles."""
    relationship = next(iter(active_schema.graph.relationships.values()))
    source_label = active_schema.entity_node(relationship.source_entity_id).label
    target_label = active_schema.entity_node(relationship.target_entity_id).label

    generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    stale_generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    try:
        await _seed_healthy(driver, active_schema, generation_id)
        # A target node belonging to the *previous* generation, wired to a
        # source node in the new one by an edge stamped with the new one.
        await _run(
            driver,
            f"CREATE (t:{target_label} {{graph_generation_id: $staleId}})",
            staleId=stale_generation_id,
        )
        await _run(
            driver,
            f"MATCH (s:{source_label} {{graph_generation_id: $generationId}}) "
            f"MATCH (t:{target_label} {{graph_generation_id: $staleId}}) "
            "WITH s, t LIMIT 1 "
            f"CREATE (s)-[:{relationship.relationship_type} "
            "{graph_generation_id: $generationId}]->(t)",
            generationId=generation_id,
            staleId=stale_generation_id,
        )

        report = await Neo4jGenerationValidator(driver).validate(  # type: ignore[arg-type]
            schema=active_schema, graph_generation_id=generation_id
        )

        assert not report.passed
        assert ValidationCheckId.RELATIONSHIP_ENDPOINTS_SAME_GENERATION in {
            f.check_id for f in report.errors
        }
    finally:
        await _drop(driver, generation_id)
        await _drop(driver, stale_generation_id)


@pytest.mark.asyncio
async def test_a_node_missing_a_key_property_is_detected(
    driver: object, active_schema: ActiveSchema
) -> None:
    """Neo4j treats a null property as absent from a uniqueness constraint
    rather than as a violation, so the constraint does not catch this and the
    node is unfindable by the lookup the constraint exists to serve."""
    node = next(node for node in active_schema.graph.nodes.values() if node.key_fields)
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    try:
        await _seed_healthy(driver, active_schema, generation_id)
        # Same label and generation, no key properties at all.
        await _run(
            driver,
            f"CREATE (n:{node.label} {{graph_generation_id: $generationId}})",
            generationId=generation_id,
        )

        report = await Neo4jGenerationValidator(driver).validate(  # type: ignore[arg-type]
            schema=active_schema, graph_generation_id=generation_id
        )

        assert not report.passed
        assert ValidationCheckId.NODE_KEY_COMPLETE in {f.check_id for f in report.errors}
    finally:
        await _drop(driver, generation_id)


@pytest.mark.asyncio
async def test_a_missing_relationship_type_warns_but_does_not_block(
    driver: object, active_schema: ActiveSchema
) -> None:
    """Nodes but no edges: legitimate for a sparse source, so activation must
    still be allowed."""
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    try:
        for node in active_schema.graph.nodes.values():
            entity = active_schema.entities[node.entity_id]
            properties = {
                entity.fields[field_id].graph_property: f"{node.label}-{field_id}-1"
                for field_id in node.key_fields
            }
            assignments = ", ".join(f"n.{name} = ${name}" for name in properties)
            await _run(
                driver,
                f"CREATE (n:{node.label} {{graph_generation_id: $generationId}}) "
                + (f"SET {assignments}" if assignments else ""),
                generationId=generation_id,
                **properties,
            )

        report = await Neo4jGenerationValidator(driver).validate(  # type: ignore[arg-type]
            schema=active_schema, graph_generation_id=generation_id
        )

        assert report.passed, report.summary()
        assert report.warnings
        assert all(f.severity is ValidationSeverity.WARNING for f in report.warnings)
    finally:
        await _drop(driver, generation_id)
