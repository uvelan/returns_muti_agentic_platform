"""W2.4/W2.7 against the real SQL Server: the source contract, and the sync.

`tests/bootstrap/test_analyzer_produced_warehouse_entities.py` proves the
descriptor is what the analyzer derives from a written-out observation of
`platform.bay_configuration`. This is the other half: that the observation is
what the live source actually declares, and that the whole targeted path -- read,
extract, project -- works against it.

W2.6 was reported complete twice before it was, and closed only when a genuine
sample was checked **against every document rather than against document zero**.
A relational source lets that standard be met exactly rather than approximately:
the catalogue is the contract, so "every declared path is a column" is a total
statement, and the value checks below run over every row in the table rather than
the first.

Needs the compose SQL Server. Skipped, not failed, when it cannot be reached: a
suite that goes red on a machine with no database says nothing about the code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from return_platform.bootstrap.adapters.analyzer_source_observation import SourceObservation
from return_platform.bootstrap.adapters.source_inspection_sqlserver import (
    build_sqlserver_source_inspection_adapter,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.bay_observations import (
    BAY_ENTITY_ID,
    WAREHOUSE_ENTITY_ID,
    WAREHOUSE_FIELD_ID,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import ProjectionReadScope
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.sqlserver import (
    SqlServerConnectionSettings,
    SqlServerSourceScanConnector,
    run_read_query,
)
from tests.bootstrap.test_analyzer_produced_warehouse_entities import (
    BAY_COLUMNS,
    BAY_INDEXES,
)

pytestmark = pytest.mark.asyncio

SOURCE_ID = "source_bays"
OBJECT_NAME = "platform.bay_configuration"
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


@pytest.fixture
def connection(test_settings: Settings) -> SqlServerConnectionSettings:
    """Against the *platform* database, which is where the bay master lives.

    `test_settings` names `test_db`, which exists so the connector tests have a
    throwaway; the bay tables are created by the platform's own SQL migrations in
    `return_platform` and there is nothing to assert about a copy of them.
    """
    return SqlServerConnectionSettings(
        server=test_settings.sqlserver_host,
        port=test_settings.sqlserver_port,
        user=test_settings.sqlserver_user,
        password=test_settings.sqlserver_password.get_secret_value(),
        database="return_platform",
    )


@pytest.fixture
def rows(connection: SqlServerConnectionSettings) -> list[dict[str, Any]]:
    try:
        found = run_read_query(connection, f"SELECT * FROM {OBJECT_NAME}", {})
    except Exception as error:  # noqa: BLE001 - see the module docstring
        pytest.skip(f"SQL Server is not reachable for the bay contract check: {error}")
    if not found:
        pytest.skip("platform.bay_configuration is empty; there is no contract to check")
    return found


async def observe(connection: SqlServerConnectionSettings) -> SourceObservation:
    """A plain coroutine rather than a fixture.

    pytest-asyncio will not hand an async fixture to a test whose own loop scope
    it has not resolved, and the failure is an opaque `AssertionError` from the
    plugin rather than anything about this module.
    """
    adapter = build_sqlserver_source_inspection_adapter(connection, source_id=SOURCE_ID)
    validation = await adapter.validate(source_id=SOURCE_ID)
    if not validation.reachable:
        pytest.skip(f"SQL Server is not reachable: {validation.detail}")
    return SourceObservation(
        description=await adapter.describe_object(source_id=SOURCE_ID, object_name=OBJECT_NAME),
        indexes=tuple(await adapter.list_indexes(source_id=SOURCE_ID, object_name=OBJECT_NAME)),
    )


async def test_the_live_catalogue_is_the_one_the_descriptor_was_compiled_from(
    connection: SqlServerConnectionSettings,
) -> None:
    """The recorded observation is not taken on trust.

    If the table gains, loses or retypes a column, this fails and the descriptor
    is stale -- which is the only way anyone finds out before a sync starts
    projecting nulls.
    """
    observed = await observe(connection)

    assert (
        tuple(
            (field.field_name, field.declared_type, field.nullable)
            for field in observed.description.fields
        )
        == BAY_COLUMNS
    )


async def test_the_declared_indexes_still_say_what_the_capabilities_were_derived_from(
    connection: SqlServerConnectionSettings,
) -> None:
    """Index names are excluded on purpose, and so is the order they arrive in.

    SQL Server generates the primary key's name (`PK__bay_conf__5327...`) and an
    index rebuilt on another instance carries a different one; the adapter orders
    its results by that name, so the *sequence* is a property of a random suffix.
    Column order **within** an index is not excluded -- it is what makes a key
    prefix cheap, and it is where the leading-column capability rule comes from.
    """
    observed = await observe(connection)

    assert {(index.fields, index.unique, index.primary) for index in observed.indexes} == set(
        BAY_INDEXES
    )


async def test_every_declared_path_resolves_on_every_row_not_on_row_zero(
    descriptor: ActiveSchema, rows: list[dict[str, Any]]
) -> None:
    """The standard W2.6 closed on, applied to all of them.

    Three of nine `shipmentInfo` paths resolved on zero documents and it took a
    100-document sample to notice, because document zero happened to be the wrong
    one to look at. Here every row is checked and a NOT NULL column is required
    to be present on all of them: a path that resolves sometimes is a path that
    projects null the rest of the time, and a null in the graph is
    indistinguishable from a source that had no data.
    """
    nullable = {name for name, _, is_nullable in BAY_COLUMNS if is_nullable}
    missing: list[str] = []

    for entity_id in (WAREHOUSE_ENTITY_ID, BAY_ENTITY_ID):
        for field_id, field in descriptor.entities[entity_id].fields.items():
            assert field.physical_path is not None
            column = field.physical_path[0]
            for index, row in enumerate(rows):
                if column not in row:
                    missing.append(f"{entity_id}.{field_id} -> {column} absent on row {index}")
                elif row[column] is None and column not in nullable:
                    missing.append(f"{entity_id}.{field_id} -> {column} is NULL on row {index}")

    assert not missing, "\n".join(missing)
    assert len(rows) > 1, "one row cannot distinguish 'resolves' from 'resolves here'"


async def test_one_anchored_read_returns_that_warehouses_bays_and_nothing_else(
    descriptor: ActiveSchema, connection: SqlServerConnectionSettings, rows: list[dict[str, Any]]
) -> None:
    """The targeted read, compiled and executed against the real server.

    This is what caught the namespace defect: `compile_source_read` emitted
    `FROM "bay_configuration"` with no schema qualifier, and SQL Server answered
    `Invalid object name`. Nothing had noticed because every source in the
    descriptor before W2.4 was MongoDB, and the scheduled-scan path resolves the
    namespace separately.
    """
    warehouse_id = str(rows[0]["warehouse_id"])
    expected = {str(row["bay_id"]) for row in rows if str(row["warehouse_id"]) == warehouse_id}

    connector = SqlServerSourceScanConnector(connection, schema=descriptor)
    page = await connector.targeted_read(
        schema=descriptor,
        plan=build_targeted_read_plan(
            schema=descriptor,
            entity_id=WAREHOUSE_ENTITY_ID,
            normalized_anchors={WAREHOUSE_FIELD_ID: ("EXACT", warehouse_id)},
        ),
    )

    assert page.documents
    returned = {
        str(document.document["bay_id"]) for document in page.documents if document.document
    }
    assert returned == expected


async def test_the_read_projects_both_entities_and_the_edge_between_them(
    descriptor: ActiveSchema, connection: SqlServerConnectionSettings, rows: list[dict[str, Any]]
) -> None:
    """One warehouse node however many bay rows carried its id.

    `distinct` on the entity would not do this -- it de-duplicates within one
    document, and a SQL row is one document -- so it is the node key that
    collapses them, which is why the descriptor does not declare `distinct` and
    claim something that does not happen.
    """
    warehouse_id = str(rows[0]["warehouse_id"])
    bays = [row for row in rows if str(row["warehouse_id"]) == warehouse_id]

    connector = SqlServerSourceScanConnector(connection, schema=descriptor)
    page = await connector.targeted_read(
        schema=descriptor,
        plan=build_targeted_read_plan(
            schema=descriptor,
            entity_id=WAREHOUSE_ENTITY_ID,
            normalized_anchors={WAREHOUSE_FIELD_ID: ("EXACT", warehouse_id)},
        ),
    )
    mutations = GenericSourceRecordExtractor().extract(
        schema=descriptor,
        source_asset_id=SOURCE_ID,
        page=page,
        read_scope=ProjectionReadScope.PARTIAL_TARGETED_READ,
    )
    batch = await GenericGraphProjector().project(schema=descriptor, mutations=mutations)

    warehouse_keys = {
        tuple(sorted(mutation.key_values.items()))
        for mutation in batch.node_mutations
        if mutation.projection_id == WAREHOUSE_ENTITY_ID
    }
    bay_keys = {
        tuple(sorted(mutation.key_values.items()))
        for mutation in batch.node_mutations
        if mutation.projection_id == BAY_ENTITY_ID
    }

    assert len(warehouse_keys) == 1
    assert len(bay_keys) == len(bays)
    assert len(batch.relationship_mutations) == len(bays)
    assert {mutation.relationship_id for mutation in batch.relationship_mutations} == {
        "warehouse_HAS_BAY_bay"
    }
