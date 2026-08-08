"""Canonical read contracts: scan/cursor model, capabilities, targeted-read plans.

Moved from `dynamic_knowledge.on_demand_sync.contracts` (Phase 8 / Wave C1) --
that module's remaining types (`DynamicSourceRecord`, `GraphNodeMutation`, etc.)
are graph-*mutation* contracts specific to the dynamic-knowledge sync pipeline
and stay there; these are source-*read* contracts, dynamic_knowledge-independent.
`dynamic_knowledge.on_demand_sync.contracts` re-exports these names unchanged
so existing importers do not need to change.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectorKind(StrEnum):
    """Physical system a DatasetRef/connector addresses. Add PostgreSQL/Neo4j
    only when a real source connector implementation exists for them -- see
    this package's README for why none exists today."""

    MONGODB = "MONGODB"
    MSSQL = "MSSQL"


class SourceCursor(BaseModel):
    """An opaque, connector-owned position in one source's change stream.

    Ordering, encoding/decoding, and resume/boundary semantics belong to the
    connector that produced the cursor. Nothing outside the connector may
    decode or order ``encoded_value`` itself -- see CursorComparison and
    SourceScanConnector.compare_cursors.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor_type: str
    encoded_value: str


class CursorComparison(StrEnum):
    BEFORE = "BEFORE"
    EQUAL = "EQUAL"
    AFTER = "AFTER"


class RawSourceDocument(BaseModel):
    """One raw change event from a source, before any extraction/allowlisting.

    ``document`` is ``None`` for a DELETE whose source only provides a
    tombstone -- ``source_key_values`` is what lets a DELETE still be
    resolved to the correct composite key without a full document.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["UPSERT", "DELETE"]
    document: Mapping[str, Any] | None = None
    source_identity: str
    source_key_values: Mapping[str, Any] | None = None


class RawSourcePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    documents: tuple[RawSourceDocument, ...]
    next_cursor: SourceCursor | None = None
    high_watermark: SourceCursor | None = None
    observed_at: datetime


class SourceConnectorCapabilities(BaseModel):
    """What one connector can actually guarantee; not assumed uniformly true of every source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supports_high_watermark: bool
    supports_bounded_scan: bool
    supports_delta_replay: bool
    supports_delete_events: bool
    supports_stable_ordering: bool
    supports_snapshot_read: bool
    supports_partitioned_cursors: bool
    supports_cursor_comparison: bool
    supports_point_lookup: bool = False
    supports_bounded_sample: bool = False


class LogicalAnchorCondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str
    operator: str
    value: Any


class LogicalTargetedReadPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_asset_id: str
    entity_id: str
    conditions: tuple[LogicalAnchorCondition, ...]
    required_field_ids: tuple[str, ...]
    dependency_relationship_ids: tuple[str, ...] = ()
    maximum_rows: int = Field(default=100, ge=1, le=10_000)
    maximum_dependency_records: int = Field(default=500, ge=0, le=100_000)


class DatasetRef(BaseModel):
    """Neutral physical-location descriptor for one dataset/table/collection.

    Deliberately independent of any schema surface's own vocabulary
    (`dynamic_knowledge.schema.SourceAssetDefinition.object_ref`,
    `data_platform.schema_registry.DataAssetSchema`) -- each schema surface
    gets a small `to_dataset_ref()` conversion living in its own module, not
    here, so this package never depends on either schema flavor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    connector_kind: ConnectorKind
    database: str
    namespace: str | None = None
    name: str


class BoundedSamplePolicy(BaseModel):
    """Server-side enforced ceiling on a bounded sample read (e.g. an admin
    preview). Enforced inside the connector's sample functions themselves,
    not only at whatever HTTP layer happens to call them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rows: int = Field(default=100, ge=1, le=1_000)
