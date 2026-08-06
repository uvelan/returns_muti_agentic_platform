from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.knowledge.evidence import (
    EvidenceReference,
    QueryEvidence,
    ResponseStatement,
    StatementType,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.guards import (
    AnchorValue,
    GuardContext,
    GuardRejected,
    HallucinationGuard,
    PrincipalContext,
    StrongAnchorGuard,
    StrongAnchorRequest,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


def context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies["agent_a"],
        principal=PrincipalContext(
            principal_id="p1", tenant_id="t1", roles=frozenset({"associate"})
        ),
    )


def test_strong_anchor_accepts_configured_allowed_field(active_schema: ActiveSchema) -> None:
    normalized = StrongAnchorGuard().validate(
        context(active_schema),
        StrongAnchorRequest(
            entity_id="entity_a",
            strong_anchor_id="exact_id",
            anchors=(
                AnchorValue(
                    field_id="id", operator="EXACT", value="A-100", value_origin="USER_MESSAGE"
                ),
            ),
        ),
    )
    assert normalized == {"id": "A-100"}


def test_strong_anchor_rejects_unconfigured_field(active_schema: ActiveSchema) -> None:
    with pytest.raises(GuardRejected) as error:
        StrongAnchorGuard().validate(
            context(active_schema),
            StrongAnchorRequest(
                entity_id="entity_a",
                strong_anchor_id="exact_id",
                anchors=(
                    AnchorValue(
                        field_id="name", operator="EXACT", value="x", value_origin="USER_MESSAGE"
                    ),
                ),
            ),
        )
    assert error.value.code == "REJECT_INVALID_SCHEMA_REFERENCE"


def test_hallucination_guard_rejects_wrong_claim_value() -> None:
    evidence = QueryEvidence.create(
        query_execution_id="q1",
        schema_version="v1",
        graph_generation_id="g1",
        logical_plan_checksum="a",
        compiled_query_checksum="b",
        result={"total": 3},
    )
    response = StructuredAgentResponse(
        status="AWAITING_ASSOCIATE_INPUT",
        business_capability="order-discovery",
        statements=(
            ResponseStatement(
                statement_id="s1",
                statement_type=StatementType.GRAPH_FACT,
                text="Five records were found.",
                evidence_refs=(
                    EvidenceReference(
                        query_execution_id="q1", result_path=("total",), expected_value=5
                    ),
                ),
            ),
        ),
    )
    result = HallucinationGuard().validate(
        response=response, evidence=(evidence,), graph_generation_id="g1"
    )
    assert result.valid is False
    reason = result.failures[0].reason
    assert "claimed value 5" in reason
    assert "actual evidence value 3" in reason


def test_hallucination_guard_names_the_bad_path_so_correction_can_target_it() -> None:
    """A generic 'evidence path is invalid' message gives a correction attempt
    nothing to fix; it must name the actual path so a retry can address it."""
    evidence = QueryEvidence.create(
        query_execution_id="q1",
        schema_version="v1",
        graph_generation_id="g1",
        logical_plan_checksum="a",
        compiled_query_checksum="b",
        result={"candidates": [{"customer_name": "Maya Foster"}]},
    )
    response = StructuredAgentResponse(
        status="AWAITING_ASSOCIATE_INPUT",
        business_capability="order-discovery",
        statements=(
            ResponseStatement(
                statement_id="s1",
                statement_type=StatementType.GRAPH_FACT,
                text="A sixth candidate exists.",
                evidence_refs=(
                    EvidenceReference(
                        query_execution_id="q1",
                        result_path=("candidates", "5", "customer_name"),
                        expected_value=None,
                    ),
                ),
            ),
        ),
    )
    result = HallucinationGuard().validate(
        response=response, evidence=(evidence,), graph_generation_id="g1"
    )
    assert result.valid is False
    reason = result.failures[0].reason
    assert "['candidates', '5', 'customer_name']" in reason
    assert "q1" in reason


def test_field_permission_empty_set_denies_access(active_schema: ActiveSchema) -> None:
    from return_platform.dynamic_knowledge.knowledge.guards import SchemaQueryGuard
    from return_platform.dynamic_knowledge.knowledge.query_plan import (
        LogicalQueryPlan,
        QueryOperation,
    )

    with pytest.raises(GuardRejected) as error:
        SchemaQueryGuard().validate(
            context(active_schema),
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="entity_a",
                fields=("changed_at",),
            ),
        )
    assert error.value.code == "REJECT_UNAUTHORIZED_FIELD"


def test_response_safety_guard_rejects_raw_cypher(active_schema: ActiveSchema) -> None:
    from return_platform.dynamic_knowledge.knowledge.guards import ResponseSafetyGuard

    response = StructuredAgentResponse(
        status="AWAITING_ASSOCIATE_INPUT",
        business_capability="order-discovery",
        statements=(
            ResponseStatement(
                statement_id="s1",
                statement_type=StatementType.CLARIFICATION_QUESTION,
                text="```cypher\nMATCH (n) DETACH DELETE n\n```",
            ),
        ),
    )
    with pytest.raises(GuardRejected) as error:
        ResponseSafetyGuard().validate(
            response,
            allowed_capabilities=active_schema.agent_policies[
                "agent_a"
            ].allowed_business_capabilities,
        )
    assert error.value.code == "ORDER_AGENT_RESPONSE_VALIDATION_FAILED"
