"""Internal-store manifests and adapter contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from return_platform.dynamic_knowledge.schema import ConnectorType


class CompatibilityStatus(StrEnum):
    MISSING = "MISSING"
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"


class InternalFieldDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    data_type: str
    required: bool = True
    nullable: bool = True


class InternalObjectDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: str
    fields: tuple[InternalFieldDefinition, ...]
    indexes: tuple[dict[str, Any], ...] = ()


class InternalSchemaManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_version: str
    connector_type: ConnectorType
    objects: tuple[InternalObjectDefinition, ...]


class ObjectInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: CompatibilityStatus
    reasons: tuple[str, ...] = ()


class BootstrapReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connector_type: ConnectorType
    created_objects: tuple[str, ...]
    existing_objects: tuple[str, ...]
    created_indexes: tuple[str, ...]


class InternalStoreAdapter(Protocol):
    connector_type: ConnectorType

    async def inspect_object(self, definition: InternalObjectDefinition) -> ObjectInspection: ...
    async def create_object(self, definition: InternalObjectDefinition) -> None: ...
    async def ensure_indexes(self, definition: InternalObjectDefinition) -> tuple[str, ...]: ...
