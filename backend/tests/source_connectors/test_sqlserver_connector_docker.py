"""Real SQL Server tests for source_connectors.sqlserver -- no mocks.

Per the execution plan's "validate against Docker Mongo/SQL, not mocks" --
the existing tests/dynamic_knowledge/test_sqlserver_connector.py tests are
fast, fake-based unit tests for edge cases; these are the first real-infra
proof that the connector's SQL is actually correct against a real server.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pymssql
import pytest

from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.source_connectors.sqlserver import (
    SqlServerConnectionSettings,
    SqlServerSourceScanConnector,
    fetch_row,
    sample_rows,
)
from tests.source_connectors._schema_fixture import build_active_schema


def _connection(settings: Settings) -> SqlServerConnectionSettings:
    return SqlServerConnectionSettings(
        server=settings.sqlserver_host,
        port=settings.sqlserver_port,
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=settings.sqlserver_database,
        timeout_seconds=10,
    )


def _configured_id(document: Mapping[str, Any] | None) -> Any:
    assert document is not None
    return document["configured_id"]


def _execute_ddl_or_dml(
    connection: SqlServerConnectionSettings, statement: str, params: Any
) -> None:
    """Test-only helper for DDL/DML setup -- unlike _run_query (which always
    fetches a result set), CREATE/INSERT/DROP have none to fetch."""

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
            cursor.execute(statement, params)


@pytest.fixture
def sql_table(test_settings: Settings) -> Iterator[str]:
    """Create and seed a throwaway table for one test, dropping it after."""

    table = f"source_connectors_test_{uuid.uuid4().hex[:12]}"
    connection = _connection(test_settings)
    _execute_ddl_or_dml(
        connection,
        f"""
        CREATE TABLE [dbo].[{table}] (
            configured_id NVARCHAR(64) NOT NULL PRIMARY KEY,
            configured_changed_at DATETIME2 NOT NULL
        )
        """,
        {},
    )
    rows = [
        ("row-1", datetime(2026, 1, 1, tzinfo=UTC)),
        ("row-2", datetime(2026, 1, 2, tzinfo=UTC)),
        ("row-3", datetime(2026, 1, 3, tzinfo=UTC)),
    ]
    for configured_id, changed_at in rows:
        _execute_ddl_or_dml(
            connection,
            f"INSERT INTO [dbo].[{table}] (configured_id, configured_changed_at) "
            "VALUES (%(id)s, %(changed_at)s)",
            {"id": configured_id, "changed_at": changed_at},
        )
    yield table
    _execute_ddl_or_dml(connection, f"DROP TABLE [dbo].[{table}]", {})


@pytest.mark.asyncio
async def test_scan_reads_real_rows_ordered_by_cursor_field(
    test_settings: Settings, sql_table: str
) -> None:
    schema = build_active_schema(mongo_collection="unused", sql_table=sql_table, sql_schema="dbo")
    connector = SqlServerSourceScanConnector(_connection(test_settings), schema=schema)
    watermark = await connector.capture_high_watermark(source_asset_id="source_sql")
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_sql", after=None, through=watermark
        )
    ]
    identities = [
        _configured_id(document.document) for page in pages for document in page.documents
    ]
    assert identities == ["row-1", "row-2", "row-3"]


@pytest.mark.asyncio
async def test_targeted_read_returns_only_the_matching_row(
    test_settings: Settings, sql_table: str
) -> None:
    schema = build_active_schema(mongo_collection="unused", sql_table=sql_table, sql_schema="dbo")
    connector = SqlServerSourceScanConnector(_connection(test_settings), schema=schema)
    plan = build_targeted_read_plan(
        schema=schema, entity_id="entity_sql", normalized_anchors={"id": ("EXACT", "row-2")}
    )
    page = await connector.targeted_read(schema=schema, plan=plan)
    assert len(page.documents) == 1
    assert _configured_id(page.documents[0].document) == "row-2"


@pytest.mark.asyncio
async def test_sample_rows_paginates_real_table(test_settings: Settings, sql_table: str) -> None:
    connection = _connection(test_settings)
    first_page = await sample_rows(
        connection,
        namespace="dbo",
        table=sql_table,
        order_by_column="configured_id",
        offset=0,
        limit=2,
    )
    second_page = await sample_rows(
        connection,
        namespace="dbo",
        table=sql_table,
        order_by_column="configured_id",
        offset=2,
        limit=2,
    )
    assert [row["configured_id"] for row in first_page] == ["row-1", "row-2"]
    assert [row["configured_id"] for row in second_page] == ["row-3"]


@pytest.mark.asyncio
async def test_fetch_row_returns_exact_row_by_key(test_settings: Settings, sql_table: str) -> None:
    connection = _connection(test_settings)
    row = await fetch_row(
        connection, namespace="dbo", table=sql_table, key_column="configured_id", key_value="row-2"
    )
    assert row is not None
    assert row["configured_id"] == "row-2"

    missing = await fetch_row(
        connection,
        namespace="dbo",
        table=sql_table,
        key_column="configured_id",
        key_value="row-404",
    )
    assert missing is None
