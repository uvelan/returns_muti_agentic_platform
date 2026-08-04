"""Immutable evidence and structured response contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from return_platform.dynamic_knowledge.fingerprint import sha256_digest


class StatementType(StrEnum):
    GRAPH_FACT = "GRAPH_FACT"
    USER_PROVIDED_FACT = "USER_PROVIDED_FACT"
    REASONED_SUGGESTION = "REASONED_SUGGESTION"
    CLARIFICATION_QUESTION = "CLARIFICATION_QUESTION"


class QueryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query_execution_id: str
    schema_version: str
    graph_generation_id: str
    logical_plan_checksum: str
    compiled_query_checksum: str
    result: Any
    result_checksum: str

    @classmethod
    def create(
        cls,
        *,
        query_execution_id: str,
        schema_version: str,
        graph_generation_id: str,
        logical_plan_checksum: str,
        compiled_query_checksum: str,
        result: Any,
    ) -> QueryEvidence:
        return cls(
            query_execution_id=query_execution_id,
            schema_version=schema_version,
            graph_generation_id=graph_generation_id,
            logical_plan_checksum=logical_plan_checksum,
            compiled_query_checksum=compiled_query_checksum,
            result=result,
            result_checksum=sha256_digest(result),
        )


class EvidenceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query_execution_id: str
    result_path: tuple[str, ...]
    expected_value: Any = None


class ResponseStatement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statement_id: str
    statement_type: StatementType
    text: str
    evidence_refs: tuple[EvidenceReference, ...] = ()
    source_message_id: str | None = None

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> ResponseStatement:
        if self.statement_type is StatementType.GRAPH_FACT and not self.evidence_refs:
            raise ValueError("GRAPH_FACT requires evidence references")
        if self.statement_type is StatementType.USER_PROVIDED_FACT and not self.source_message_id:
            raise ValueError("USER_PROVIDED_FACT requires source_message_id")
        return self


class StructuredAgentResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    business_capability: str
    statements: tuple[ResponseStatement, ...]
    suggestions: tuple[str, ...] = ()
    requested_input: str | None = None


class EvidenceValidationFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statement_id: str
    reason: str


class EvidenceValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    failures: tuple[EvidenceValidationFailure, ...] = ()
