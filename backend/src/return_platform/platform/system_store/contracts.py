"""System-store structure definitions and the adapter port.

A `StructureDefinition` is the bootstrap-time view of one
`configuration.domain.system_store.SystemStoreStructure` entry: its logical name (the
key business code resolves through `SystemStore.collection()`), its physical name (the
actual collection name), its declared schema version, its index specs, and whether it
is declared `encrypted`. Mongo collections are schemaless, so "inspect" only concerns
existence -- there is no field-level compatibility check the way
`dynamic_knowledge.internal_store` needs for typed SQL/Neo4j objects.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
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


def compute_structure_fingerprint(definition: StructureDefinition) -> str:
    """Fingerprint of a structure's *declared shape* (indexes, encryption) -- distinct
    from `schema_version`, which is a migration-count identifier. A fingerprint change
    with no version bump signals the manifest changed the structure's shape without
    going through a migration; index drift detection (`IndexDefinition`/`indexes_match`)
    is what actually acts on that, but the fingerprint is persisted alongside the
    version ledger entry so the discrepancy is at least visible and auditable."""
    payload = {
        "indexes": [dict(sorted(index.items())) for index in definition.indexes],
        "encrypted": definition.encrypted,
    }
    raw_json = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


def compute_manifest_fingerprint(structures: Sequence[StructureDefinition]) -> str:
    """Identity of an entire manifest's structures block (Slice 3R.8) -- the durable
    bootstrap-state document is keyed on this, not on a release ID or a bare "bootstrap"
    constant, so a manifest change is recognized as needing its own bootstrap run rather
    than incorrectly reusing a COMPLETE record left by a different set of structures."""
    payload = [
        {
            "logical_name": definition.logical_name,
            "physical_name": definition.physical_name,
            "schema_version": definition.schema_version,
            "fingerprint": compute_structure_fingerprint(definition),
        }
        for definition in sorted(structures, key=lambda definition: definition.logical_name)
    ]
    raw_json = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


class StructureIdentity(BaseModel):
    """What the schema-version ledger binds a recorded version to (design doc /
    Slice 3R.4). Logical name alone is insufficient (a structure can be repointed at a
    different physical collection); logical + physical name alone is also insufficient
    (a collection can be dropped and recreated under the same name). `physical_identity`
    is an opaque, provider-supplied identity for the actual physical object -- for
    MongoDB, its collection UUID -- that changes across a drop+recreate even when the
    name doesn't."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_name: str
    physical_name: str
    physical_identity: str
    structure_fingerprint: str


class StructureInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_name: str
    status: CompatibilityStatus
    physical_identity: str | None = None


class BootstrapStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class BootstrapState(BaseModel):
    """Durable, platform-owned record of one whole-manifest bootstrap attempt
    (Slice 3R.8), keyed by `manifest_fingerprint`. `COMPLETE` means the entire manifest
    finished -- structures, indexes, and migrations for every declared structure --
    never inferred from partial schema-version progress. No secrets or configuration
    payload; identity and lifecycle fields only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_fingerprint: str
    status: BootstrapStatus
    owner_instance_id: str
    lease_id: str
    fencing_token: int
    started_at: datetime
    completed_at: datetime | None = None
    failure_code: str | None = None
    failure_at: datetime | None = None


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

    async def ensure_indexes(self, definition: StructureDefinition) -> IndexEnsureResult: ...


class IndexDefinition:
    """Canonical, comparable index shape (Slice 3R.5). Covers exactly the attributes the
    canonical structure model (`StructureDefinition.indexes`) supports today: ordered
    key/direction pairs, `unique`, and `partial_filter_expression`. TTL/sparse/collation
    are not compared because the canonical model does not declare them yet -- adding
    support for those requires extending the typed model and config schema first, not
    silently comparing backend-only defaults."""

    __slots__ = ("keys", "name", "partial_filter_expression", "unique")

    def __init__(
        self,
        *,
        name: str,
        keys: tuple[tuple[str, int], ...],
        unique: bool = False,
        partial_filter_expression: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.keys = keys
        self.unique = unique
        self.partial_filter_expression = (
            dict(partial_filter_expression) if partial_filter_expression else None
        )

    @classmethod
    def from_declared(cls, spec: Mapping[str, Any]) -> IndexDefinition:
        fields = spec["fields"]
        keys = tuple((str(field), 1) for field in fields)
        return cls(
            name=str(spec["name"]),
            keys=keys,
            unique=bool(spec.get("unique", False)),
            partial_filter_expression=spec.get("partial_filter_expression"),
        )

    @classmethod
    def from_observed(cls, raw: Mapping[str, Any]) -> IndexDefinition:
        key_doc = raw.get("key", {})
        keys = tuple((str(field), int(direction)) for field, direction in key_doc.items())
        return cls(
            name=str(raw["name"]),
            keys=keys,
            unique=bool(raw.get("unique", False)),
            partial_filter_expression=raw.get("partialFilterExpression"),
        )

    def matches(self, other: IndexDefinition) -> bool:
        return (
            self.keys == other.keys
            and self.unique == other.unique
            and (self.partial_filter_expression or None)
            == (other.partial_filter_expression or None)
        )


class IndexDriftReport(BaseModel):
    """A same-named index exists but its canonical definition differs from what the
    manifest declares. Never auto-repaired -- resolution is FAIL or WARN, per
    `SystemStoreConfig.fail_closed_on_drift`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_name: str
    index_name: str


class IndexEnsureResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    created: tuple[str, ...] = ()
    drifted: tuple[IndexDriftReport, ...] = ()
