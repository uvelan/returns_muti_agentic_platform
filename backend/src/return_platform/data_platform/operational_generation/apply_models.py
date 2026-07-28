from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExecutionRunState(StrEnum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_BLOCKED = "ROLLBACK_BLOCKED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    EXPIRED = "EXPIRED"


class ExecutionRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    plan_id: UUID
    state: ExecutionRunState
    started_at: datetime
    updated_at: datetime
    error: str | None = None


class StepReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: UUID
    run_id: UUID
    step_index: int
    executed_at: datetime
    success: bool
    error: str | None = None
    target_channel: str
    operations_count: int
