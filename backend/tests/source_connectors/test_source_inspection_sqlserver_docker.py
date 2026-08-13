"""The SQL Server inspection adapter, and the scope filter, against real SQL Server.

This is the adapter W2.4 is blocked on -- without it the analyzer cannot describe
the SQL warehouse source, so warehouse and bay have no graph entity and W2.7 has
nothing to sync. Proving it against a fake would prove nothing about the eight
catalogue queries, which is where every realistic defect lives: a join that drops
composite key columns, an index whose reported field order is attribute order
rather than key order, a row count that turns out to be a full scan.

The scope tests are here rather than beside a stub for the same reason. "A model
that names a table it was not granted is refused" is only worth asserting against
a database where that table exists and the connection could read it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pymssql
import pytest

from return_platform.bootstrap.adapters.source_inspection_sqlserver import (
    build_sqlserver_source_inspection_adapter,
)
from return_platform.configuration.settings import Settings
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
from return_platform.source_connectors.sqlserver import SqlServerConnectionSettings

SOURCE_ID = "warehouse_sql"


def _connection(settings: Settings) -> SqlServerConnectionSettings:
    return SqlServerConnectionSettings(
        server=settings.sqlserver_host,
        port=settings.sqlserver_port,
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=settings.sqlserver_database,
        timeout_seconds=10,
    )


def _execute(connection: SqlServerConnectionSettings, statement: str) -> None:
    """Test-only DDL/DML helper. The connector under test has no such method and
    must never grow one -- setup is the only reason this exists."""
    with pymssql.connect(
        server=connection.server,
        port=str(connection.port),
        user=connection.user,
        password=connection.password,
        database=connection.database,
        login_timeout=connection.timeout_seconds,
        timeout=connection.timeout_seconds,
        autocommit=True,
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute(statement)


class _Tables:
    """The two related tables one test run inspects."""

    def __init__(self, parent: str, child: str) -> None:
        self.parent = parent
        self.child = child

    @property
    def parent_object(self) -> str:
        return f"dbo.{self.parent}"

    @property
    def child_object(self) -> str:
        return f"dbo.{self.child}"


@pytest.fixture
def warehouse_tables(test_settings: Settings) -> Iterator[_Tables]:
    """A warehouse table and a bay table joined by a composite foreign key.

    Composite on purpose: a single-column key would pass a `list_relationships`
    implementation that reports only the first column of a constraint, which is
    exactly the defect that produces a graph schema joining on the wrong thing.
    """
    suffix = uuid.uuid4().hex[:10]
    tables = _Tables(f"insp_warehouse_{suffix}", f"insp_bay_{suffix}")
    connection = _connection(test_settings)
    _execute(
        connection,
        f"""
        CREATE TABLE [dbo].[{tables.parent}] (
            region_code NVARCHAR(8) NOT NULL,
            warehouse_code NVARCHAR(16) NOT NULL,
            display_name NVARCHAR(64) NULL,
            opened_at DATETIME2 NOT NULL,
            CONSTRAINT [PK_{tables.parent}] PRIMARY KEY (region_code, warehouse_code)
        )
        """,
    )
    _execute(
        connection,
        f"""
        CREATE TABLE [dbo].[{tables.child}] (
            bay_id NVARCHAR(24) NOT NULL PRIMARY KEY,
            region_code NVARCHAR(8) NOT NULL,
            warehouse_code NVARCHAR(16) NOT NULL,
            aisle NVARCHAR(8) NULL,
            CONSTRAINT [FK_{tables.child}] FOREIGN KEY (region_code, warehouse_code)
                REFERENCES [dbo].[{tables.parent}] (region_code, warehouse_code)
        )
        """,
    )
    _execute(
        connection,
        f"CREATE INDEX [IX_{tables.child}_region_aisle] "
        f"ON [dbo].[{tables.child}] (region_code, aisle)",
    )
    for index, (region, code, name) in enumerate(
        [("EAST", "W-001", "East Main"), ("WEST", "W-002", None), ("WEST", "W-003", "West Annex")]
    ):
        value = "NULL" if name is None else f"N'{name}'"
        _execute(
            connection,
            f"INSERT INTO [dbo].[{tables.parent}] "
            "(region_code, warehouse_code, display_name, opened_at) VALUES "
            f"(N'{region}', N'{code}', {value}, '2026-0{index + 1}-01T00:00:00')",
        )
    yield tables
    _execute(connection, f"DROP TABLE [dbo].[{tables.child}]")
    _execute(connection, f"DROP TABLE [dbo].[{tables.parent}]")


@pytest.fixture
def inspection(test_settings: Settings) -> SourceInspectionPort:
    return build_sqlserver_source_inspection_adapter(
        _connection(test_settings), source_id=SOURCE_ID
    )


@pytest.mark.asyncio
async def test_validate_reports_the_server_it_actually_reached(
    inspection: SourceInspectionPort,
) -> None:
    """Catches an adapter that reports success without opening a connection --
    the operator's connection test is the only signal that credentials work."""
    result = await inspection.validate(source_id=SOURCE_ID)
    assert result.reachable is True
    assert result.server_version is not None
    assert "SQL Server" in result.server_version


@pytest.mark.asyncio
async def test_an_unreachable_source_is_reported_rather_than_raised(
    test_settings: Settings,
) -> None:
    """`validate` exists to answer "can we reach this", and half its answers are
    "no". Raising would make the S4 connection-test screen show a stack trace
    where it should show a message an operator can act on."""
    unreachable = SqlServerConnectionSettings(
        server=test_settings.sqlserver_host,
        port=1,
        user=test_settings.sqlserver_user,
        password=test_settings.sqlserver_password.get_secret_value(),
        database=test_settings.sqlserver_database,
        timeout_seconds=2,
    )
    result = await build_sqlserver_source_inspection_adapter(
        unreachable, source_id=SOURCE_ID
    ).validate(source_id=SOURCE_ID)
    assert result.reachable is False
    assert result.detail


@pytest.mark.asyncio
async def test_list_objects_names_user_tables_and_excludes_the_server_catalogue(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """A listing that included `sys` would invite the analyzer to propose a graph
    schema for SQL Server's own metadata."""
    objects = await inspection.list_objects(source_id=SOURCE_ID)
    names = {item.object_name for item in objects}
    assert warehouse_tables.parent_object in names
    assert warehouse_tables.child_object in names
    assert not any(name.startswith(("sys.", "INFORMATION_SCHEMA.")) for name in names)
    assert all(item.object_kind in (ObjectKind.TABLE, ObjectKind.VIEW) for item in objects)


@pytest.mark.asyncio
async def test_describe_object_takes_types_and_nullability_from_the_catalogue(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """Inferring from sampled rows would report `display_name` as non-nullable
    whenever the sample happened to miss its NULL, and the analyzer's validation
    treats a declared type as fact."""
    description = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=warehouse_tables.parent_object
    )
    by_name = {field.field_name: field for field in description.fields}
    assert by_name["region_code"].nullable is False
    assert by_name["display_name"].nullable is True
    assert by_name["opened_at"].declared_type == "datetime2"
    assert description.object_kind is ObjectKind.TABLE


@pytest.mark.asyncio
async def test_the_row_count_is_the_catalogue_estimate_not_a_full_scan(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """A `COUNT(*)` on every describe would scan a warehouse table each time the
    analyzer looked at it; the partition statistic is what "approximate" in the
    field name is promising."""
    description = await inspection.describe_object(
        source_id=SOURCE_ID, object_name=warehouse_tables.parent_object
    )
    assert description.approximate_row_count == 3


@pytest.mark.asyncio
async def test_sample_returns_only_the_columns_it_was_asked_for(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """The projection has to be pushed to the server, or an unlisted column
    crosses the network before anything can filter it out."""
    rows = await inspection.sample(
        source_id=SOURCE_ID,
        object_name=warehouse_tables.parent_object,
        limit=2,
        fields=("region_code", "warehouse_code"),
    )
    assert len(rows) == 2
    assert all(set(row) == {"region_code", "warehouse_code"} for row in rows)


@pytest.mark.asyncio
async def test_profile_reports_statistics_and_never_a_value(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """`profile` is the weaker grant that W4.8 consumes. If a value could reach
    the result, profiling a source would be equivalent to sampling it and the
    distinction would be decorative."""
    profile = await inspection.profile(
        source_id=SOURCE_ID, object_name=warehouse_tables.parent_object, sample_size=10
    )
    assert profile.sampled_rows == 3
    by_name = {field.field_name: field for field in profile.fields}
    assert by_name["display_name"].null_rate == pytest.approx(1 / 3)
    assert by_name["region_code"].null_rate == 0.0
    assert by_name["region_code"].approximate_distinct == 2
    serialised = profile.model_dump_json()
    assert "East Main" not in serialised
    assert "W-001" not in serialised


@pytest.mark.asyncio
async def test_profile_nominates_a_unique_column_as_an_identifier_candidate(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """The selectivity signal W4.8 ranks on. `region_code` repeats and must not
    be nominated, or the ranking would put a two-value column first."""
    profile = await inspection.profile(
        source_id=SOURCE_ID, object_name=warehouse_tables.parent_object, sample_size=10
    )
    by_name = {field.field_name: field for field in profile.fields}
    assert by_name["warehouse_code"].identifier_candidate is True
    assert by_name["region_code"].identifier_candidate is False


@pytest.mark.asyncio
async def test_profile_nominates_the_datetime_column_for_change_tracking(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """A SQL Server source with no datetime column cannot be configured for
    incremental sync at all -- `SqlServerSourceScanConnector` refuses one. Better
    learned during analysis than at first sync."""
    profile = await inspection.profile(
        source_id=SOURCE_ID, object_name=warehouse_tables.parent_object, sample_size=10
    )
    by_name = {field.field_name: field for field in profile.fields}
    assert by_name["opened_at"].change_tracking_candidate is True
    assert by_name["warehouse_code"].change_tracking_candidate is False


@pytest.mark.asyncio
async def test_list_indexes_preserves_the_declared_key_order(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """An index reported in attribute order rather than key order describes an
    access path the server does not offer, and the analyzer would plan against
    it -- a lookup by `aisle` alone does not use `(region_code, aisle)`."""
    indexes = await inspection.list_indexes(
        source_id=SOURCE_ID, object_name=warehouse_tables.child_object
    )
    by_name = {index.index_name: index for index in indexes}
    composite = by_name[f"IX_{warehouse_tables.child}_region_aisle"]
    assert composite.fields == ("region_code", "aisle")
    assert composite.unique is False
    assert any(index.primary for index in indexes)


@pytest.mark.asyncio
async def test_list_relationships_reports_both_columns_of_a_composite_key(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """Reporting only the first column of a composite foreign key produces a
    graph schema that joins on the wrong thing and is wrong exactly when the
    first column is not selective -- which is when composites are used."""
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


def _scoped(
    inspection: SourceInspectionPort, tables: _Tables, *, max_sample_rows: int = 2
) -> ScopedSourceInspection:
    """A grant covering the bay table's identifying columns and nothing else."""
    return ScopedSourceInspection(
        inspection,
        scope=InspectionScope(
            sources=(
                SourceScope(
                    source_id=SOURCE_ID,
                    objects=(
                        ObjectScope(
                            object_name=tables.child_object,
                            fields=frozenset({"bay_id", "region_code"}),
                        ),
                    ),
                    max_sample_rows=max_sample_rows,
                ),
            )
        ),
    )


@pytest.mark.asyncio
async def test_a_table_outside_the_grant_is_refused_although_the_connection_can_read_it(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """W4.5's headline requirement. The connection has full access to the
    warehouse table -- the unscoped adapter reads it in the tests above -- so the
    refusal is the code's, not the database's."""
    with pytest.raises(ScopeViolation):
        await _scoped(inspection, warehouse_tables).describe_object(
            source_id=SOURCE_ID, object_name=warehouse_tables.parent_object
        )


@pytest.mark.asyncio
async def test_an_ungranted_table_is_absent_from_the_listing_a_model_is_shown(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """Refusing by name is not enough on its own: a listing that named the
    warehouse table would hand a model the name it would otherwise have to
    guess, and the refusal would only ever fire after the disclosure."""
    listed = await _scoped(inspection, warehouse_tables).list_objects(source_id=SOURCE_ID)
    names = {item.object_name for item in listed}
    assert names == {warehouse_tables.child_object}


@pytest.mark.asyncio
async def test_an_ungranted_column_is_refused_when_named_and_absent_when_not(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """Both halves matter. Naming `aisle` must fail; omitting `fields` entirely
    must not quietly widen to `SELECT *`, which is the shape that leaks."""
    scoped = _scoped(inspection, warehouse_tables)
    with pytest.raises(ScopeViolation, match="aisle"):
        await scoped.sample(
            source_id=SOURCE_ID,
            object_name=warehouse_tables.child_object,
            limit=1,
            fields=("bay_id", "aisle"),
        )
    described = await scoped.describe_object(
        source_id=SOURCE_ID, object_name=warehouse_tables.child_object
    )
    assert {field.field_name for field in described.fields} == {"bay_id", "region_code"}


@pytest.mark.asyncio
async def test_a_sample_larger_than_the_grant_is_clamped_to_it(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """The bound is the scope's, not the argument's: no value a caller passes can
    widen what a source yields."""
    scoped = _scoped(inspection, warehouse_tables, max_sample_rows=1)
    rows = await scoped.sample(
        source_id=SOURCE_ID, object_name=warehouse_tables.child_object, limit=100
    )
    assert len(rows) <= 1


@pytest.mark.asyncio
async def test_an_index_over_an_ungranted_column_is_withheld_whole(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """Reporting `(region_code, aisle)` with `aisle` stripped would describe an
    index on `region_code` alone, which does not exist."""
    indexes = await _scoped(inspection, warehouse_tables).list_indexes(
        source_id=SOURCE_ID, object_name=warehouse_tables.child_object
    )
    assert all("aisle" not in index.fields for index in indexes)
    assert all(set(index.fields) <= {"bay_id", "region_code"} for index in indexes)


@pytest.mark.asyncio
async def test_a_relationship_to_an_ungranted_table_is_withheld(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """The edge itself discloses that the warehouse table exists and what it is
    keyed by -- which is the disclosure the object grant was drawn to prevent."""
    relationships = await _scoped(inspection, warehouse_tables).list_relationships(
        source_id=SOURCE_ID, object_name=warehouse_tables.child_object
    )
    assert relationships == ()


@pytest.mark.asyncio
async def test_an_object_granted_with_no_readable_field_refuses_before_building_a_query(
    inspection: SourceInspectionPort, warehouse_tables: _Tables
) -> None:
    """An empty field grant has to be refused by the scope layer, not passed
    down. An empty column list reaches SQL Server as `SELECT  FROM ...`, and the
    operator would be debugging a syntax error instead of reading a refusal."""
    scoped = ScopedSourceInspection(
        inspection,
        scope=InspectionScope(
            sources=(
                SourceScope(
                    source_id=SOURCE_ID,
                    objects=(
                        ObjectScope(object_name=warehouse_tables.child_object, fields=frozenset()),
                    ),
                    max_sample_rows=2,
                ),
            )
        ),
    )
    with pytest.raises(ScopeViolation, match="no field of"):
        await scoped.sample(source_id=SOURCE_ID, object_name=warehouse_tables.child_object, limit=1)


@pytest.mark.asyncio
async def test_the_adapter_refuses_a_source_it_was_not_constructed_for(
    inspection: SourceInspectionPort,
) -> None:
    """One adapter serves one source. Answering for another id would make "what
    can this analysis read" a runtime argument rather than a composition-time
    decision."""
    with pytest.raises(ValueError, match="this adapter serves"):
        await inspection.list_objects(source_id="some_other_source")


@pytest.mark.asyncio
async def test_a_three_part_object_name_is_refused_rather_than_silently_truncated(
    inspection: SourceInspectionPort,
) -> None:
    """`other_db.dbo.orders` names a database this connection was not opened
    against. Splitting from the right and reading the last two parts would
    quietly answer about the wrong database."""
    with pytest.raises(Exception, match="cross-database"):
        await inspection.describe_object(source_id=SOURCE_ID, object_name="other_db.dbo.orders")


@pytest.mark.asyncio
async def test_an_object_name_that_is_not_an_identifier_is_refused(
    inspection: SourceInspectionPort,
) -> None:
    """The last gate before a name reaches bracketed SQL. Every value is bound as
    a parameter; identifiers cannot be, so they are validated instead."""
    with pytest.raises(Exception, match="unsafe SQL Server"):
        await inspection.describe_object(
            source_id=SOURCE_ID, object_name="dbo.orders]; DROP TABLE [x"
        )
