from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from return_platform.data_platform.operational_generation.models import (
    OperationalGenerationProposal,
)
from return_platform.data_platform.operational_generation.write_models import OperationalWritePlan


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    plan_id: UUID
    proposal_checksum: str
    plan_checksum: str
    schema_release_id: str
    target_environment: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime


def create_approval(
    plan: OperationalWritePlan,
    proposal: OperationalGenerationProposal,
    actor_id: str,
    target_environment: str,
    generator_actor_id: str | None = None,
    enforce_separation: bool = True,
) -> ApprovalRecord:
    if enforce_separation and generator_actor_id and generator_actor_id == actor_id:
        raise HTTPException(status_code=403, detail="Self-approval is denied by policy")

    return ApprovalRecord(
        approval_id=uuid4(),
        plan_id=plan.plan_id,
        proposal_checksum=proposal.proposal_checksum,
        plan_checksum=plan.plan_checksum,
        schema_release_id=plan.schema_release_id,
        target_environment=target_environment,
        approved_by=actor_id,
        approved_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def verify_approval(
    approval: ApprovalRecord,
    plan: OperationalWritePlan,
    proposal: OperationalGenerationProposal,
    target_environment: str,
) -> None:
    if approval.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Approval has expired")

    if approval.proposal_checksum != proposal.proposal_checksum:
        raise HTTPException(status_code=400, detail="Proposal has been edited since approval")

    if approval.plan_checksum != plan.plan_checksum:
        raise HTTPException(status_code=400, detail="Plan has been replanned since approval")

    if approval.schema_release_id != plan.schema_release_id:
        raise HTTPException(status_code=400, detail="Schema release mismatch")

    if approval.target_environment != target_environment:
        raise HTTPException(status_code=400, detail="Target environment mismatch")
