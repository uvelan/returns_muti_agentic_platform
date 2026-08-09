"""Generic SQL Server SourceScanConnector -- no business table/column names here.

Moved from `dynamic_knowledge.connectors.sqlserver` (Phase 8 / Wave C1) --
`dynamic_knowledge.connectors.sqlserver` re-exports these names unchanged so
existing importers do not need to change.

Unlike MongoDB there is no universal auto-generated monotonic key to fall
back on, so every SQL Server source must configure incremental_cursor_field
(a datetime column) -- scan() raises SqlServerConnectorError for a source
that omits it, rather than guessing a column.

Follows this codebase's existing pattern for SQL Server access
(sync_service.py): pymssql is synchronous, so every blocking call is wrapped
in asyncio.to_thread. Table/schema/column names are validated against a safe
identifier pattern before being embedded in bracketed SQL identifiers
([name]); all values are always passed as query parameters.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pymssql

from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.compilation import compile_source_read
from return_platform.source_connectors.contracts import (
    CursorComparison,
    LogicalTargetedReadPlan,
    RawSourceDocument,
    RawSourcePage,
    SourceConnectorCapabilities,
    SourceCursor,
)
from return_platform.source_connectors.identifiers import UnsafeIdentifierError, validate_identifier
from return_platform.source_connectors.path_resolution import resolve_physical_path

_FIELD_DATETIME = "FIELD_DATETIME"
_COLON_PARAMETER = re.compile(r":(\w+)")

CAPABILITIES = SourceConnectorCapabilities(
    supports_high_watermark=True,
    supports_bounded_scan=True,
    supports_delta_replay=False,
    supports_delete_events=False,
    supports_stable_ordering=False,
    supports_snapshot_read=False,
    supports_partitioned_cursors=False,
    supports_cursor_comparison=True,
    supports_point_lookup=True,
)


class SqlServerConnectorError(RuntimeError):
    """A SQL Server source could not be scanned as configured."""


def _validate_identifier(value: str, *, what: str) -> str:
    try:
        return validate_identifier(value, what=what)
    except UnsafeIdentifierError as exc:
        raise SqlServerConnectorError(str(exc)) from exc


class SqlServerConnectionSettings:
    """Plain connection parameters -- kept separate from Settings so this
    module has no dependency on the application's configuration layer."""

    def __init__(
        self,
        *,
        server: str,
        port: int,
        user: str,
        password: str,
        database: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.timeout_seconds = timeout_seconds


def _run_query(
    connection: SqlServerConnectionSettings, query: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    timeout = max(1, int(connection.timeout_seconds))
    with pymssql.connect(
        server=connection.server,
        port=str(connection.port),
        user=connection.user,
        password=connection.password,
        database=connection.database,
        login_timeout=timeout,
        timeout=timeout,
        autocommit=True,
    ) as conn:
        with conn.cursor(as_dict=True) as cursor:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]


class SqlServerSourceScanConnector:
    """One connector instance serves every SQL Server-backed source in one schema.

    SourceScanConnector.capture_high_watermark/compare_cursors take no schema
    parameter (only scan() does), so a connector that needs schema to resolve
    a source's table/column -- as any real SQL Server connector must -- has to
    have it bound at construction time. The schema passed into scan() is
    ignored in favor of this bound one, so all three methods always agree on
    one configuration for the lifetime of this connector instance; construct
    a fresh connector (via the registry) if the active schema changes.
    """

    def __init__(
        self,
        connection: SqlServerConnectionSettings,
        *,
        schema: ActiveSchema,
        page_size: int = 500,
        max_records_per_source: int | None = None,
    ) -> None:
        self._connection = connection
        self._schema = schema
        self._page_size = page_size
        self._max_records_per_source = max_records_per_source

    def capabilities(self) -> SourceConnectorCapabilities:
        return CAPABILITIES

    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor:
        namespace, table, cursor_field = self._resolve(source_asset_id)
        rows = await asyncio.to_thread(
            _run_query,
            self._connection,
            f"SELECT MAX([{cursor_field}]) AS watermark FROM [{namespace}].[{table}]",
            {},
        )
        watermark = rows[0]["watermark"] if rows else None
        if watermark is None:
            watermark = datetime.now(UTC)
        return SourceCursor(cursor_type=_FIELD_DATETIME, encoded_value=_isoformat(watermark))

    def compare_cursors(
        self, *, source_asset_id: str, left: SourceCursor, right: SourceCursor
    ) -> CursorComparison:
        del source_asset_id
        if left.cursor_type != right.cursor_type or left.cursor_type != _FIELD_DATETIME:
            raise SqlServerConnectorError(
                f"cannot compare cursors of type {left.cursor_type!r} vs {right.cursor_type!r}"
            )
        left_value = datetime.fromisoformat(left.encoded_value)
        right_value = datetime.fromisoformat(right.encoded_value)
        if left_value < right_value:
            return CursorComparison.BEFORE
        if left_value > right_value:
            return CursorComparison.AFTER
        return CursorComparison.EQUAL

    async def scan(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        after: SourceCursor | None,
        through: SourceCursor,
    ) -> AsyncIterator[RawSourcePage]:
        del (
            schema
        )  # this connector always uses the schema bound at construction; see class docstring
        namespace, table, cursor_field = self._resolve(source_asset_id)
        params: dict[str, Any] = {"through": datetime.fromisoformat(through.encoded_value)}
        predicate = f"[{cursor_field}] <= %(through)s"
        if after is not None:
            params["after"] = datetime.fromisoformat(after.encoded_value)
            predicate = f"[{cursor_field}] > %(after)s AND {predicate}"
        top = (
            f"TOP ({int(self._max_records_per_source)}) "
            if self._max_records_per_source is not None
            else ""
        )
        query = (
            f"SELECT {top}* FROM [{namespace}].[{table}] "
            f"WHERE {predicate} ORDER BY [{cursor_field}] ASC"
        )
        rows = await asyncio.to_thread(_run_query, self._connection, query, params)

        batch: list[RawSourceDocument] = []
        last_cursor_value: datetime | None = None
        for row in rows:
            last_cursor_value = row.get(cursor_field)
            identity = "|".join(str(value) for value in row.values())
            batch.append(
                RawSourceDocument(operation="UPSERT", document=row, source_identity=identity)
            )
            if len(batch) >= self._page_size:
                yield _page(batch, last_cursor_value, through)
                batch = []
        if batch:
            yield _page(batch, last_cursor_value, through)

    async def targeted_read(
        self, *, schema: ActiveSchema, plan: LogicalTargetedReadPlan
    ) -> RawSourcePage:
        """Bounded, anchor-conditioned read for one entity -- never a source-wide
        scan. Reuses `compile_source_read`'s MSSQL branch (AND-only conditions)
        rather than re-deriving filter translation here.

        `compile_source_read` emits colon-style bind parameters (`:p0`,
        `:p0_from`, `:p0_to`) shared across its Mongo/SQL/Neo4j branches;
        pymssql only accepts its own `%(name)s` pyformat style (see scan()'s
        `%(through)s` above), so the compiled statement is translated at this
        connector boundary rather than changing compile_source_read's shared
        output shape for every caller.
        """

        del (
            schema
        )  # this connector always uses the schema bound at construction; see class docstring
        compiled = compile_source_read(self._schema, plan)
        query = _COLON_PARAMETER.sub(r"%(\1)s", compiled.statement)
        rows = await asyncio.to_thread(_run_query, self._connection, query, compiled.parameters)
        documents = tuple(
            RawSourceDocument(
                operation="UPSERT",
                document=row,
                source_identity="|".join(str(value) for value in row.values()),
            )
            for row in rows
        )
        return RawSourcePage(
            documents=documents,
            next_cursor=None,
            high_watermark=None,
            observed_at=datetime.now(UTC),
        )

    def _resolve(self, source_asset_id: str) -> tuple[str, str, str]:
        source = self._schema.sources[source_asset_id]
        namespace = source.object_ref.get("namespace")
        table = source.object_ref.get("name")
        if not namespace or not table:
            raise SqlServerConnectorError(
                f"source {source_asset_id!r} object_ref is missing 'namespace' and/or 'name'"
            )
        if source.incremental_cursor_field is None:
            raise SqlServerConnectorError(
                f"source {source_asset_id!r} has no incremental_cursor_field configured; "
                "SQL Server sources have no fallback ordering column"
            )
        column = self._resolve_physical_column(source_asset_id, source.incremental_cursor_field)
        return (
            _validate_identifier(namespace, what="schema"),
            _validate_identifier(table, what="table"),
            _validate_identifier(column, what="column"),
        )

    def _resolve_physical_column(self, source_asset_id: str, field_id: str) -> str:
        """incremental_cursor_field is a logical field_id, never a physical
        column name -- resolve it through every entity this source backs and
        require they all agree, rather than assuming field_id == column name."""

        path = resolve_physical_path(
            self._schema,
            source_asset_id=source_asset_id,
            field_id=field_id,
            error_cls=SqlServerConnectorError,
            noun="physical columns",
        )
        if len(path) != 1:
            raise SqlServerConnectorError(
                f"field {field_id!r} has a nested physical_path {path!r}; "
                "SQL Server columns must be flat"
            )
        return path[0]


def _isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _page(
    documents: list[RawSourceDocument], last_cursor_value: datetime | None, through: SourceCursor
) -> RawSourcePage:
    next_cursor = (
        SourceCursor(cursor_type=_FIELD_DATETIME, encoded_value=_isoformat(last_cursor_value))
        if last_cursor_value is not None
        else None
    )
    return RawSourcePage(
        documents=tuple(documents),
        next_cursor=next_cursor,
        high_watermark=through,
        observed_at=datetime.now(UTC),
    )


async def sample_rows(
    connection: SqlServerConnectionSettings,
    *,
    namespace: str,
    table: str,
    order_by_column: str,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Bounded, offset-paginated sample read for admin browse/preview use
    cases -- never used for durable sync (see scan() for that)."""

    if limit < 1:
        raise SqlServerConnectorError(f"sample limit must be >= 1, got {limit!r}")
    safe_namespace = _validate_identifier(namespace, what="schema")
    safe_table = _validate_identifier(table, what="table")
    safe_order_by = _validate_identifier(order_by_column, what="column")
    query = (
        f"SELECT * FROM [{safe_namespace}].[{safe_table}] "
        f"ORDER BY [{safe_order_by}] OFFSET %(offset)s ROWS FETCH NEXT %(limit)s ROWS ONLY"
    )
    return await asyncio.to_thread(
        _run_query, connection, query, {"offset": max(0, offset), "limit": limit}
    )


async def fetch_row(
    connection: SqlServerConnectionSettings,
    *,
    namespace: str,
    table: str,
    key_column: str,
    key_value: Any,
) -> dict[str, Any] | None:
    """Bounded single-row lookup by exact key -- no scan, no cursor."""

    safe_namespace = _validate_identifier(namespace, what="schema")
    safe_table = _validate_identifier(table, what="table")
    safe_key_column = _validate_identifier(key_column, what="column")
    query = f"SELECT * FROM [{safe_namespace}].[{safe_table}] WHERE [{safe_key_column}] = %(key)s"
    rows = await asyncio.to_thread(_run_query, connection, query, {"key": key_value})
    return rows[0] if rows else None
