"""Human sign-off on a validated schema.

An approval is bound to a specific `revision_id` and `validation_result_id`, not
just to a draft. Approving "the draft" would be meaningless the moment the draft
changes -- the record has to say which exact shape a named human accepted, or it
cannot answer the only question anyone ever asks it afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.graph_schema_analyzer.domain.errors import InvalidSessionTransition

__all__ = ["Approval", "ApprovalStatus"]


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    draft_id: str
    revision_id: str
    validation_result_id: str
    approver: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: datetime | None = None
    note: str | None = None

    def approved(self, *, by: str, occurred_at: datetime, note: str | None = None) -> Approval:
        return self._decided(ApprovalStatus.APPROVED, by=by, occurred_at=occurred_at, note=note)

    def rejected(self, *, by: str, occurred_at: datetime, note: str | None = None) -> Approval:
        return self._decided(ApprovalStatus.REJECTED, by=by, occurred_at=occurred_at, note=note)

    def _decided(
        self,
        status: ApprovalStatus,
        *,
        by: str,
        occurred_at: datetime,
        note: str | None,
    ) -> Approval:
        """A decision is final. Re-deciding an already-decided approval is
        refused rather than overwritten: the audit question is "what did this
        person decide", and a mutable answer cannot be audited."""
        if self.status is not ApprovalStatus.PENDING:
            raise InvalidSessionTransition(
                f"approval {self.approval_id} is already {self.status} and cannot be re-decided."
            )
        return self.model_copy(
            update={
                "status": status,
                "approver": by,
                "decided_at": occurred_at,
                "note": note,
            }
        )

    def to_document(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")
