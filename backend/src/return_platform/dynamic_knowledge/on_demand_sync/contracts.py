"""Logical source-read and targeted synchronization contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SyncStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


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


class DynamicSourceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_asset_id: str
    entity_id: str
    natural_key: dict[str, Any]
    values: dict[str, Any]


class ProjectionReadScope(StrEnum):
    """Whether an extraction saw the complete parent document or only some of it.

    REPLACE_CHILD_SET reconciliation (dropping a child absent from a re-read
    parent) is only ever valid for COMPLETE_SOURCE_DOCUMENT reads -- a
    PARTIAL_TARGETED_READ (e.g. an on-demand anchor read) must never be used
    to infer that an unseen child was deleted.
    """

    COMPLETE_SOURCE_DOCUMENT = "COMPLETE_SOURCE_DOCUMENT"
    PARTIAL_TARGETED_READ = "PARTIAL_TARGETED_READ"


class DynamicRecordMutation(BaseModel):
    """One UPSERT or DELETE produced by SourceRecordExtractor from a RawSourcePage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["UPSERT", "DELETE"]
    record: DynamicSourceRecord | None = None
    entity_id: str
    projection_id: str
    source_asset_id: str
    source_identity: str
    source_version: str | None = None
    resolved_key: dict[str, Any]
    read_scope: ProjectionReadScope


class GraphNodeMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["UPSERT", "HARD_DELETE", "TOMBSTONE", "DETACH_ONLY"]
    projection_id: str
    entity_id: str
    key_values: dict[str, Any]
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphRelationshipMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["UPSERT", "DELETE"]
    relationship_id: str
    source_key_values: dict[str, Any]
    target_key_values: dict[str, Any]
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphMutationBatch(BaseModel):
    """The projector's output. Replay-identity fields (sync_run_id, chunk_id,
    schema_fingerprint) are added once the chunked, idempotent writer that
    actually needs them exists -- populating them here today would just be
    unused placeholders."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_mutations: tuple[GraphNodeMutation, ...]
    relationship_mutations: tuple[GraphRelationshipMutation, ...]


class SyncReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_request_id: str
    request_digest: str
    status: SyncStatus
    schema_version: str
    graph_generation_id: str
    source_rows_read: int = 0
    dependency_rows_read: int = 0
    nodes_written: int = 0
    relationships_written: int = 0
    error_code: str | None = None


class SyncReservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acquired: bool
    sync_request_id: str
    existing_receipt: SyncReceipt | None = None
