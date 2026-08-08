"""Graph-mutation contracts for dynamic-knowledge on-demand/targeted synchronization.

Source-*read* contracts (SourceCursor, CursorComparison, RawSourceDocument,
RawSourcePage, SourceConnectorCapabilities, LogicalAnchorCondition,
LogicalTargetedReadPlan) moved to `source_connectors.contracts` (Phase 8 /
Wave C1) -- that package is dynamic_knowledge-independent, consumed by
`data_platform`/`data_console`/`v2` too. Re-exported here unchanged so
existing importers in this package don't need to change; the types below
(DynamicSourceRecord, GraphNodeMutation, etc.) are graph-*mutation* concerns
specific to this sync pipeline and stay defined here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from return_platform.source_connectors.contracts import (
    CursorComparison,
    LogicalAnchorCondition,
    LogicalTargetedReadPlan,
    RawSourceDocument,
    RawSourcePage,
    SourceConnectorCapabilities,
    SourceCursor,
)

__all__ = [
    "CursorComparison",
    "DynamicRecordMutation",
    "DynamicSourceRecord",
    "GraphMutationBatch",
    "GraphNodeMutation",
    "GraphRelationshipMutation",
    "LogicalAnchorCondition",
    "LogicalTargetedReadPlan",
    "ProjectionReadScope",
    "RawSourceDocument",
    "RawSourcePage",
    "SourceConnectorCapabilities",
    "SourceCursor",
    "SyncReceipt",
    "SyncReservation",
    "SyncStatus",
]


class SyncStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


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
