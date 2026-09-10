"""Immutable evidence and structured response contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from return_platform.dynamic_knowledge.fingerprint import sha256_digest


class StatementType(StrEnum):
    GRAPH_FACT = "GRAPH_FACT"
    #: Something the case itself records -- its fulfilment status, the RMA,
    #: where it is going -- cited from `contextJson.case_facts` rather than from
    #: a query. Added 2026-09-10: asked "what is the status", the model wrote a
    #: GRAPH_FACT citing `case_facts`, which no query produced, and the turn
    #: failed three times over a fact the platform held all along. A case fact
    #: is verifiable, so it gets a statement type the guard can verify.
    CASE_FACT = "CASE_FACT"
    USER_PROVIDED_FACT = "USER_PROVIDED_FACT"
    REASONED_SUGGESTION = "REASONED_SUGGESTION"
    CLARIFICATION_QUESTION = "CLARIFICATION_QUESTION"


#: The `query_execution_id` a CASE_FACT cites. Not a query: the name of the
#: case-fact map in the context, so a citation reads ["fulfillment_status"]
#: against it the way a GRAPH_FACT's reads ["rows", "0", "sku"] against a result.
CASE_FACTS_EVIDENCE_ID = "case_facts"


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
    #: The two conditional requirements `validate_evidence_shape` enforces,
    #: stated where a model reads them.
    #:
    #: Both are conditional on *this field's value*, which the JSON Schema
    #: dialect these contracts are emitted in cannot express: Gemini's
    #: `responseSchema` is an OpenAPI 3.0 subset with no `if`/`then` or
    #: `dependentRequired`, and OpenAI's strict structured outputs reject both
    #: keywords as well. A `description` is the only carrier every provider
    #: forwards, so the rule travels as prose attached to the field that decides
    #: it -- the same treatment `AgentAction.action_type` gets for the payload
    #: contract it conditions.
    statement_type: StatementType = Field(
        description=(
            "GRAPH_FACT requires a non-empty evidence_refs citing query evidence and is "
            "rejected without one; CASE_FACT requires evidence_refs whose "
            "query_execution_id is 'case_facts' and whose result_path names a fact on "
            "contextJson.case_facts; USER_PROVIDED_FACT requires source_message_id. "
            "REASONED_SUGGESTION and CLARIFICATION_QUESTION require neither. "
            "State something neither the query results nor the case facts contain as a "
            "REASONED_SUGGESTION rather than as an uncited GRAPH_FACT."
        )
    )
    text: str
    evidence_refs: tuple[EvidenceReference, ...] = ()
    source_message_id: str | None = None

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> ResponseStatement:
        if self.statement_type is StatementType.GRAPH_FACT:
            if not self.evidence_refs:
                raise ValueError("GRAPH_FACT requires evidence references")
            if any(ref.query_execution_id == CASE_FACTS_EVIDENCE_ID for ref in self.evidence_refs):
                raise ValueError(
                    "a GRAPH_FACT cites query evidence; a fact read from case_facts is a CASE_FACT"
                )
        if self.statement_type is StatementType.CASE_FACT:
            if not self.evidence_refs:
                raise ValueError("CASE_FACT requires evidence references into case_facts")
            for ref in self.evidence_refs:
                if ref.query_execution_id != CASE_FACTS_EVIDENCE_ID or not ref.result_path:
                    raise ValueError(
                        "CASE_FACT evidence_refs cite query_execution_id 'case_facts' with a "
                        "result_path naming the fact"
                    )
        if self.statement_type is StatementType.USER_PROVIDED_FACT and not self.source_message_id:
            raise ValueError("USER_PROVIDED_FACT requires source_message_id")
        return self


#: Response statuses that declare the exchange finished and nothing outstanding.
#:
#: Kept as a set of strings rather than an enum because `status` is the model's
#: word and older releases spell it differently; what matters is only whether a
#: turn claimed to be done.
TERMINAL_STATUSES = frozenset({"COMPLETE", "DISCOVERY_COMPLETE"})


class StructuredAgentResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    business_capability: str
    statements: tuple[ResponseStatement, ...]
    suggestions: tuple[str, ...] = ()
    requested_input: str | None = None

    @model_validator(mode="after")
    def validate_open_question_is_not_complete(self) -> StructuredAgentResponse:
        """A turn that asks a question has not finished.

        `AgentAction` already refuses a CLARIFY whose `requested_input` is empty.
        Nothing refused the inverse, and the inverse is the one that loses an
        answer: a response may carry a `CLARIFICATION_QUESTION` -- or populate
        `requested_input` -- while declaring itself `COMPLETE`, and every
        downstream reader believes the declaration.

        The observed failure was a `CONFIRM_ORDER` that confirmed the order and
        asked "what is coming back off it, and what went wrong with it?" in the
        same breath, under `status: COMPLETE`. The platform took the status at
        its word: no clarification thread was opened, discovery closed, the case
        was raised, and policy evaluation ran against a return with no reason and
        no line chosen -- returning REQUIRED_FACT_UNKNOWN and failing safe to
        REVIEW_REQUIRED. The associate was left looking at a policy exception
        beside a question nobody had answered yet, and the evaluator was blamed
        for a decision it was never in a position to make.

        Asking and finishing are mutually exclusive, so this is a contract error
        rather than a judgement call about which of the two the model meant.
        """
        asks_a_question = any(
            statement.statement_type is StatementType.CLARIFICATION_QUESTION
            for statement in self.statements
        ) or bool((self.requested_input or "").strip())
        if asks_a_question and self.status.strip().upper() in TERMINAL_STATUSES:
            raise ValueError(
                f"status {self.status!r} declares the exchange finished, but the "
                "response still asks the associate a question"
            )
        return self


class EvidenceValidationFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statement_id: str
    reason: str


class EvidenceValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    failures: tuple[EvidenceValidationFailure, ...] = ()
