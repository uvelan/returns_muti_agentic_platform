"""Deterministic tests for canonical operational evidence contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from return_platform.canonical.operations import (
    AgentDecision,
    AuditEvent,
    ContextSnapshot,
    GraphProjectionEvidence,
    GraphProjectionStatus,
    GraphSyncRun,
    ReturnSession,
    WorkflowStage,
)

_SESSION_ID = UUID("3e3d274b-229f-4f46-a7d1-f5d94bd9b53d")
_DECISION_ID = UUID("49fa4eca-2757-4367-bdec-1fd978aec481")
_AUDIT_ID = UUID("e90a1a12-0a16-48b4-acf0-3184128c0a6d")
_CORRELATION_ID = UUID("894231ea-f972-4a26-8944-420e3abcbd67")
_SYNC_RUN_ID = UUID("4986927c-5e22-4fc9-a65b-53e58ea1f5b7")
_EVIDENCE_ID = UUID("d069055a-e3ba-4ea4-ab1a-7af9a070dad2")
_DIGEST = "e" * 64
_CREATED_AT = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
_UPDATED_AT = datetime(2026, 7, 20, 14, 5, tzinfo=UTC)
_COMPLETED_AT = datetime(2026, 7, 20, 14, 10, tzinfo=UTC)


def _session_payload() -> dict[str, object]:
    return {
        "session_id": _SESSION_ID,
        "current_stage": WorkflowStage.ORDER_DISCOVERY,
        "status": "RUNNING",
        "intake_context": ContextSnapshot.from_mapping(
            schema_version="intake-v1",
            payload={"channel": "ASSOCIATE", "attempt": 1},
        ),
        "discovery_context": None,
        "return_request_context": None,
        "fulfillment_tracking_context": None,
        "bay_staging_context": None,
        "learning_feedback_context": None,
        "workflow_id": "return-session-3e3d274b",
        "workflow_run_id": "run-1",
        "configuration_versions": (
            {"component": "workflow", "version": "workflow-v1"},
            {"component": "policy", "version": "policy-v3"},
        ),
        "created_at": _CREATED_AT,
        "updated_at": _UPDATED_AT,
        "completed_at": None,
    }


def _decision_payload() -> dict[str, object]:
    return {
        "decision_id": _DECISION_ID,
        "session_id": _SESSION_ID,
        "agent_name": "eligibility-agent",
        "stage": WorkflowStage.ELIGIBILITY_EVALUATION,
        "decision_type": "RETURN_ELIGIBILITY",
        "decision": "APPROVE",
        "explanation": "Return is within the configured window.",
        "confidence": Decimal("0.9500000"),
        "evidence_references": ("order:TDS:202:SO-77", "policy:return-v3"),
        "model_provider": "GOOGLE",
        "model_name": "model-a",
        "configuration_version": "agent-routing-v1",
        "created_at": _UPDATED_AT,
        "reviewed_by": None,
    }


def _audit_payload() -> dict[str, object]:
    return {
        "audit_event_id": _AUDIT_ID,
        "session_id": _SESSION_ID,
        "correlation_id": _CORRELATION_ID,
        "actor_type": "AGENT",
        "actor_id": "eligibility-agent",
        "operation": "EVALUATE_ELIGIBILITY",
        "entity_type": "ReturnSession",
        "entity_key": "SESSION:3e3d274b",
        "before_summary": "Stage ORDER_DISCOVERY",
        "after_summary": "Stage ELIGIBILITY_EVALUATION",
        "outcome": "SUCCESS",
        "safe_error_code": None,
        "occurred_at": _UPDATED_AT,
        "evidence_references": ("decision:49fa4eca",),
    }


def _sync_run_payload() -> dict[str, object]:
    return {
        "sync_run_id": _SYNC_RUN_ID,
        "pipeline_id": "graph-sync-v1",
        "mapping_version": "mapping-v1",
        "configuration_digest": _DIGEST,
        "started_at": _CREATED_AT,
        "completed_at": _COMPLETED_AT,
        "status": "COMPLETED",
        "source_assets": ("TDS.salesInv", "shipment.trans"),
        "records_read": 10,
        "nodes_created": 4,
        "nodes_updated": 2,
        "relationships_created": 5,
        "relationships_updated": 1,
        "records_rejected": 0,
        "validation_results": (
            {
                "validation_code": "NO_DUPLICATE_KEYS",
                "passed": True,
                "safe_message": "No duplicate canonical keys were observed.",
            },
        ),
        "safe_errors": (),
    }


def _projection_payload() -> dict[str, object]:
    return {
        "evidence_id": _EVIDENCE_ID,
        "sync_run_id": _SYNC_RUN_ID,
        "source_asset": "TDS.salesInv",
        "source_record_id": "202*SO-77",
        "canonical_entity_type": "SalesOrder",
        "canonical_entity_key": "TDS:202:SO-77:2026-07-20",
        "graph_label": "SalesOrder",
        "graph_key": "TDS:202:SO-77:2026-07-20",
        "mapping_version": "mapping-v1",
        "projection_status": GraphProjectionStatus.PROJECTED,
        "rejection_reason": None,
        "projected_at": _COMPLETED_AT,
    }


def test_return_session_accepts_code_owned_stage_and_version_bindings() -> None:
    session = ReturnSession.model_validate(_session_payload())

    assert session.current_stage.value == "ORDER_DISCOVERY"
    assert len(session.configuration_versions) == 2


def test_return_session_accepts_completed_stage_with_timestamp() -> None:
    payload = _session_payload()
    payload["current_stage"] = WorkflowStage.COMPLETED
    payload["status"] = "COMPLETED"
    payload["completed_at"] = _COMPLETED_AT

    session = ReturnSession.model_validate(payload)

    assert session.completed_at == _COMPLETED_AT


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("current_stage", "UNKNOWN", "is_instance_of"),
        (
            "updated_at",
            datetime(2026, 7, 20, 13, 59, tzinfo=UTC),
            "return_session_updated_at_invalid",
        ),
    ],
)
def test_return_session_rejects_invalid_stage_or_timeline(
    field: str,
    value: object,
    error_type: str,
) -> None:
    payload = _session_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        ReturnSession.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


@pytest.mark.parametrize(
    ("workflow_id", "workflow_run_id"),
    [(None, "run-1"), ("workflow-1", None)],
)
def test_return_session_rejects_partial_workflow_reference(
    workflow_id: str | None,
    workflow_run_id: str | None,
) -> None:
    payload = _session_payload()
    payload["workflow_id"] = workflow_id
    payload["workflow_run_id"] = workflow_run_id

    with pytest.raises(ValidationError) as exc_info:
        ReturnSession.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_session_workflow_reference_pair_invalid"


def test_return_session_requires_completed_at_for_completed_stage() -> None:
    payload = _session_payload()
    payload["current_stage"] = WorkflowStage.COMPLETED

    with pytest.raises(ValidationError) as exc_info:
        ReturnSession.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_session_completed_at_required"


def test_return_session_rejects_completed_at_before_completed_stage() -> None:
    payload = _session_payload()
    payload["completed_at"] = _COMPLETED_AT

    with pytest.raises(ValidationError) as exc_info:
        ReturnSession.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_session_completed_stage_required"


def test_return_session_rejects_completed_at_before_updated_at() -> None:
    payload = _session_payload()
    payload["current_stage"] = WorkflowStage.COMPLETED
    payload["completed_at"] = datetime(2026, 7, 20, 14, 4, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        ReturnSession.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_session_completed_at_invalid"


def test_return_session_rejects_duplicate_configuration_component() -> None:
    payload = _session_payload()
    payload["configuration_versions"] = (
        {"component": "workflow", "version": "workflow-v1"},
        {"component": "workflow", "version": "workflow-v2"},
    )

    with pytest.raises(ValidationError) as exc_info:
        ReturnSession.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_session_duplicate_configuration_component"


def test_context_snapshot_is_deterministic_and_digest_bound() -> None:
    first = ContextSnapshot.from_mapping(
        schema_version="intake-v1",
        payload={"z": 2, "a": [1, True, None]},
    )
    second = ContextSnapshot.from_mapping(
        schema_version="intake-v1",
        payload={"a": [1, True, None], "z": 2},
    )

    assert first.payload_json == '{"a":[1,true,null],"z":2}'
    assert first.payload_digest == second.payload_digest


@pytest.mark.parametrize(
    ("payload_json", "digest", "error_type"),
    [
        ('{"b":2, "a":1}', "0" * 64, "context_snapshot_json_not_canonical"),
        ('{"a":1,"a":2}', "0" * 64, "context_snapshot_json_invalid"),
        ("[1,2]", "0" * 64, "context_snapshot_root_invalid"),
        ('{"a":NaN}', "0" * 64, "context_snapshot_json_invalid"),
        ('{"a":1}', "0" * 64, "context_snapshot_digest_mismatch"),
    ],
)
def test_context_snapshot_rejects_ambiguous_or_tampered_payload(
    payload_json: str,
    digest: str,
    error_type: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ContextSnapshot.model_validate(
            {
                "schema_version": "intake-v1",
                "payload_json": payload_json,
                "payload_digest": digest,
            },
        )

    assert exc_info.value.errors()[0]["type"] == error_type


def test_return_session_requires_at_least_one_configuration_version() -> None:
    payload = _session_payload()
    payload["configuration_versions"] = ()

    with pytest.raises(ValidationError) as exc_info:
        ReturnSession.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "too_short"


def test_return_session_rejects_string_uuid_coercion() -> None:
    payload = _session_payload()
    payload["session_id"] = str(_SESSION_ID)

    with pytest.raises(ValidationError) as exc_info:
        ReturnSession.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_agent_decision_accepts_model_and_evidence() -> None:
    decision = AgentDecision.model_validate(_decision_payload())

    assert decision.confidence == Decimal("0.9500000")


@pytest.mark.parametrize(
    ("provider", "model"),
    [(None, "model-a"), ("GOOGLE", None)],
)
def test_agent_decision_rejects_partial_model_reference(
    provider: str | None,
    model: str | None,
) -> None:
    payload = _decision_payload()
    payload["model_provider"] = provider
    payload["model_name"] = model

    with pytest.raises(ValidationError) as exc_info:
        AgentDecision.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "agent_decision_model_reference_pair_invalid"


@pytest.mark.parametrize("confidence", [Decimal("-0.0000001"), Decimal("1.0000001")])
def test_agent_decision_rejects_out_of_range_confidence(
    confidence: Decimal,
) -> None:
    payload = _decision_payload()
    payload["confidence"] = confidence

    with pytest.raises(ValidationError) as exc_info:
        AgentDecision.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] in {
        "greater_than_equal",
        "less_than_equal",
    }


def test_agent_decision_rejects_float_confidence() -> None:
    payload = _decision_payload()
    payload["confidence"] = 0.95

    with pytest.raises(ValidationError) as exc_info:
        AgentDecision.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_agent_decision_rejects_duplicate_evidence() -> None:
    payload = _decision_payload()
    payload["evidence_references"] = ("order:1", "order:1")

    with pytest.raises(ValidationError) as exc_info:
        AgentDecision.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "agent_decision_duplicate_evidence"


def test_agent_decision_requires_evidence() -> None:
    payload = _decision_payload()
    payload["evidence_references"] = ()

    with pytest.raises(ValidationError) as exc_info:
        AgentDecision.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "too_short"


def test_audit_event_accepts_safe_evidence() -> None:
    event = AuditEvent.model_validate(_audit_payload())

    assert event.correlation_id == _CORRELATION_ID


def test_audit_event_rejects_duplicate_evidence() -> None:
    payload = _audit_payload()
    payload["evidence_references"] = ("decision:1", "decision:1")

    with pytest.raises(ValidationError) as exc_info:
        AuditEvent.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "audit_event_duplicate_evidence"


def test_graph_sync_run_accepts_bounded_counters_and_validation() -> None:
    run = GraphSyncRun.model_validate(_sync_run_payload())

    assert run.records_read == 10
    assert run.validation_results[0].passed is True


def test_graph_sync_run_allows_in_progress_run_without_completed_at() -> None:
    payload = _sync_run_payload()
    payload["status"] = "RUNNING"
    payload["completed_at"] = None

    run = GraphSyncRun.model_validate(payload)

    assert run.completed_at is None


def test_graph_sync_run_requires_source_assets() -> None:
    payload = _sync_run_payload()
    payload["source_assets"] = ()

    with pytest.raises(ValidationError) as exc_info:
        GraphSyncRun.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "too_short"


def test_graph_sync_run_rejects_reverse_timeline() -> None:
    payload = _sync_run_payload()
    payload["completed_at"] = datetime(2026, 7, 20, 13, 59, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        GraphSyncRun.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "graph_sync_completed_at_invalid"


def test_graph_sync_run_rejects_negative_counter() -> None:
    payload = _sync_run_payload()
    payload["records_rejected"] = -1

    with pytest.raises(ValidationError) as exc_info:
        GraphSyncRun.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"


def test_graph_sync_run_rejects_boolean_counter() -> None:
    payload = _sync_run_payload()
    payload["records_read"] = True

    with pytest.raises(ValidationError) as exc_info:
        GraphSyncRun.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "int_type"


def test_graph_sync_run_rejects_duplicate_source_asset() -> None:
    payload = _sync_run_payload()
    payload["source_assets"] = ("TDS.salesInv", "TDS.salesInv")

    with pytest.raises(ValidationError) as exc_info:
        GraphSyncRun.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "graph_sync_duplicate_source_asset"


def test_graph_sync_run_rejects_duplicate_validation_code() -> None:
    result = {
        "validation_code": "NO_DUPLICATE_KEYS",
        "passed": True,
        "safe_message": "Passed.",
    }
    payload = _sync_run_payload()
    payload["validation_results"] = (result, result)

    with pytest.raises(ValidationError) as exc_info:
        GraphSyncRun.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "graph_sync_duplicate_validation_code"


def test_projection_evidence_accepts_projected_outcome_without_rejection() -> None:
    evidence = GraphProjectionEvidence.model_validate(_projection_payload())

    assert evidence.projection_status.value == "PROJECTED"


def test_projection_evidence_requires_reason_for_unresolved_outcome() -> None:
    payload = _projection_payload()
    payload["projection_status"] = GraphProjectionStatus.UNRESOLVED

    with pytest.raises(ValidationError) as exc_info:
        GraphProjectionEvidence.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "graph_projection_rejection_reason_required"


def test_projection_evidence_accepts_unresolved_outcome_with_reason() -> None:
    payload = _projection_payload()
    payload["projection_status"] = GraphProjectionStatus.UNRESOLVED
    payload["rejection_reason"] = "OMC product bridge was not found."

    evidence = GraphProjectionEvidence.model_validate(payload)

    assert evidence.rejection_reason == "OMC product bridge was not found."


def test_projection_evidence_rejects_reason_for_projected_outcome() -> None:
    payload = _projection_payload()
    payload["rejection_reason"] = "Unexpected reason."

    with pytest.raises(ValidationError) as exc_info:
        GraphProjectionEvidence.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "graph_projection_rejection_reason_forbidden"
