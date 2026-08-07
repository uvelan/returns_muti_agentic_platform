from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.dynamic_knowledge.connectors import sqlserver as sqlserver_module
from return_platform.dynamic_knowledge.connectors.sqlserver import (
    SqlServerConnectionSettings,
    SqlServerConnectorError,
    SqlServerSourceScanConnector,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import CursorComparison, SourceCursor
from return_platform.dynamic_knowledge.schema import ActiveSchema


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: tuple[str, dict[str, Any]] | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def execute(self, query: str, params: dict[str, Any]) -> None:
        self.executed = (query, params)

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def cursor(self, as_dict: bool) -> FakeCursor:
        del as_dict
        return self._cursor


def _connection_settings() -> SqlServerConnectionSettings:
    return SqlServerConnectionSettings(
        server="sql.internal", port=1433, user="u", password="p", database="db"
    )


def _sql_source_schema(active_schema: ActiveSchema, *, cursor_field: str | None) -> ActiveSchema:
    raw = active_schema.model_dump(mode="json")
    raw["sources"]["source_b"]["object_ref"] = {
        "database": "db",
        "namespace": "dbo",
        "name": "objects",
    }
    raw["sources"]["source_b"]["incremental_cursor_field"] = cursor_field
    return ActiveSchema.model_validate(raw)


def _patch_connect(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> FakeCursor:
    cursor = FakeCursor(rows)
    monkeypatch.setattr(
        sqlserver_module.pymssql, "connect", lambda **kwargs: FakeConnection(cursor)
    )
    return cursor


@pytest.mark.asyncio
async def test_resolve_rejects_source_without_incremental_cursor_field(
    active_schema: ActiveSchema,
) -> None:
    schema = _sql_source_schema(active_schema, cursor_field=None)
    connector = SqlServerSourceScanConnector(_connection_settings(), schema=schema)
    with pytest.raises(SqlServerConnectorError, match="incremental_cursor_field"):
        await connector.capture_high_watermark(source_asset_id="source_b")


@pytest.mark.asyncio
async def test_capture_high_watermark_queries_max_of_configured_column(
    active_schema: ActiveSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = _sql_source_schema(active_schema, cursor_field="parent_id")
    max_value = datetime(2026, 8, 6, tzinfo=UTC)
    cursor = _patch_connect(monkeypatch, [{"watermark": max_value}])
    connector = SqlServerSourceScanConnector(_connection_settings(), schema=schema)
    watermark = await connector.capture_high_watermark(source_asset_id="source_b")
    assert watermark.cursor_type == "FIELD_DATETIME"
    assert watermark.encoded_value == max_value.isoformat()
    assert cursor.executed is not None
    assert "MAX([parent_id])" in cursor.executed[0]
    assert "[dbo].[objects]" in cursor.executed[0]


def test_compare_cursors_orders_datetimes(active_schema: ActiveSchema) -> None:
    schema = _sql_source_schema(active_schema, cursor_field="parent_id")
    connector = SqlServerSourceScanConnector(_connection_settings(), schema=schema)
    earlier = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    )
    later = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 1, 2, tzinfo=UTC).isoformat()
    )
    assert (
        connector.compare_cursors(source_asset_id="source_b", left=earlier, right=later)
        is CursorComparison.BEFORE
    )
    assert (
        connector.compare_cursors(source_asset_id="source_b", left=later, right=earlier)
        is CursorComparison.AFTER
    )


@pytest.mark.asyncio
async def test_scan_parameterizes_after_and_through_bounds(
    active_schema: ActiveSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = _sql_source_schema(active_schema, cursor_field="parent_id")
    rows = [
        {"configured_id": "A-1", "parent_id": datetime(2026, 1, 2, tzinfo=UTC)},
        {"configured_id": "A-2", "parent_id": datetime(2026, 1, 3, tzinfo=UTC)},
    ]
    cursor = _patch_connect(monkeypatch, rows)
    connector = SqlServerSourceScanConnector(_connection_settings(), schema=schema, page_size=10)
    after = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    )
    through = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 1, 3, tzinfo=UTC).isoformat()
    )
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_b", after=after, through=through
        )
    ]
    assert len(pages) == 1
    assert len(pages[0].documents) == 2
    assert cursor.executed is not None
    query, params = cursor.executed
    assert "%(after)s" in query and "%(through)s" in query
    assert params["after"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert params["through"] == datetime(2026, 1, 3, tzinfo=UTC)


@pytest.mark.asyncio
async def test_scan_without_after_omits_the_lower_bound(
    active_schema: ActiveSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = _sql_source_schema(active_schema, cursor_field="parent_id")
    cursor = _patch_connect(monkeypatch, [])
    connector = SqlServerSourceScanConnector(_connection_settings(), schema=schema)
    through = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 1, 3, tzinfo=UTC).isoformat()
    )
    async for _ in connector.scan(
        schema=schema, source_asset_id="source_b", after=None, through=through
    ):
        pass
    assert cursor.executed is not None
    query, params = cursor.executed
    assert "%(after)s" not in query
    assert "after" not in params


@pytest.mark.asyncio
async def test_scan_pages_at_the_configured_page_size(
    active_schema: ActiveSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = _sql_source_schema(active_schema, cursor_field="parent_id")
    rows = [
        {"configured_id": f"A-{i}", "parent_id": datetime(2026, 1, i, tzinfo=UTC)}
        for i in range(1, 6)
    ]
    _patch_connect(monkeypatch, rows)
    connector = SqlServerSourceScanConnector(_connection_settings(), schema=schema, page_size=2)
    through = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 1, 5, tzinfo=UTC).isoformat()
    )
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_b", after=None, through=through
        )
    ]
    assert [len(page.documents) for page in pages] == [2, 2, 1]


def test_unsafe_namespace_is_rejected(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["sources"]["source_b"]["object_ref"] = {
        "database": "db",
        "namespace": "dbo]; DROP TABLE x; --",
        "name": "objects",
    }
    raw["sources"]["source_b"]["incremental_cursor_field"] = "parent_id"
    schema = ActiveSchema.model_validate(raw)
    connector = SqlServerSourceScanConnector(_connection_settings(), schema=schema)
    with pytest.raises(SqlServerConnectorError, match="unsafe SQL Server schema"):
        connector._resolve("source_b")
