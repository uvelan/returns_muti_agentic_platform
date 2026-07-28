from collections.abc import Mapping
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OperationType(StrEnum):
    INSERT = "INSERT"
    DOMAIN_COMMAND = "DOMAIN_COMMAND"
    GRAPH_SYNC_REQUEST = "GRAPH_SYNC_REQUEST"


class RollbackFeasibility(StrEnum):
    SAFE = "SAFE"
    BLOCKED = "BLOCKED"
    COMPENSATION_REQUIRED = "COMPENSATION_REQUIRED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    type: OperationType
    asset_id: str
    payload: Mapping[str, Any]
    target_channel: str
    dependencies: tuple[str, ...]


class TransactionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str
    target_channel: str
    operations: tuple[Operation, ...]


class SagaStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_index: int
    transaction_groups: tuple[TransactionGroup, ...]
    rollback_feasibility: RollbackFeasibility


class PlanImpact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_operations: int
    inserts: int
    domain_commands: int
    graph_sync_requests: int
    affected_channels: tuple[str, ...]


class OperationalWritePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: UUID
    proposal_checksum: str
    schema_release_id: str
    schema_checksum: str
    idempotency_key: str
    saga_steps: tuple[SagaStep, ...]
    impact: PlanImpact
    plan_checksum: str
