"""Logical source-read and targeted synchronization contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SyncStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


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


class GraphWriteBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_rows: dict[str, tuple[dict[str, Any], ...]]
    relationship_rows: dict[str, tuple[dict[str, Any], ...]]


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
