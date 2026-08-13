"""The PostgreSQL inspection adapter against a real PostgreSQL.

Skipped unless `PLATFORM_TEST_POSTGRES_HOST` is set, and that is a deliberate
difference from the other three modules rather than an oversight. This platform
has no PostgreSQL source and `compose.yaml` runs none for the application --
`temporal-postgresql` is Temporal's private store and publishes no host port.
§5A requires the connector regardless, because the sources a deployment binds are
configuration; so the connector exists, and the test names the server it wants
instead of inventing one nobody runs.

Run it against a throwaway server:

    docker run -d --rm --name pg-probe -e POSTGRES_PASSWORD=probe_pw \\
        -e POSTGRES_USER=probe_user -e POSTGRES_DB=probe_db \\
        -p 127.0.0.1:15432:5432 postgres:17.10-alpine

then set `PLATFORM_TEST_POSTGRES_HOST=localhost`,
`PLATFORM_TEST_POSTGRES_PORT=15432`, `PLATFORM_TEST_POSTGRES_USER=probe_user`,
`PLATFORM_TEST_POSTGRES_PASSWORD=probe_pw`, `PLATFORM_TEST_POSTGRES_DATABASE=probe_db`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from return_platform.bootstrap.adapters.source_inspection_postgresql import (
    build_postgres_source_inspection_adapter,
)
from return_platform.graph_schema_analyzer.application.source_inspection import (
    ScopedSourceInspection,
)
from return_platform.graph_schema_analyzer.domain.errors import ScopeViolation
from return_platform.graph_schema_analyzer.domain.source_scope import (
    InspectionScope,
    ObjectScope,
    SourceScope,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    ObjectKind,
    RelationshipKind,
    SourceInspectionPort,
)
from return_platform.source_connectors.postgresql import (
    PostgresConnectionSettings,
    run_read_query,
)

SOURCE_ID = "warehouse_pg"

pytestmark = pytest.mark.skipif(
    not os.getenv("PLATFORM_TEST_POSTGRES_HOST"),
    reason="no PostgreSQL source is configured; see this module's docstring",
)


def _connection() -> PostgresConnectionSettings:
    return PostgresConnectionSettings(
        host=os.environ["PLATFORM_TEST_POSTGRES_HOST"],
        port=int(os.getenv("PLATFORM_TEST_POSTGRES_PORT", "5432")),
        user=os.environ["PLATFORM_TEST_POSTGRES_USER"],
        password=os.environ["PLATFORM_TEST_POSTGRES_PASSWORD"],
        database=os.environ["PLATFORM_TEST_POSTGRES_DATABASE"],
        timeout_seconds=10,
    )


async def _execute(statement: str) -> None:
    """Test-only DDL/DML helper, on its own writable connection.

    `run_read_query` opens a read-only transaction, so setup cannot go through
    it -- which is the point of that connection being read-only, and is worth
    seeing demonstrated here. Synchronous in a worker thread for the same reason
    the connector is: psycopg's async connection refuses Windows' default event
    loop.
    """
    import asyncio

    import psycopg

    def _run() -> None:
        with psycopg.connect(_connection().conninfo(), autocommit=True) as conn:
            conn.execute(statement)  # type: ignore[arg-type]

    await asyncio.to_thread(_run)


class _Tables:
    def __init__(self, parent: str, child: str) -> None:
        self.parent = parent
        self.child = child

    @property
    def parent_object(self) -> str:
        return f"public.{self.parent}"

    @property
    def child_object(self) -> str:
        return f"public.{self.child}"


@pytest_asyncio.fixture
async def warehouse_tables() -> AsyncIterator[_Tables]:
    """A warehouse and a bay table joined by a composite foreign key.

    Composite for the same reason as the SQL Server module: a single-column key
    would pass an implementation that reports only a constraint's first column.
    """
    suffix = uuid.uuid4().hex[:10]
    tables = _Tables(f"insp_warehouse_{suffix}", f"insp_bay_{suffix}")
    await _execute(
        f"""
        CREATE TABLE public.{tables.parent} (
            region_code text NOT NULL,
            warehouse_code text NOT NULL,
            display_name text NULL,
            opened_at timestamptz NOT NULL,
            PRIMARY KEY (region_code, warehouse_code)
        )
        """
    )
    await _execute(
        f"""
        CREATE TABLE public.{tables.child} (
            bay_id text PRIMARY KEY,
            region_code text NOT NULL,
            warehouse_code text NOT NULL,
            aisle text NULL,
            CONSTRAINT fk_{tables.child} FOREIGN KEY (region_code, warehouse_code)
                REFERENCES public.{tables.parent} (region_code, warehouse_code)
        )
        """
    )
    await _execute(
        f"CREATE INDEX ix_{tables.child}_region_aisle ON public.{tables.child} (region_code, aisle)"
    )
    await _execute(
        f"INSERT INTO public.{tables.parent} VALUES "
        "('EAST', 'W-001', 'East Main', '2026-01-01T00:00:00Z'), "
        "('WEST', 'W-002', NULL, '2026-02-01T00:00:00Z'), "
        "('WEST', 'W-003', 'West Annex', '2026-03-01T00:00:00Z')"
    )
    await _execute(f"ANALYZE public.{tables.parent}")
    try:
        yield tables
    finally:
        await _execute(f"DROP TABLE public.{tables.child}")
        await _execute(f"DROP TABLE public.{tables.parent}")


@pytest.fixture
def inspection() -> SourceInspectionPort:
    return build_postgres_source_inspection_adapter(_connection(), source_id=SOURCE_ID)


@pytest.mark.asyncio
async def test_validate_reports_the_server_it_actually_reached(
    inspection: SourceInspectionPort,
) -> None:
    """The first proof that a PostgreSQL driver is installed at all -- §5A has
    required this connector since the audit and none was."""
    result = await inspection.validate(source_id=SOURCE_ID)
    assert result.reachable is True
    assert result.server_version is not None
    assert "PostgreSQL" in result.server_version


@pytest.mark.asyncio
async def test_an_unreachable_source_is_reported_rather_than_raised() -> None:
    """`validate` is the operator's connection test; a stack trace is not an
    answer they can act on."""
    unreachable = PostgresConnectionSettings(
        host="127.0.0.1",
        port=1,
        user="none",
        password="none",
        database="none",
        timeout_seconds=2,
    )
    result = await build_postgres_source_inspection_adapter(
        unreachable, source_id=SOURCE_ID
    ).validate(source_id=SOURCE_ID)
    assert result.reachable is False
    assert result.detail


@pytest.mark.asyncio
async def test_the_read_connection_refuses_to_write(warehouse_tables: _Tables) -> None:
    """The read-only transaction is the structural half of "source systems are
    read-only to the analyzer": a defect above this line is refused by the server
    rather than by our own discipline."""
    import psycopg

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        await run_read_query(
            _connection(),
            f"INSERT INTO public.{warehouse_tables.parent} VALUES "
            "('X', 'Y', NULL, now()) RETURNING region_code",
            {},
        )


@pytest.mark.asyncio
async def test_list_objects_names_user_tables_and_excludes_the_server_catalogue(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """A listing including `pg_catalog` would invite the analyzer to propose a
    graph schema for PostgreSQL's own metadata."""
    objects = await inspection.list_objects(source_id=SOURCE_ID)
    names = {item.object_name for item in objects}
    assert warehouse_tables.parent_object in names
    assert not any(name.startswith(("pg_catalog.", "information_schema.")) for name in names)
    assert all(item.object_kind in (ObjectKind.TABLE, ObjectKind.VIEW) for item in objects)


@pytest.mark.asyncio
async def test_describe_object_takes_types_and_nullability_from_the_catalogue(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """Inferring from sampled rows would report `display_name` as non-nullable
    whenever the sample missed its NULL."""
    description = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=warehouse_tables.parent_object
    )
    by_name = {field.field_name: field for field in description.fields}
    assert by_name["region_code"].nullable is False
    assert by_name["display_name"].nullable is True
    assert "timestamp" in by_name["opened_at"].declared_type


@pytest.mark.asyncio
async def test_an_unanalysed_table_reports_no_row_count_rather_than_zero(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """`reltuples` is -1 on a table that has never been analysed. Reporting that
    as 0 would rank an unanalysed table as the most selective thing in the
    schema; reporting -1 would be a negative row count."""
    child = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=warehouse_tables.child_object
    )
    parent = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=warehouse_tables.parent_object
    )
    assert child.approximate_row_count is None
    assert parent.approximate_row_count == 3


@pytest.mark.asyncio
async def test_profile_reports_statistics_and_never_a_value(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """`profile` is the weaker grant W4.8 consumes; a value reaching the result
    would make it equivalent to sampling."""
    profile = await inspection.profile(
        source_id=SOURCE_ID, object_name=warehouse_tables.parent_object, sample_size=10
    )
    by_name = {field.field_name: field for field in profile.fields}
    assert profile.sampled_rows == 3
    assert by_name["display_name"].null_rate == pytest.approx(1 / 3)
    assert by_name["warehouse_code"].identifier_candidate is True
    assert by_name["region_code"].identifier_candidate is False
    assert by_name["opened_at"].change_tracking_candidate is True
    assert "East Main" not in profile.model_dump_json()


@pytest.mark.asyncio
async def test_list_indexes_preserves_the_declared_key_order(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """Joining `pg_attribute` without `unnest(indkey) WITH ORDINALITY` returns
    columns in attribute order, which describes an access path the index does not
    offer -- a lookup by `aisle` alone does not use `(region_code, aisle)`."""
    indexes = await inspection.list_indexes(
        source_id=SOURCE_ID, object_name=warehouse_tables.child_object
    )
    by_name = {index.index_name: index for index in indexes}
    composite = by_name[f"ix_{warehouse_tables.child}_region_aisle"]
    assert composite.fields == ("region_code", "aisle")
    assert composite.unique is False
    assert any(index.primary for index in indexes)


@pytest.mark.asyncio
async def test_list_relationships_reports_both_columns_of_a_composite_key(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """Reporting only a constraint's first column produces a graph schema that
    joins on the wrong thing, and does so exactly when the first column is not
    selective -- which is when composites are used."""
    relationships = await inspection.list_relationships(
        source_id=SOURCE_ID, object_name=warehouse_tables.child_object
    )
    assert len(relationships) == 1
    observed = relationships[0]
    assert observed.relationship_kind is RelationshipKind.FOREIGN_KEY
    assert observed.from_object == warehouse_tables.child_object
    assert observed.to_object == warehouse_tables.parent_object
    assert observed.from_fields == ("region_code", "warehouse_code")
    assert observed.to_fields == ("region_code", "warehouse_code")


@pytest.mark.asyncio
async def test_an_ungranted_table_is_refused_although_the_connection_can_read_it(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """W4.5's headline requirement, on the fourth backend. The connection has
    full read access -- the tests above use it -- so the refusal is the code's."""
    scoped = ScopedSourceInspection(
        inspection,
        scope=InspectionScope(
            sources=(
                SourceScope(
                    source_id=SOURCE_ID,
                    objects=(
                        ObjectScope(
                            object_name=warehouse_tables.child_object,
                            fields=frozenset({"bay_id"}),
                        ),
                    ),
                    max_sample_rows=2,
                ),
            )
        ),
    )
    with pytest.raises(ScopeViolation):
        await scoped.describe_object(
            source_id=SOURCE_ID, object_name=warehouse_tables.parent_object
        )
    listed = await scoped.list_objects(source_id=SOURCE_ID)
    assert {item.object_name for item in listed} == {warehouse_tables.child_object}
