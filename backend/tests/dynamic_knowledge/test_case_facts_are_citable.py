"""A fact the case records can be stated, cited, and checked.

Asked "what is the status" on 2026-09-10, the model wrote a GRAPH_FACT citing
`case_facts` -- which no query produced -- and the turn failed three times over
a fact the platform held all along. A CASE_FACT is that citation given a type
the guard can verify: against the case's own record, the way a GRAPH_FACT is
verified against a query's rows.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.dynamic_knowledge.knowledge.evidence import (
    CASE_FACTS_EVIDENCE_ID,
    EvidenceReference,
    ResponseStatement,
    StatementType,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.guards import HallucinationGuard


def _case_fact(path: tuple[str, ...], expected: object = None) -> ResponseStatement:
    return ResponseStatement(
        statement_id="status",
        statement_type=StatementType.CASE_FACT,
        text="The return is in transit.",
        evidence_refs=(
            EvidenceReference(
                query_execution_id=CASE_FACTS_EVIDENCE_ID,
                result_path=path,
                expected_value=expected,
            ),
        ),
    )


def _response(*statements: ResponseStatement) -> StructuredAgentResponse:
    return StructuredAgentResponse(
        status="COMPLETE", business_capability="return-status", statements=statements
    )


def test_a_case_fact_cites_case_facts_and_nothing_else() -> None:
    assert _case_fact(("fulfillment_status",)).evidence_refs[0].result_path == (
        "fulfillment_status",
    )
    with pytest.raises(ValidationError, match="CASE_FACT requires evidence"):
        ResponseStatement(
            statement_id="s", statement_type=StatementType.CASE_FACT, text="in transit"
        )
    with pytest.raises(ValidationError, match="cite query_execution_id 'case_facts'"):
        ResponseStatement(
            statement_id="s",
            statement_type=StatementType.CASE_FACT,
            text="in transit",
            evidence_refs=(EvidenceReference(query_execution_id="qe-1", result_path=("rows",)),),
        )


def test_a_graph_fact_may_not_cite_the_case() -> None:
    """The exact shape the model produced on 2026-09-10, refused at the contract
    with the name of the type it should have used."""
    with pytest.raises(ValidationError, match="is a CASE_FACT"):
        ResponseStatement(
            statement_id="s",
            statement_type=StatementType.GRAPH_FACT,
            text="status AWAITING_HANDOFF",
            evidence_refs=(
                EvidenceReference(
                    query_execution_id=CASE_FACTS_EVIDENCE_ID, result_path=("fulfillment_status",)
                ),
            ),
        )


def test_the_guard_checks_a_case_fact_against_the_case() -> None:
    guard = HallucinationGuard()
    facts = {"fulfillment_status": "IN_TRANSIT", "return_reference": "RMA-1"}

    ok = guard.validate(
        response=_response(_case_fact(("fulfillment_status",), "IN_TRANSIT")),
        evidence=(),
        graph_generation_id="gen-1",
        case_facts=facts,
    )
    assert ok.valid

    wrong_value = guard.validate(
        response=_response(_case_fact(("fulfillment_status",), "AWAITING_HANDOFF")),
        evidence=(),
        graph_generation_id="gen-1",
        case_facts=facts,
    )
    assert not wrong_value.valid
    assert "does not match the recorded value" in wrong_value.failures[0].reason

    not_recorded = guard.validate(
        response=_response(_case_fact(("carrier_eta",))),
        evidence=(),
        graph_generation_id="gen-1",
        case_facts=facts,
    )
    assert not not_recorded.valid
    assert "not recorded on the case" in not_recorded.failures[0].reason

    no_case = guard.validate(
        response=_response(_case_fact(("fulfillment_status",))),
        evidence=(),
        graph_generation_id="gen-1",
    )
    assert not no_case.valid
