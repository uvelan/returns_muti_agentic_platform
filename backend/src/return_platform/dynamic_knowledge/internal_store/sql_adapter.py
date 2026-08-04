"""Portable MSSQL/PostgreSQL internal-store adapter using an injected executor."""

from __future__ import annotations

from typing import Protocol

from return_platform.dynamic_knowledge.internal_store.contracts import (
    CompatibilityStatus,
    InternalObjectDefinition,
    InternalStoreAdapter,
    ObjectInspection,
)
from return_platform.dynamic_knowledge.schema import ConnectorType, validate_graph_identifier


class SqlMetadataExecutor(Protocol):
    async def inspect_table(self, table_name: str) -> dict[str, tuple[str, bool]] | None: ...
    async def execute_ddl(self, statement: str) -> None: ...
    async def index_exists(self, table_name: str, index_name: str) -> bool: ...


class SqlInternalStoreAdapter(InternalStoreAdapter):
    """Create-only SQL bootstrap for MSSQL and PostgreSQL."""

    def __init__(self, *, connector_type: ConnectorType, executor: SqlMetadataExecutor) -> None:
        if connector_type not in {ConnectorType.MSSQL, ConnectorType.POSTGRESQL}:
            raise ValueError("SqlInternalStoreAdapter supports MSSQL and POSTGRESQL only")
        self.connector_type = connector_type
        self._executor = executor

    async def inspect_object(self, definition: InternalObjectDefinition) -> ObjectInspection:
        observed = await self._executor.inspect_table(definition.name)
        if observed is None:
            return ObjectInspection(name=definition.name, status=CompatibilityStatus.MISSING)
        reasons: list[str] = []
        for field in definition.fields:
            actual = observed.get(field.name)
            if actual is None and field.required:
                reasons.append(f"missing required field {field.name}")
                continue
            if actual is None:
                continue
            actual_type, actual_nullable = actual
            if actual_type.upper() != field.data_type.upper():
                reasons.append(
                    f"field {field.name} type {actual_type} does not satisfy {field.data_type}"
                )
            if not field.nullable and actual_nullable:
                reasons.append(f"field {field.name} must be non-nullable")
        return ObjectInspection(
            name=definition.name,
            status=CompatibilityStatus.INCOMPATIBLE if reasons else CompatibilityStatus.COMPATIBLE,
            reasons=tuple(reasons),
        )

    async def create_object(self, definition: InternalObjectDefinition) -> None:
        table = self._identifier(definition.name)
        field_sql = []
        for field in definition.fields:
            nullability = "NULL" if field.nullable else "NOT NULL"
            field_sql.append(f"{self._identifier(field.name)} {field.data_type} {nullability}")
        statement = f"CREATE TABLE {table} ({', '.join(field_sql)})"
        await self._executor.execute_ddl(statement)

    async def ensure_indexes(self, definition: InternalObjectDefinition) -> tuple[str, ...]:
        created: list[str] = []
        for index in definition.indexes:
            name = str(index["name"])
            fields = tuple(str(item) for item in index["fields"])
            if await self._executor.index_exists(definition.name, name):
                continue
            unique = "UNIQUE " if bool(index.get("unique", False)) else ""
            statement = (
                f"CREATE {unique}INDEX {self._identifier(name)} ON {self._identifier(definition.name)} "
                f"({', '.join(self._identifier(field) for field in fields)})"
            )
            await self._executor.execute_ddl(statement)
            created.append(name)
        return tuple(created)

    @staticmethod
    def _identifier(value: str) -> str:
        validate_graph_identifier(value)
        return f'"{value}"'
