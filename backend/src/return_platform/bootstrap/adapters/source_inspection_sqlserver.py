"""`SourceInspectionPort` over one SQL Server database.

The adapter W2.4 is blocked on: without it the analyzer has no way to describe the
SQL warehouse source, so warehouse and bay have no entity, and W2.7 has nothing to
sync. W2.4's own Failure condition names this file.

Everything here reads the system catalogue rather than the data:
`sys.tables`/`sys.columns` for shape, `sys.dm_db_partition_stats` for row counts,
`sys.indexes` for access paths, `sys.foreign_keys` for relationships. Only
`sample` and `profile` touch a user table, and both are bounded by a `TOP (n)`
composed here from an integer -- never from a caller's string.

Object names arrive as `schema.table` and are split and validated on every path
(`split_qualified_name`); values are always bound as query parameters. There is no
method that takes a query, and the shape of `SourceInspectionPort` is why there is
nowhere to add one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import pymssql

from return_platform.bootstrap.adapters.source_inspection_profiling import build_profile
from return_platform.graph_schema_analyzer.ports.source_port import (
    FieldDescription,
    IndexDescription,
    ObjectDescription,
    ObjectKind,
    ObjectProfile,
    RelationshipKind,
    RelationshipObservation,
    SourceInspectionPort,
    SourceObjectRef,
    SourceValidation,
)
from return_platform.source_connectors.identifiers import (
    split_qualified_name,
    validate_identifier,
)
from return_platform.source_connectors.sqlserver import (
    SqlServerConnectionSettings,
    run_read_query,
)

__all__ = ["SqlServerSourceInspectionAdapter", "build_sqlserver_source_inspection_adapter"]

_DEFAULT_SCHEMA = "dbo"

# Ceiling on rows a single inspection call may read, under whatever the scope
# layer already clamped to. Defence in depth: the scope check is the control, and
# this is what holds if an adapter is ever composed without one.
_SAMPLE_CEILING = 100

# Schemas SQL Server owns. Analysing them would propose a graph schema for the
# server's own catalogue.
_EXCLUDED_SCHEMAS = ("sys", "INFORMATION_SCHEMA")


class SqlServerSourceInspectionAdapter:
    """Structurally satisfies `SourceInspectionPort` for exactly one source."""

    def __init__(self, connection: SqlServerConnectionSettings, *, source_id: str) -> None:
        self._connection = connection
        self._source_id = source_id

    async def validate(self, *, source_id: str) -> SourceValidation:
        self._require_source(source_id)
        try:
            rows = await self._query("SELECT @@VERSION AS server_version", {})
        except pymssql.Error as exc:
            return SourceValidation(source_id=source_id, reachable=False, detail=str(exc))
        version = str(rows[0]["server_version"]) if rows else None
        return SourceValidation(
            source_id=source_id,
            reachable=True,
            # `@@VERSION` is a multi-line banner; the first line is the product
            # and build, and the rest is copyright and OS text nobody reads.
            server_version=None if version is None else version.splitlines()[0].strip(),
        )

    async def list_sources(self) -> Sequence[str]:
        return (self._source_id,)

    async def list_objects(self, *, source_id: str) -> Sequence[SourceObjectRef]:
        self._require_source(source_id)
        rows = await self._query(
            """
            SELECT s.name AS schema_name, o.name AS object_name, o.type AS object_type
            FROM sys.objects AS o
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE o.type IN ('U', 'V') AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA')
            ORDER BY s.name, o.name
            """,
            {},
        )
        return tuple(
            SourceObjectRef(
                source_id=source_id,
                object_name=f"{row['schema_name']}.{row['object_name']}",
                object_kind=(
                    ObjectKind.VIEW if str(row["object_type"]).strip() == "V" else ObjectKind.TABLE
                ),
            )
            for row in rows
            if str(row["schema_name"]) not in _EXCLUDED_SCHEMAS
        )

    async def describe_object(self, *, source_id: str, object_name: str) -> ObjectDescription:
        namespace, table = self._resolve(source_id, object_name)
        rows = await self._query(
            """
            SELECT c.name AS column_name, t.name AS type_name, c.is_nullable AS is_nullable
            FROM sys.columns AS c
            JOIN sys.objects AS o ON o.object_id = c.object_id
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            LEFT JOIN sys.types AS t ON t.user_type_id = c.user_type_id
            WHERE s.name = %(schema)s AND o.name = %(table)s
            ORDER BY c.column_id
            """,
            {"schema": namespace, "table": table},
        )
        kind_rows = await self._query(
            """
            SELECT o.type AS object_type
            FROM sys.objects AS o
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE s.name = %(schema)s AND o.name = %(table)s
            """,
            {"schema": namespace, "table": table},
        )
        is_view = bool(kind_rows) and str(kind_rows[0]["object_type"]).strip() == "V"
        return ObjectDescription(
            source_id=source_id,
            object_name=object_name,
            object_kind=ObjectKind.VIEW if is_view else ObjectKind.TABLE,
            fields=tuple(
                FieldDescription(
                    field_name=str(row["column_name"]),
                    declared_type=str(row["type_name"] or "unknown"),
                    nullable=bool(row["is_nullable"]),
                )
                for row in rows
            ),
            approximate_row_count=await self._approximate_row_count(namespace, table),
        )

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        namespace, table = self._resolve(source_id, object_name)
        return tuple(await self._read(namespace, table, limit, fields))

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> ObjectProfile:
        namespace, table = self._resolve(source_id, object_name)
        description = await self.describe_object(source_id=source_id, object_name=object_name)
        return build_profile(
            source_id=source_id,
            object_name=object_name,
            rows=await self._read(namespace, table, sample_size, None),
            approximate_row_count=description.approximate_row_count,
            # The catalogue's types outrank the sampled ones: a nullable column
            # whose sampled values are all NULL has no observed type and a
            # declared type that is still true.
            declared_types={field.field_name: field.declared_type for field in description.fields},
        )

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        namespace, table = self._resolve(source_id, object_name)
        rows = await self._query(
            """
            SELECT i.name AS index_name, i.is_unique AS is_unique,
                   i.is_primary_key AS is_primary, c.name AS column_name,
                   ic.key_ordinal AS key_ordinal
            FROM sys.indexes AS i
            JOIN sys.objects AS o ON o.object_id = i.object_id
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            JOIN sys.index_columns AS ic
              ON ic.object_id = i.object_id AND ic.index_id = i.index_id
            JOIN sys.columns AS c
              ON c.object_id = ic.object_id AND c.column_id = ic.column_id
            WHERE s.name = %(schema)s AND o.name = %(table)s
              AND i.name IS NOT NULL AND ic.is_included_column = 0
            ORDER BY i.name, ic.key_ordinal
            """,
            {"schema": namespace, "table": table},
        )
        # One row per index column; the ORDER BY is what makes the reassembled
        # tuple carry the index's real key order, which is the whole reason an
        # index is useful for one predicate and not another.
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = str(row["index_name"])
            entry = grouped.setdefault(
                name,
                {
                    "unique": bool(row["is_unique"]),
                    "primary": bool(row["is_primary"]),
                    "fields": [],
                },
            )
            entry["fields"].append(str(row["column_name"]))
        return tuple(
            IndexDescription(
                index_name=name,
                fields=tuple(entry["fields"]),
                unique=bool(entry["unique"]),
                primary=bool(entry["primary"]),
            )
            for name, entry in grouped.items()
        )

    async def list_relationships(
        self, *, source_id: str, object_name: str | None = None
    ) -> Sequence[RelationshipObservation]:
        self._require_source(source_id)
        predicate = ""
        params: dict[str, Any] = {}
        if object_name is not None:
            namespace, table = self._resolve(source_id, object_name)
            predicate = "AND ps.name = %(schema)s AND pt.name = %(table)s"
            params = {"schema": namespace, "table": table}
        rows = await self._query(
            f"""
            SELECT fk.name AS constraint_name,
                   ps.name AS parent_schema, pt.name AS parent_table,
                   pc.name AS parent_column,
                   rs.name AS referenced_schema, rt.name AS referenced_table,
                   rc.name AS referenced_column,
                   fkc.constraint_column_id AS ordinal
            FROM sys.foreign_keys AS fk
            JOIN sys.foreign_key_columns AS fkc
              ON fkc.constraint_object_id = fk.object_id
            JOIN sys.objects AS pt ON pt.object_id = fk.parent_object_id
            JOIN sys.schemas AS ps ON ps.schema_id = pt.schema_id
            JOIN sys.objects AS rt ON rt.object_id = fk.referenced_object_id
            JOIN sys.schemas AS rs ON rs.schema_id = rt.schema_id
            JOIN sys.columns AS pc
              ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
            JOIN sys.columns AS rc
              ON rc.object_id = fkc.referenced_object_id
             AND rc.column_id = fkc.referenced_column_id
            WHERE 1 = 1 {predicate}
            ORDER BY fk.name, fkc.constraint_column_id
            """,
            params,
        )
        # Composite keys arrive as one row per column, in ordinal order. Reporting
        # only the first would describe a join on the wrong thing.
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = str(row["constraint_name"])
            entry = grouped.setdefault(
                name,
                {
                    "from_object": f"{row['parent_schema']}.{row['parent_table']}",
                    "to_object": f"{row['referenced_schema']}.{row['referenced_table']}",
                    "from_fields": [],
                    "to_fields": [],
                },
            )
            entry["from_fields"].append(str(row["parent_column"]))
            entry["to_fields"].append(str(row["referenced_column"]))
        return tuple(
            RelationshipObservation(
                source_id=source_id,
                relationship_kind=RelationshipKind.FOREIGN_KEY,
                from_object=str(entry["from_object"]),
                from_fields=tuple(entry["from_fields"]),
                to_object=str(entry["to_object"]),
                to_fields=tuple(entry["to_fields"]),
                constraint_name=name,
            )
            for name, entry in grouped.items()
        )

    async def _approximate_row_count(self, namespace: str, table: str) -> int | None:
        """From partition statistics, never `COUNT(*)`.

        An exact count over a warehouse table is a full scan, and the analyzer
        asks for one on every object it describes. The catalogue's number is
        maintained by the engine and is what "approximate" in the field name is
        promising.
        """
        rows = await self._query(
            """
            SELECT SUM(ps.row_count) AS approximate_rows
            FROM sys.dm_db_partition_stats AS ps
            JOIN sys.objects AS o ON o.object_id = ps.object_id
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE s.name = %(schema)s AND o.name = %(table)s AND ps.index_id IN (0, 1)
            """,
            {"schema": namespace, "table": table},
        )
        if not rows or rows[0]["approximate_rows"] is None:
            return None
        return int(rows[0]["approximate_rows"])

    async def _read(
        self, namespace: str, table: str, limit: int, fields: Sequence[str] | None
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), _SAMPLE_CEILING))
        if fields is None:
            projection = "*"
        else:
            projection = ", ".join(
                f"[{validate_identifier(field, what='column')}]" for field in fields
            )
        # `TOP` will not accept a bind parameter in every SQL Server version's
        # parser, so the bound is composed from an int that has already been
        # clamped -- never from anything a caller supplied as text.
        return await self._query(
            f"SELECT TOP ({bounded}) {projection} FROM [{namespace}].[{table}]", {}
        )

    async def _query(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """pymssql is synchronous; every blocking call goes to a worker thread,
        matching `SqlServerSourceScanConnector`."""
        return await asyncio.to_thread(run_read_query, self._connection, query, params)

    def _resolve(self, source_id: str, object_name: str) -> tuple[str, str]:
        self._require_source(source_id)
        return split_qualified_name(object_name, default_namespace=_DEFAULT_SCHEMA)

    def _require_source(self, source_id: str) -> None:
        if source_id != self._source_id:
            raise ValueError(
                f"this adapter serves {self._source_id!r}, not {source_id!r}; "
                "resolve the adapter published for that source."
            )


def build_sqlserver_source_inspection_adapter(
    connection: SqlServerConnectionSettings, *, source_id: str
) -> SourceInspectionPort:
    """Typed factory -- the return annotation is what makes mypy prove port
    conformance, rather than a runtime isinstance that only checks method names."""
    return SqlServerSourceInspectionAdapter(connection, source_id=source_id)
