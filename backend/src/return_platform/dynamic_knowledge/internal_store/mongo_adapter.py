"""MongoDB create-missing internal-store adapter using an injected metadata gateway."""

from __future__ import annotations

from typing import Protocol

from return_platform.dynamic_knowledge.internal_store.contracts import (
    CompatibilityStatus,
    InternalObjectDefinition,
    InternalStoreAdapter,
    ObjectInspection,
)
from return_platform.dynamic_knowledge.schema import ConnectorType


class MongoMetadataGateway(Protocol):
    async def collection_exists(self, name: str) -> bool: ...
    async def create_collection(self, name: str) -> None: ...
    async def existing_fields(self, name: str) -> set[str]: ...
    async def index_exists(self, collection: str, index_name: str) -> bool: ...
    async def create_index(
        self,
        collection: str,
        *,
        index_name: str,
        fields: tuple[str, ...],
        unique: bool,
    ) -> None: ...


class MongoInternalStoreAdapter(InternalStoreAdapter):
    connector_type = ConnectorType.MONGODB

    def __init__(self, gateway: MongoMetadataGateway) -> None:
        self._gateway = gateway

    async def inspect_object(self, definition: InternalObjectDefinition) -> ObjectInspection:
        if not await self._gateway.collection_exists(definition.name):
            return ObjectInspection(name=definition.name, status=CompatibilityStatus.MISSING)
        observed = await self._gateway.existing_fields(definition.name)
        missing = tuple(field.name for field in definition.fields if field.required and field.name not in observed)
        return ObjectInspection(
            name=definition.name,
            status=CompatibilityStatus.INCOMPATIBLE if missing else CompatibilityStatus.COMPATIBLE,
            reasons=tuple(f"missing required field {field}" for field in missing),
        )

    async def create_object(self, definition: InternalObjectDefinition) -> None:
        await self._gateway.create_collection(definition.name)

    async def ensure_indexes(self, definition: InternalObjectDefinition) -> tuple[str, ...]:
        created: list[str] = []
        for index in definition.indexes:
            name = str(index["name"])
            if await self._gateway.index_exists(definition.name, name):
                continue
            await self._gateway.create_index(
                definition.name,
                index_name=name,
                fields=tuple(str(item) for item in index["fields"]),
                unique=bool(index.get("unique", False)),
            )
            created.append(name)
        return tuple(created)
