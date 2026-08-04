from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.internal_store.bootstrap import (
    InternalSchemaIncompatible,
    InternalStoreBootstrapper,
)
from return_platform.dynamic_knowledge.internal_store.contracts import (
    CompatibilityStatus,
    InternalFieldDefinition,
    InternalObjectDefinition,
    InternalSchemaManifest,
    ObjectInspection,
)
from return_platform.dynamic_knowledge.schema import ConnectorType


class Adapter:
    connector_type = ConnectorType.MONGODB

    def __init__(self, status: CompatibilityStatus) -> None:
        self.status = status
        self.created: list[str] = []

    async def inspect_object(self, definition: InternalObjectDefinition) -> ObjectInspection:
        return ObjectInspection(
            name=definition.name,
            status=self.status,
            reasons=("required field mismatch",) if self.status is CompatibilityStatus.INCOMPATIBLE else (),
        )

    async def create_object(self, definition: InternalObjectDefinition) -> None:
        self.created.append(definition.name)

    async def ensure_indexes(self, definition: InternalObjectDefinition) -> tuple[str, ...]:
        return (f"{definition.name}_id_uq",)


def manifest() -> InternalSchemaManifest:
    return InternalSchemaManifest(
        manifest_version="1",
        connector_type=ConnectorType.MONGODB,
        objects=(
            InternalObjectDefinition(
                name="agent_turns",
                kind="COLLECTION",
                fields=(InternalFieldDefinition(name="id", data_type="STRING", nullable=False),),
                indexes=({"name": "agent_turns_id_uq", "fields": ["id"], "unique": True},),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_missing_internal_object_is_created() -> None:
    adapter = Adapter(CompatibilityStatus.MISSING)
    report = await InternalStoreBootstrapper(adapter).bootstrap(manifest())
    assert report.created_objects == ("agent_turns",)
    assert adapter.created == ["agent_turns"]


@pytest.mark.asyncio
async def test_compatible_internal_object_is_not_changed() -> None:
    adapter = Adapter(CompatibilityStatus.COMPATIBLE)
    report = await InternalStoreBootstrapper(adapter).bootstrap(manifest())
    assert report.existing_objects == ("agent_turns",)
    assert adapter.created == []


@pytest.mark.asyncio
async def test_incompatible_internal_object_fails_startup() -> None:
    adapter = Adapter(CompatibilityStatus.INCOMPATIBLE)
    with pytest.raises(InternalSchemaIncompatible):
        await InternalStoreBootstrapper(adapter).bootstrap(manifest())
