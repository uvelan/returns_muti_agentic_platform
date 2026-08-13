"""The Neo4j inspection adapter against a real Neo4j.

Neo4j is the awkward one of the four, and the awkwardness is why these run
against the real server rather than a fake. Its answers come from procedures and
`SHOW` commands whose result shapes are version-specific
(`db.schema.nodeTypeProperties`, `db.schema.visualization`, `SHOW INDEXES`,
`SHOW CONSTRAINTS`), and it is the only backend where uniqueness lives on a
constraint rather than on the index row -- so an adapter can be plausibly written
and completely wrong without a real server to say so.

Test data uses labels suffixed with a run-unique token and is deleted afterwards,
because this is the platform's own graph rather than a throwaway one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from neo4j import AsyncDriver, AsyncGraphDatabase

from return_platform.bootstrap.adapters.source_inspection_neo4j import (
    build_neo4j_source_inspection_adapter,
)
from return_platform.configuration.settings import Settings
from return_platform.graph_schema_analyzer.ports.source_port import (
    ObjectKind,
    RelationshipKind,
    SourceInspectionPort,
)

SOURCE_ID = "platform_graph"


class _Labels:
    def __init__(self, token: str) -> None:
        self.warehouse = f"InspWarehouse{token}"
        self.bay = f"InspBay{token}"
        self.constraint = f"insp_unique_{token}"
        self.index = f"insp_lookup_{token}"


@pytest_asyncio.fixture
async def graph(test_settings: Settings) -> AsyncIterator[tuple[AsyncDriver, _Labels]]:
    driver = AsyncGraphDatabase.driver(
        test_settings.neo4j_uri,
        auth=(test_settings.neo4j_user, test_settings.neo4j_password.get_secret_value()),
    )
    labels = _Labels(uuid.uuid4().hex[:8])
    async with driver.session() as session:
        await session.run(
            f"CREATE CONSTRAINT {labels.constraint} IF NOT EXISTS "
            f"FOR (n:{labels.warehouse}) REQUIRE n.warehouse_code IS UNIQUE"
        )
        await session.run(
            f"CREATE INDEX {labels.index} IF NOT EXISTS FOR (n:{labels.bay}) ON (n.aisle)"
        )
        await session.run(
            f"CREATE (w:{labels.warehouse} {{warehouse_code: 'W-1', region: 'EAST'}}) "
            f"CREATE (b:{labels.bay} {{bay_id: 'B-1', aisle: 'A1'}}) "
            "CREATE (b)-[:LOCATED_IN]->(w)"
        )
        await session.run(
            f"CREATE (w:{labels.warehouse} {{warehouse_code: 'W-2'}}) "
            f"CREATE (b:{labels.bay} {{bay_id: 'B-2', aisle: 'A2'}}) "
            "CREATE (b)-[:LOCATED_IN]->(w)"
        )
    try:
        yield (driver, labels)
    finally:
        async with driver.session() as session:
            await session.run(f"MATCH (n:{labels.warehouse}) DETACH DELETE n")
            await session.run(f"MATCH (n:{labels.bay}) DETACH DELETE n")
            await session.run(f"DROP CONSTRAINT {labels.constraint} IF EXISTS")
            await session.run(f"DROP INDEX {labels.index} IF EXISTS")
        await driver.close()


def _inspection(driver: AsyncDriver) -> SourceInspectionPort:
    return build_neo4j_source_inspection_adapter(driver, source_id=SOURCE_ID)


@pytest.mark.asyncio
async def test_validate_reports_the_server_it_actually_reached(
    graph: tuple[AsyncDriver, _Labels],
) -> None:
    """Catches an adapter reporting success without opening a session."""
    result = await _inspection(graph[0]).validate(source_id=SOURCE_ID)
    assert result.reachable is True
    assert result.server_version is not None
    assert "Neo4j" in result.server_version


@pytest.mark.asyncio
async def test_an_unreachable_source_is_reported_rather_than_raised(
    test_settings: Settings,
) -> None:
    """`validate` is the operator's connection test; half its answers are "no",
    and a stack trace is not an answer they can act on."""
    driver = AsyncGraphDatabase.driver(
        "bolt://127.0.0.1:1",
        auth=(test_settings.neo4j_user, test_settings.neo4j_password.get_secret_value()),
    )
    try:
        result = await _inspection(driver).validate(source_id=SOURCE_ID)
    finally:
        await driver.close()
    assert result.reachable is False
    assert result.detail


@pytest.mark.asyncio
async def test_node_labels_are_the_objects_a_neo4j_source_offers(
    graph: tuple[AsyncDriver, _Labels],
) -> None:
    """The mapping that lets all four connectors answer the same eight questions.
    Without it Neo4j would need its own vocabulary and the interface would not be
    one interface."""
    driver, labels = graph
    objects = await _inspection(driver).list_objects(source_id=SOURCE_ID)
    by_name = {item.object_name: item for item in objects}
    assert labels.warehouse in by_name
    assert by_name[labels.warehouse].object_kind is ObjectKind.NODE_LABEL


@pytest.mark.asyncio
async def test_describe_object_reports_properties_and_their_optionality(
    graph: tuple[AsyncDriver, _Labels],
) -> None:
    """`region` is on one warehouse node of two, so it must be reported and must
    be reported optional -- a bounded sample could miss it entirely, and a schema
    proposal that omits a real property is one the analyzer cannot later map."""
    driver, labels = graph
    description = await _inspection(driver).describe_object(
        source_id=SOURCE_ID, object_name=labels.warehouse
    )
    by_name = {field.field_name: field for field in description.fields}
    assert "warehouse_code" in by_name
    assert by_name["region"].nullable is True
    assert description.approximate_row_count == 2


@pytest.mark.asyncio
async def test_a_uniqueness_constraint_is_reported_as_a_unique_index(
    graph: tuple[AsyncDriver, _Labels],
) -> None:
    """Neo4j is the only backend here that splits these: the index backing a
    uniqueness constraint does not say it is unique on its own row. Reporting it
    as a plain lookup would tell the analyzer a key it can rely on is merely an
    access path, and it would not propose it as an identity."""
    driver, labels = graph
    indexes = await _inspection(driver).list_indexes(
        source_id=SOURCE_ID, object_name=labels.warehouse
    )
    unique = [index for index in indexes if index.unique]
    assert unique, f"expected a unique index for {labels.warehouse}, got {indexes}"
    assert unique[0].fields == ("warehouse_code",)


@pytest.mark.asyncio
async def test_a_plain_index_is_not_reported_as_unique(
    graph: tuple[AsyncDriver, _Labels],
) -> None:
    """The other half of the same distinction: reporting every index as unique
    would nominate `aisle` as an identity for bays, which it is not."""
    driver, labels = graph
    indexes = await _inspection(driver).list_indexes(source_id=SOURCE_ID, object_name=labels.bay)
    by_name = {index.index_name: index for index in indexes}
    assert by_name[labels.index].unique is False
    assert by_name[labels.index].fields == ("aisle",)


@pytest.mark.asyncio
async def test_relationships_are_read_from_the_declared_schema(
    graph: tuple[AsyncDriver, _Labels],
) -> None:
    """Neo4j is the one backend that declares its relationships outright. A
    bounded `MATCH ()-[r]->() LIMIT n` would be cheaper to write and would report
    only the types appearing in the first n rows -- an incomplete answer
    indistinguishable from a complete one."""
    driver, labels = graph
    relationships = await _inspection(driver).list_relationships(
        source_id=SOURCE_ID, object_name=labels.bay
    )
    matching = [
        item
        for item in relationships
        if item.from_object == labels.bay and item.to_object == labels.warehouse
    ]
    assert matching, f"expected {labels.bay}->{labels.warehouse}, got {relationships}"
    assert matching[0].relationship_kind is RelationshipKind.GRAPH_RELATIONSHIP
    assert matching[0].constraint_name == "LOCATED_IN"


@pytest.mark.asyncio
async def test_profile_reports_statistics_over_node_properties(
    graph: tuple[AsyncDriver, _Labels],
) -> None:
    """The same selectivity signal the other three produce, so W4.8 can rank
    across a mixed-source schema rather than across four incomparable numbers."""
    driver, labels = graph
    profile = await _inspection(driver).profile(
        source_id=SOURCE_ID, object_name=labels.warehouse, sample_size=10
    )
    by_name = {field.field_name: field for field in profile.fields}
    assert profile.sampled_rows == 2
    assert by_name["warehouse_code"].identifier_candidate is True
    assert by_name["region"].null_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_a_label_that_is_not_an_identifier_is_refused(
    graph: tuple[AsyncDriver, _Labels],
) -> None:
    """A node label cannot be a bound parameter in a `MATCH` pattern, so it is
    validated immediately before interpolation. This is the last gate."""
    with pytest.raises(ValueError, match="unsafe Neo4j node label"):
        await _inspection(graph[0]).describe_object(
            source_id=SOURCE_ID, object_name="Warehouse) DETACH DELETE (n"
        )
