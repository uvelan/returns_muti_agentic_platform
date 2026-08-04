"""Neo4j control-database create-missing adapter using an injected gateway."""

from __future__ import annotations

from typing import Protocol

from return_platform.dynamic_knowledge.internal_store.contracts import (
    CompatibilityStatus,
    InternalObjectDefinition,
    InternalStoreAdapter,
    ObjectInspection,
)
from return_platform.dynamic_knowledge.schema import ConnectorType, validate_graph_identifier


class Neo4jMetadataGateway(Protocol):
    async def label_exists(self, label: str) -> bool: ...
    async def observed_properties(self, label: str) -> set[str]: ...
    async def create_marker_node(self, label: str) -> None: ...
    async def constraint_exists(self, name: str) -> bool: ...
    async def execute_schema(self, statement: str) -> None: ...


class Neo4jInternalStoreAdapter(InternalStoreAdapter):
    connector_type = ConnectorType.NEO4J

    def __init__(self, gateway: Neo4jMetadataGateway) -> None:
        self._gateway = gateway

    async def inspect_object(self, definition: InternalObjectDefinition) -> ObjectInspection:
        if not await self._gateway.label_exists(definition.name):
            return ObjectInspection(name=definition.name, status=CompatibilityStatus.MISSING)
        observed = await self._gateway.observed_properties(definition.name)
        missing = tuple(field.name for field in definition.fields if field.required and field.name not in observed)
        return ObjectInspection(
            name=definition.name,
            status=CompatibilityStatus.INCOMPATIBLE if missing else CompatibilityStatus.COMPATIBLE,
            reasons=tuple(f"missing required property {field}" for field in missing),
        )

    async def create_object(self, definition: InternalObjectDefinition) -> None:
        validate_graph_identifier(definition.name)
        await self._gateway.create_marker_node(definition.name)

    async def ensure_indexes(self, definition: InternalObjectDefinition) -> tuple[str, ...]:
        created: list[str] = []
        validate_graph_identifier(definition.name)
        for index in definition.indexes:
            name = str(index["name"])
            validate_graph_identifier(name)
            if await self._gateway.constraint_exists(name):
                continue
            fields = tuple(str(item) for item in index["fields"])
            for field in fields:
                validate_graph_identifier(field)
            if len(fields) != 1:
                raise ValueError("Neo4j internal bootstrap currently requires one property per constraint")
            unique = bool(index.get("unique", False))
            property_name = fields[0]
            if unique:
                statement = (
                    f"CREATE CONSTRAINT `{name}` IF NOT EXISTS FOR (node:`{definition.name}`) "
                    f"REQUIRE node.`{property_name}` IS UNIQUE"
                )
            else:
                statement = (
                    f"CREATE INDEX `{name}` IF NOT EXISTS FOR (node:`{definition.name}`) "
                    f"ON (node.`{property_name}`)"
                )
            await self._gateway.execute_schema(statement)
            created.append(name)
        return tuple(created)
