"""Questions the analyzer asks a human, and their answers.

Structurally similar to Order Discovery's CLARIFY pause but deliberately a
separate concept with its own persistence: an analyzer clarification outlives a
single reasoning run (an analyst may answer it a day later, after consulting a
data owner), so it is a durable record in its own right rather than state parked
inside a graph checkpoint.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.graph_schema_analyzer.domain.errors import InvalidSessionTransition

__all__ = ["Clarification", "ClarificationStatus"]


class ClarificationStatus(StrEnum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    WITHDRAWN = "WITHDRAWN"


class Clarification(BaseModel):
    """One open question against one analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clarification_id: str
    analysis_id: str
    question: str
    status: ClarificationStatus = ClarificationStatus.OPEN
    answer: str | None = None
    asked_at: datetime
    answered_at: datetime | None = None
    answered_by: str | None = None

    def answered(self, answer: str, *, by: str, occurred_at: datetime) -> Clarification:
        """Record a human answer. Answering a non-OPEN clarification is rejected:
        a late answer to a withdrawn question must not silently resurrect it, and
        re-answering an ANSWERED one would discard the answer already reasoned on."""
        if self.status is not ClarificationStatus.OPEN:
            raise InvalidSessionTransition(
                f"clarification {self.clarification_id} is {self.status}, not OPEN, so it "
                "cannot be answered."
            )
        return self.model_copy(
            update={
                "status": ClarificationStatus.ANSWERED,
                "answer": answer,
                "answered_at": occurred_at,
                "answered_by": by,
            }
        )

    def withdrawn(self, *, occurred_at: datetime) -> Clarification:
        if self.status is not ClarificationStatus.OPEN:
            raise InvalidSessionTransition(
                f"clarification {self.clarification_id} is {self.status}, not OPEN, so it "
                "cannot be withdrawn."
            )
        return self.model_copy(
            update={"status": ClarificationStatus.WITHDRAWN, "answered_at": occurred_at}
        )

    def to_document(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")
