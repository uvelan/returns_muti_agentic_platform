"""Validation findings and the named checks a draft must pass.

**Count discrepancy in the design doc, resolved in favour of the names.**
Section 10.4 says "Validation checks (all 13 must pass before approval)" and then
enumerates *fourteen*: source exists; dataset exists; field exists; type
compatibility; identifiers available; relationships resolvable; cardinality
plausible; transformation supported; search anchors viable; Cypher compiles;
query safety passes; graph index definition valid; graph constraint valid; sync
projection executable. `ValidationCheck` implements all fourteen -- dropping one
to make the count match would be choosing a prose number over an explicitly
named safety check. Flagged in the execution ledger.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "REQUIRED_CHECKS",
    "Severity",
    "ValidationCheck",
    "ValidationFinding",
    "ValidationResult",
]


class ValidationCheck(StrEnum):
    SOURCE_EXISTS = "SOURCE_EXISTS"
    DATASET_EXISTS = "DATASET_EXISTS"
    FIELD_EXISTS = "FIELD_EXISTS"
    TYPE_COMPATIBILITY = "TYPE_COMPATIBILITY"
    IDENTIFIERS_AVAILABLE = "IDENTIFIERS_AVAILABLE"
    RELATIONSHIPS_RESOLVABLE = "RELATIONSHIPS_RESOLVABLE"
    CARDINALITY_PLAUSIBLE = "CARDINALITY_PLAUSIBLE"
    TRANSFORMATION_SUPPORTED = "TRANSFORMATION_SUPPORTED"
    SEARCH_ANCHORS_VIABLE = "SEARCH_ANCHORS_VIABLE"
    CYPHER_COMPILES = "CYPHER_COMPILES"
    QUERY_SAFETY_PASSES = "QUERY_SAFETY_PASSES"
    GRAPH_INDEX_DEFINITION_VALID = "GRAPH_INDEX_DEFINITION_VALID"
    GRAPH_CONSTRAINT_VALID = "GRAPH_CONSTRAINT_VALID"
    SYNC_PROJECTION_EXECUTABLE = "SYNC_PROJECTION_EXECUTABLE"


# Every check must run and pass before approval. A check that cannot be
# evaluated counts as a failure, never a skip: "we could not tell" and "it is
# fine" are different answers, and only one of them is safe to build on.
REQUIRED_CHECKS: frozenset[ValidationCheck] = frozenset(ValidationCheck)


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ValidationFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check: ValidationCheck
    severity: Severity
    element: str
    message: str


class ValidationResult(BaseModel):
    """One validation pass over one draft revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str
    draft_id: str
    revision_id: str
    findings: tuple[ValidationFinding, ...]
    checks_run: frozenset[ValidationCheck]
    validated_at: datetime

    @property
    def errors(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def passed(self) -> bool:
        """Passing requires *both* no errors and every required check having
        actually run. A result missing a check is not a pass with a gap; it is
        an incomplete pass, and approving on it would skip a safety check
        silently."""
        return not self.errors and REQUIRED_CHECKS <= self.checks_run

    @property
    def missing_checks(self) -> frozenset[ValidationCheck]:
        return REQUIRED_CHECKS - self.checks_run

    def to_document(self) -> Mapping[str, Any]:
        payload = dict(self.model_dump(mode="json"))
        # frozenset is not JSON-serialisable in a stable order; store a sorted list.
        payload["checks_run"] = sorted(check.value for check in self.checks_run)
        return payload

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> ValidationResult:
        payload = dict(document)
        raw: Sequence[str] = payload.get("checks_run", ())
        payload["checks_run"] = frozenset(ValidationCheck(value) for value in raw)
        return cls.model_validate(payload)
