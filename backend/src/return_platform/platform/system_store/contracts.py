"""System-store structure definitions and the adapter port.

A `StructureDefinition` is the bootstrap-time view of one
`configuration.domain.system_store.SystemStoreStructure` entry: its logical name (the
key business code resolves through `SystemStore.collection()`), its physical name (the
actual collection name), its declared schema version, its index specs, and whether it
is declared `encrypted`. Mongo collections are schemaless, so "inspect" only concerns
existence — there is no field-level compatibility check the way
`dynamic_knowledge.internal_store` needs for typed SQL/Neo4j objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class CompatibilityStatus(StrEnum):
    MISSING = "MISSING"
    PRESENT = "PRESENT"


class StructureDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_name: str
    physical_name: str
    schema_version: int
    encrypted: bool = False
    indexes: tuple[Mapping[str, Any], ...] = ()


class StructureInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_name: str
    status: CompatibilityStatus


class SystemStoreBootstrapReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    created_structures: tuple[str, ...]
    existing_structures: tuple[str, ...]
    created_indexes: tuple[str, ...]
    migrated_structures: tuple[str, ...]


class SystemStoreAdapter(Protocol):
    """Provider-specific structure lifecycle. The canonical provider is MongoDB."""

    async def inspect_structure(self, definition: StructureDefinition) -> StructureInspection: ...

    async def create_structure(self, definition: StructureDefinition) -> None: ...

    async def ensure_indexes(self, definition: StructureDefinition) -> tuple[str, ...]: ...
