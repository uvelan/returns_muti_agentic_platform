"""`SourceInspectionPort` over one PostgreSQL database.

The fourth connector §5A requires and the first PostgreSQL code in the codebase.
Shaped to match `source_inspection_sqlserver.py` deliberately: the same eight
questions answered from the catalogue (`information_schema`, `pg_class`,
`pg_index`, `pg_constraint`) rather than from the data, the same `schema.table`
object naming, the same bounded read for the two methods that touch a user table.

`approximate_row_count` comes from `pg_class.reltuples`, which is maintained by
ANALYZE and is `-1` on a table that has never been analysed. That case is reported
as `None` rather than as `-1` or as `0`: a consumer ranking selectivity on a
fabricated zero would rank an unanalysed table as the most selective thing in the
schema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import psycopg

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
from return_platform.source_connectors.postgresql import PostgresConnectionSettings, run_read_query

__all__ = ["PostgresSourceInspectionAdapter", "build_postgres_source_inspection_adapter"]

_DEFAULT_SCHEMA = "public"

# Ceiling on rows a single inspection call may read, under whatever the scope
# layer already clamped to. Defence in depth, matching the SQL Server adapter.
_SAMPLE_CEILING = 100


class PostgresSourceInspectionAdapter:
    """Structurally satisfies `SourceInspectionPort` for exactly one source."""

    def __init__(self, connection: PostgresConnectionSettings, *, source_id: str) -> None:
        self._connection = connection
        self._source_id = source_id

    async def validate(self, *, source_id: str) -> SourceValidation:
        self._require_source(source_id)
        try:
            rows = await self._query("SELECT version() AS server_version", {})
        except psycopg.Error as exc:
            return SourceValidation(source_id=source_id, reachable=False, detail=str(exc))
        return SourceValidation(
            source_id=source_id,
            reachable=True,
            server_version=str(rows[0]["server_version"]) if rows else None,
        )

    async def list_sources(self) -> Sequence[str]:
        return (self._source_id,)

    async def list_objects(self, *, source_id: str) -> Sequence[SourceObjectRef]:
        self._require_source(source_id)
        rows = await self._query(
            """
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
            """,
            {},
        )
        return tuple(
            SourceObjectRef(
                source_id=source_id,
                object_name=f"{row['table_schema']}.{row['table_name']}",
                object_kind=(
                    ObjectKind.VIEW if str(row["table_type"]) == "VIEW" else ObjectKind.TABLE
                ),
            )
            for row in rows
        )

    async def describe_object(self, *, source_id: str, object_name: str) -> ObjectDescription:
        namespace, table = self._resolve(source_id, object_name)
        rows = await self._query(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %(schema)s AND table_name = %(table)s
            ORDER BY ordinal_position
            """,
            {"schema": namespace, "table": table},
        )
        kind_rows = await self._query(
            """
            SELECT table_type
            FROM information_schema.tables
            WHERE table_schema = %(schema)s AND table_name = %(table)s
            """,
            {"schema": namespace, "table": table},
        )
        is_view = bool(kind_rows) and str(kind_rows[0]["table_type"]) == "VIEW"
        return ObjectDescription(
            source_id=source_id,
            object_name=object_name,
            object_kind=ObjectKind.VIEW if is_view else ObjectKind.TABLE,
            fields=tuple(
                FieldDescription(
                    field_name=str(row["column_name"]),
                    declared_type=str(row["data_type"]),
                    nullable=str(row["is_nullable"]).upper() == "YES",
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
            declared_types={field.field_name: field.declared_type for field in description.fields},
        )

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        namespace, table = self._resolve(source_id, object_name)
        # `generate_subscripts` over `indkey` is what preserves the index's key
        # order: `pg_attribute` joined without it returns columns in attribute
        # order, which describes an access path the index does not offer.
        rows = await self._query(
            """
            SELECT ci.relname AS index_name, ix.indisunique AS is_unique,
                   ix.indisprimary AS is_primary, a.attname AS column_name,
                   k.ordinality AS key_ordinal
            FROM pg_index AS ix
            JOIN pg_class AS ci ON ci.oid = ix.indexrelid
            JOIN pg_class AS ct ON ct.oid = ix.indrelid
            JOIN pg_namespace AS n ON n.oid = ct.relnamespace
            JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
            JOIN pg_attribute AS a ON a.attrelid = ct.oid AND a.attnum = k.attnum
            WHERE n.nspname = %(schema)s AND ct.relname = %(table)s
            ORDER BY ci.relname, k.ordinality
            """,
            {"schema": namespace, "table": table},
        )
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
            predicate = "AND pn.nspname = %(schema)s AND pt.relname = %(table)s"
            params = {"schema": namespace, "table": table}
        rows = await self._query(
            f"""
            SELECT c.conname AS constraint_name,
                   pn.nspname AS parent_schema, pt.relname AS parent_table,
                   pa.attname AS parent_column,
                   rn.nspname AS referenced_schema, rt.relname AS referenced_table,
                   ra.attname AS referenced_column,
                   k.ordinality AS ordinal
            FROM pg_constraint AS c
            JOIN pg_class AS pt ON pt.oid = c.conrelid
            JOIN pg_namespace AS pn ON pn.oid = pt.relnamespace
            JOIN pg_class AS rt ON rt.oid = c.confrelid
            JOIN pg_namespace AS rn ON rn.oid = rt.relnamespace
            JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
            JOIN LATERAL unnest(c.confkey) WITH ORDINALITY AS f(attnum, ordinality)
              ON f.ordinality = k.ordinality
            JOIN pg_attribute AS pa ON pa.attrelid = pt.oid AND pa.attnum = k.attnum
            JOIN pg_attribute AS ra ON ra.attrelid = rt.oid AND ra.attnum = f.attnum
            WHERE c.contype = 'f'
              AND pn.nspname NOT IN ('pg_catalog', 'information_schema')
              {predicate}
            ORDER BY c.conname, k.ordinality
            """,
            params,
        )
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
        rows = await self._query(
            """
            SELECT c.reltuples AS approximate_rows
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = %(schema)s AND c.relname = %(table)s
            """,
            {"schema": namespace, "table": table},
        )
        if not rows or rows[0]["approximate_rows"] is None:
            return None
        estimate = int(rows[0]["approximate_rows"])
        # -1 is PostgreSQL for "never analysed", not for a row count.
        return None if estimate < 0 else estimate

    async def _read(
        self, namespace: str, table: str, limit: int, fields: Sequence[str] | None
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), _SAMPLE_CEILING))
        if fields is None:
            projection = "*"
        else:
            projection = ", ".join(
                f'"{validate_identifier(field, what="column")}"' for field in fields
            )
        return await self._query(
            f'SELECT {projection} FROM "{namespace}"."{table}" LIMIT %(limit)s',
            {"limit": bounded},
        )

    async def _query(self, query: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        return await run_read_query(self._connection, query, params)

    def _resolve(self, source_id: str, object_name: str) -> tuple[str, str]:
        self._require_source(source_id)
        return split_qualified_name(object_name, default_namespace=_DEFAULT_SCHEMA)

    def _require_source(self, source_id: str) -> None:
        if source_id != self._source_id:
            raise ValueError(
                f"this adapter serves {self._source_id!r}, not {source_id!r}; "
                "resolve the adapter published for that source."
            )


def build_postgres_source_inspection_adapter(
    connection: PostgresConnectionSettings, *, source_id: str
) -> SourceInspectionPort:
    """Typed factory -- the return annotation is what makes mypy prove port
    conformance, rather than a runtime isinstance that only checks method names."""
    return PostgresSourceInspectionAdapter(connection, source_id=source_id)
