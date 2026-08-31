"""The Copilot read contract, against the semantics plan sect. 6.2-6.6 froze.

Six things are asserted here and each of them is a defect the audit found:

* **Both enums are complete and frozen.** Adding a member later breaks the
  frontend, the migration and the mock server at once, so the sets are written
  out literally rather than derived from the code under test.
* **Absence is distinguishable from default.** No optional block on the contract
  may default to anything but `None`, checked by walking the models rather than
  by reading them.
* **Artifacts have exactly one home, and it is the return record.** A label with
  no package is expressible; a package's documents are the ones naming it in
  `shipmentId`; and no document is reachable from a package it does not name.
* **Stage derivation is exhaustive and many-to-many.** Every status is covered
  twice -- on a bare case and on one carrying an RMA -- because the pair is what
  proves the relationship is not a lookup table.
* **Stage is monotone.** A secondary package moving must not drag the stage
  backwards, and every regression must be explained by one of the three
  enumerated reasons.
* **Completion cannot be faked.** Rejected, cancelled, expired, recovering,
  approval-pending and unresolved-method cases all report
  `businessComplete == false`, including where the requirement set is empty --
  which the requirement table makes unconstructable in the first place.
"""

from __future__ import annotations

import inspect
import itertools
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from return_platform.operations.case_projection import (
    AWAITING_DIMENSION_ORDER,
    COMPLETION_FORBIDDING_STATUSES,
    DEFAULT_RETURN_METHOD_REQUIREMENTS,
    POLICY_GATE_STATE_FACT,
    POLICY_GATE_SUSPENDED,
    REQUIREMENT_DIMENSIONS,
    STAGE_PRECEDENCE,
    STAGE_PRECEDENCE_LEVELS,
    SUCCESSFUL_TERMINAL_STATUSES,
    TERMINAL_RETURN_CASE_STATUSES,
    UNRESOLVED_DIMENSIONS,
    UNSUCCESSFUL_TERMINAL_STATUSES,
    ApprovedItemProjection,
    AwaitingDimension,
    CaseFactProjection,
    CaseProjectionState,
    ConfirmedOrderProjection,
    CopilotStage,
    CustomerProjection,
    NormalizedReturnMethod,
    PickupProjection,
    PolicyEvaluationProjection,
    PolicyOverrideProjection,
    ReturnArtifactProjection,
    ReturnArtifactType,
    ReturnCaseStatus,
    ReturnMethodRequirement,
    ReturnMethodRequirementTable,
    ReturnRecordProjection,
    SelectedItemProjection,
    SettlementProjection,
    SettlementStatus,
    ShipmentProjection,
    ShipmentStatus,
    StageRegressionReason,
    SupportProjection,
    WarehouseProjection,
    classify_stage_transition,
    derive_copilot_stage,
    effective_decision,
    is_terminal_status,
    policy_gate_suspended,
    project_case,
    resolve_completion,
    resolve_method_requirements,
    stage_rank,
)
from return_platform.operations.case_projection import assembly
from return_platform.operations.case_projection import contract as contract_module
from return_platform.operations.case_projection.completion import ROUTE_VERIFICATION_DIMENSIONS
from return_platform.policy import EligibilityDecision, PolicyReasonCode, PolicyRoute

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

#: The table these tests assert the completion rules against, named once.
#:
#: Passed explicitly at every call because `resolve_completion`,
#: `resolve_method_requirements` and `project_case` have no default for it
#: (D21 -- `project_case` lost its last, which the workflow's completion read
#: had been relying on). That is not
#: ceremony: the requirement table is operator-owned configuration, a default
#: would let a caller compute completion from a table nobody released, and a
#: test suite able to omit the argument would be exercising a signature
#: production does not have.
#:
#: The code baseline rather than the shipped release, because what is under test
#: here is the completion *algebra* -- a set difference over whatever table it is
#: handed. `tests/configuration/test_return_method_requirements_configuration.py`
#: is where the released table's own rows are asserted.
BASELINE = DEFAULT_RETURN_METHOD_REQUIREMENTS


# --------------------------------------------------------------------------
# Builders. Every one of them produces the *minimum* state that makes its
# clause true, so a test that passes says the clause did it rather than
# something else in a fat fixture.
# --------------------------------------------------------------------------


def case(**overrides: Any) -> CaseProjectionState:
    base: dict[str, Any] = {
        "caseId": "CASE-1",
        "tenantId": "acme",
        "principalId": "assoc-1",
        "conversationId": "CONV-1",
        "status": ReturnCaseStatus.GATHERING_INFO,
        "revision": 3,
        "updatedAt": NOW,
    }
    return CaseProjectionState(**{**base, **overrides})


def evaluation(
    decision: EligibilityDecision | None = EligibilityDecision.APPROVE,
    *,
    route: PolicyRoute = PolicyRoute.STANDARD_RETURN,
    override: PolicyOverrideProjection | None = None,
    #: Left absent by default so existing cases keep describing an evaluation
    #: that reached its decision on the facts. `REQUIRED_FACT_UNKNOWN` among
    #: these is what marks one that could not -- see `_evaluation_has_a_subject`.
    reasons: tuple[PolicyReasonCode, ...] | None = None,
) -> PolicyEvaluationProjection:
    effective = override.overrideDecision if override is not None else decision
    return PolicyEvaluationProjection(
        route=route,
        originalDecision=decision,
        effectiveDecision=effective,
        override=override,
        reasonCodes=reasons,
        policyId="FERGUSON_RETURNS",
        policyVersion="2025.05",
        evaluatedAt=NOW,
    )


def supervisor_override(
    decision: EligibilityDecision = EligibilityDecision.APPROVE,
) -> PolicyOverrideProjection:
    return PolicyOverrideProjection(
        overrideDecision=decision,
        reasonCode="SUPERVISOR_JUDGEMENT",
        reason="Contract customer, goods verified unused at the counter.",
        actor="supervisor-7",
        overriddenAt=NOW,
    )


def artifact(
    artifact_type: ReturnArtifactType,
    *,
    artifact_id: str = "ART-1",
    shipment_id: str | None = None,
    active: bool = True,
    superseded_by: str | None = None,
) -> ReturnArtifactProjection:
    """One document on an RMA. `shipment_id` is the whole of its attribution.

    `None` is a real answer -- the RMA has the document and no package is known
    yet -- so it is the default rather than an omission.
    """
    return ReturnArtifactProjection(
        artifactId=artifact_id,
        artifactType=artifact_type,
        shipmentId=shipment_id,
        active=active,
        supersededBy=superseded_by,
        createdAt=NOW,
    )


def fact(
    *,
    fact_id: str,
    fact_name: str,
    value: str | int | float | bool | None,
) -> CaseFactProjection:
    """One projected fact, with every provenance field explicitly `None`.

    `CaseFactProjection` carries no defaults on purpose -- all eleven fields are
    required-and-nullable, because the writer always writes all eleven and a
    document saying otherwise would let a client type an impossible absence.
    That makes every construction site name every field, which is why this
    builder exists: the tests below care about `factName`/`value` and nothing
    else, and nine literal `None`s repeated at five sites would obscure that.

    Each field is bound **explicitly**, never spread from a mapping: a builder
    that forwards `**kwargs` into the model would keep type-checking after a
    field is renamed and quietly stop populating it.
    """
    return CaseFactProjection(
        factId=fact_id,
        factName=fact_name,
        value=value,
        agentId=None,
        actorId=None,
        channel=None,
        sourceSystem=None,
        acquisitionMethod=None,
        observedAt=None,
        recordedAt=None,
        supersedesFactId=None,
    )


def shipment(
    shipment_id: str = "SHP-1",
    *,
    status: ShipmentStatus | None = None,
    tracking: str | None = None,
) -> ShipmentProjection:
    return ShipmentProjection(
        shipmentId=shipment_id,
        shipmentStatus=status,
        trackingNumber=tracking,
    )


def record(
    *,
    record_id: str = "REC-1",
    reference: str | None = "RMA-1",
    method: str | None = NormalizedReturnMethod.PREPAID_PARCEL.value,
    location: str | None = None,
    shipments: tuple[ShipmentProjection, ...] | None = None,
    artifacts: tuple[ReturnArtifactProjection, ...] | None = None,
) -> ReturnRecordProjection:
    return ReturnRecordProjection(
        returnRecordId=record_id,
        returnReference=reference,
        returnMethod=method,
        returnLocation=location,
        shipments=shipments,
        artifacts=artifacts,
    )


def scheduled_pickup() -> PickupProjection:
    return PickupProjection(pickupReference="PU-1", scheduledAt=NOW, status="SCHEDULED")


def parcel_shipment(shipment_id: str = "SHP-1") -> ShipmentProjection:
    """A tendered parcel. Its label is a separate fact, on the record."""
    return shipment(shipment_id, tracking=f"TRK-{shipment_id}")


def parcel_label(shipment_id: str = "SHP-1") -> ReturnArtifactProjection:
    """The label for that parcel, attributed to it by `shipmentId` and nothing else."""
    return artifact(
        ReturnArtifactType.SHIPPING_LABEL,
        artifact_id=f"LBL-{shipment_id}",
        shipment_id=shipment_id,
    )


def parcel_record(**overrides: Any) -> ReturnRecordProjection:
    """One RMA, one fully-papered parcel: tracking on the package, label on the record."""
    defaults: dict[str, Any] = {
        "shipments": (parcel_shipment(),),
        "artifacts": (parcel_label(),),
    }
    return record(**{**defaults, **overrides})


def approved_case(**overrides: Any) -> CaseProjectionState:
    """Approved, one RMA, one fully-papered parcel. The completion happy path."""
    defaults: dict[str, Any] = {
        "status": ReturnCaseStatus.PROCESSING_RETURN,
        "policyEvaluation": evaluation(EligibilityDecision.APPROVE),
        "returnRecords": (parcel_record(),),
    }
    return case(**{**defaults, **overrides})


# --------------------------------------------------------------------------
# 6.2 -- both enums, frozen and complete
# --------------------------------------------------------------------------


def test_return_case_status_is_the_frozen_complete_set() -> None:
    assert {status.value for status in ReturnCaseStatus} == {
        "GATHERING_INFO",
        "AWAITING_POLICY_REVIEW",
        "AWAITING_SUPPORT",
        "PROCESSING_RETURN",
        "COMPLETED",
        "COMPLETED_EXTERNAL_SETTLEMENT",
        "POLICY_REJECTED",
        "CANCELLED",
        "EXPIRED",
        "RECOVERY_REQUIRED",
    }


def test_copilot_stage_is_the_frozen_complete_set_in_lifecycle_order() -> None:
    assert tuple(stage.value for stage in CopilotStage) == (
        "DISCOVERY",
        "ORDER_CONFIRMATION",
        "ITEM_SELECTION",
        "RETURN_FACTS",
        "POLICY_EVALUATION",
        "APPROVAL_REQUIRED",
        "AWAITING_SUPPORT",
        "AUTHORIZED_RMA",
        "CARRIER_TRANSIT",
        "WAREHOUSE_RECEIVING",
        "RETURN_SETTLEMENT",
        "COMPLETED",
    )


def test_there_are_no_routed_statuses() -> None:
    """Warranty and delivery claims are `AWAITING_SUPPORT` with a dimension.

    Support verifies both and the case rejoins the normal RMA lifecycle, so a
    `ROUTED_WARRANTY` terminal status would end a return halfway through its own
    happy path.
    """
    assert [status for status in ReturnCaseStatus if "ROUTED" in status.value] == []
    assert AwaitingDimension.WARRANTY_VERIFICATION in UNRESOLVED_DIMENSIONS
    assert AwaitingDimension.DELIVERY_CLAIM_VERIFICATION in UNRESOLVED_DIMENSIONS


def test_the_terminal_set_excludes_recovery_required() -> None:
    assert TERMINAL_RETURN_CASE_STATUSES == {
        ReturnCaseStatus.COMPLETED,
        ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT,
        ReturnCaseStatus.POLICY_REJECTED,
        ReturnCaseStatus.CANCELLED,
        ReturnCaseStatus.EXPIRED,
    }
    assert ReturnCaseStatus.RECOVERY_REQUIRED not in TERMINAL_RETURN_CASE_STATUSES
    assert not is_terminal_status(ReturnCaseStatus.RECOVERY_REQUIRED)
    assert SUCCESSFUL_TERMINAL_STATUSES | UNSUCCESSFUL_TERMINAL_STATUSES == (
        TERMINAL_RETURN_CASE_STATUSES
    )


def test_settlement_is_not_an_awaiting_dimension() -> None:
    """Settlement never enters `awaiting` and never blocks completion."""
    assert not [dimension for dimension in AwaitingDimension if "SETTLE" in dimension.value.upper()]


def test_precedence_has_eleven_levels_and_ranks_the_policy_pair_equal() -> None:
    assert len(STAGE_PRECEDENCE_LEVELS) == 11
    assert STAGE_PRECEDENCE[0] is CopilotStage.COMPLETED
    assert STAGE_PRECEDENCE[-1] is CopilotStage.DISCOVERY
    assert set(STAGE_PRECEDENCE) == set(CopilotStage)
    # Level 7 of the frozen precedence is "policy evaluation / approval
    # required" -- one level, so an overridden review is not a regression.
    assert stage_rank(CopilotStage.POLICY_EVALUATION) == stage_rank(CopilotStage.APPROVAL_REQUIRED)
    assert stage_rank(CopilotStage.WAREHOUSE_RECEIVING) > stage_rank(CopilotStage.CARRIER_TRANSIT)


# --------------------------------------------------------------------------
# 6.3 -- the projection contract, absence distinguishable from default
# --------------------------------------------------------------------------


def _projection_models() -> list[type[BaseModel]]:
    base = contract_module.ProjectionModel
    return [
        value
        for value in vars(contract_module).values()
        if isinstance(value, type) and issubclass(value, base) and value is not base
    ]


def test_the_contract_declares_every_block_of_section_6_3() -> None:
    fields = set(contract_module.CaseProjection.model_fields)
    assert fields == {
        "caseId",
        "conversationId",
        "tenantId",
        "principalId",
        "status",
        "stage",
        "revision",
        "updatedAt",
        "awaiting",
        "businessComplete",
        "isTerminal",
        "customer",
        "confirmedOrder",
        "selectedItems",
        "facts",
        "policyEvaluation",
        "support",
        "returnRecords",
        "pickup",
        "warehouse",
        "settlement",
    }


def test_the_warehouse_block_declares_the_phase_9_fields_and_nothing_more() -> None:
    """Plan sect. 13 Phase 9's ten fields, plus `bayReason`.

    Frozen here so a field cannot be added without somebody stating its
    producer. Eight of the eleven have one now -- `facilityId`, `bayId` and
    `bayReason` from the bay facts `ReturnCaseWorkflow` writes, and
    `receivedAt`, `receivedQuantity`, `inspectionStatus`, `condition` and
    `warehouseStatus` from the receipt `POST /api/cases/{case_id}/receipt`
    records. `facilityName`, `disposition` and `qaStatus` still have none, and
    `assembly.project_warehouse` names the absence for each. A field nothing can
    fill is a field a pane will eventually fill for it.
    """
    assert set(contract_module.WarehouseProjection.model_fields) == {
        "facilityId",
        "facilityName",
        "bayId",
        "bayReason",
        "receivedAt",
        "receivedQuantity",
        "inspectionStatus",
        "condition",
        "disposition",
        "qaStatus",
        "warehouseStatus",
    }


def test_a_receipt_reaches_the_warehouse_block() -> None:
    """The producer those four fields waited for.

    Before it, a case could be recommended a bay days ahead of the goods and a
    carrier could report `delivered`, and neither made the platform able to say
    the warehouse had the item -- so "Reached warehouse" could never be true.
    """
    warehouse = assembly.project_warehouse(
        {
            "warehouse_received_at": {"value": "2026-08-28T14:05:00+00:00"},
            "warehouse_received_quantity": {"value": 4},
            "warehouse_inspection_status": {"value": "PASSED"},
            "warehouse_received_condition": {"value": "DAMAGED_IN_TRANSIT"},
            "warehouse_status": {"value": "RECEIVED"},
        }
    )

    assert warehouse is not None
    assert warehouse.receivedAt is not None
    assert warehouse.receivedQuantity == 4
    assert warehouse.inspectionStatus == "PASSED"
    assert warehouse.warehouseStatus == "RECEIVED"
    # The receiver's finding, which is a different statement from the condition
    # the associate claimed at selection. Read from the receipt only, so a
    # customer's claim is never relabelled as a warehouse finding.
    assert warehouse.condition == "DAMAGED_IN_TRANSIT"


def test_a_receipt_of_nothing_is_still_a_receipt() -> None:
    """Zero units arrived is an answer. Absent is a different one.

    A consignment that turned up empty is a receipt and a problem; one that
    never turned up is neither. Reading `0` as "no receipt" would merge them.
    """
    warehouse = assembly.project_warehouse(
        {
            "warehouse_received_at": {"value": "2026-08-28T14:05:00+00:00"},
            "warehouse_received_quantity": {"value": 0},
        }
    )

    assert warehouse is not None
    assert warehouse.receivedQuantity == 0


def test_a_bay_recommendation_alone_is_not_a_receipt() -> None:
    """Placement runs pre-arrival by design and must not read as arrival."""
    warehouse = assembly.project_warehouse(
        {
            "bay_warehouse_reference": {"value": "596"},
            "bay_reason": {"value": "PRE_ARRIVAL_NOT_ALLOWED"},
        }
    )

    assert warehouse is not None
    assert warehouse.receivedAt is None
    assert warehouse.receivedQuantity is None
    assert warehouse.warehouseStatus is None


def test_the_settlement_block_cannot_express_a_started_producer() -> None:
    """`NOT_INTEGRATED`, `PENDING`, `SETTLED` -- and deliberately no `NOT_STARTED`.

    `NOT_STARTED` would say a producer exists and has not run. None exists, and
    the vocabulary refuses to let a writer claim otherwise.
    """
    assert {status.value for status in SettlementStatus} == {
        "NOT_INTEGRATED",
        "PENDING",
        "SETTLED",
    }
    assert contract_module.SettlementProjection.model_fields["status"].is_required()


def test_no_optional_block_is_defaulted_to_anything_but_absent() -> None:
    """`()` means "looked, none"; `None` means "not computed". Never conflated."""
    for model in _projection_models():
        for name, field in model.model_fields.items():
            if field.is_required():
                continue
            assert field.default_factory is None, f"{model.__name__}.{name} has a default_factory"
            assert field.default is None, f"{model.__name__}.{name} defaults to {field.default!r}"


def test_absence_and_emptiness_are_different_values() -> None:
    assert record().artifacts is None
    assert record(artifacts=()).artifacts == ()
    assert case().returnRecords is None
    assert case(returnRecords=()).returnRecords == ()


def test_artifacts_have_exactly_one_home_and_it_is_the_return_record() -> None:
    """A shipment cannot carry documents; two homes is how one is misattributed."""
    assert "artifacts" in ReturnRecordProjection.model_fields
    assert not [
        name
        for name in ShipmentProjection.model_fields
        if "artifact" in name.lower() or "label" in name.lower()
    ]
    assert not hasattr(ShipmentProjection, "active_artifacts")


def test_a_label_without_a_shipment_is_expressible_on_the_record() -> None:
    """The exact shape of `RMA-OPS01-CD4364`: a label, and no package to hang it on.

    Before artifacts moved to the record this state had no representation at
    all, and the only way to project it was to invent a shipment id and status.
    """
    detail = record(
        reference="RMA-OPS01-CD4364",
        shipments=None,
        artifacts=(
            artifact(ReturnArtifactType.SHIPPING_LABEL, artifact_id="LBL-OPS01", shipment_id=None),
        ),
    )
    assert detail.active_shipments() == ()
    live = detail.active_artifacts(ReturnArtifactType.SHIPPING_LABEL)
    assert [item.artifactId for item in live] == ["LBL-OPS01"]
    assert live[0].shipmentId is None


def test_one_rma_carries_several_packages_each_with_its_own_artifacts() -> None:
    """`returnRecords[] -> shipments[]`, artifacts attributed by `shipmentId`.

    Never 1 RMA = 1 package, and never a document reachable from a package it
    does not name.
    """
    detail = record(
        shipments=(shipment("SHP-1"), shipment("SHP-2")),
        artifacts=(
            artifact(ReturnArtifactType.SHIPPING_LABEL, artifact_id="LBL-1", shipment_id="SHP-1"),
            artifact(ReturnArtifactType.SHIPPING_LABEL, artifact_id="LBL-2", shipment_id="SHP-2"),
        ),
    )
    assert detail.shipments is not None
    labels = {
        package.shipmentId: [
            item.artifactId
            for item in detail.active_artifacts_for_shipment(
                ReturnArtifactType.SHIPPING_LABEL, package.shipmentId
            )
        ]
        for package in detail.shipments
    }
    assert labels == {"SHP-1": ["LBL-1"], "SHP-2": ["LBL-2"]}


def test_an_unattributed_artifact_does_not_paper_any_package() -> None:
    """`shipmentId: None` means "no package yet", never "every package"."""
    detail = record(
        shipments=(shipment("SHP-1"),),
        artifacts=(artifact(ReturnArtifactType.SHIPPING_LABEL, artifact_id="LBL-1"),),
    )
    assert detail.active_artifacts(ReturnArtifactType.SHIPPING_LABEL) != ()
    assert detail.active_artifacts_for_shipment(ReturnArtifactType.SHIPPING_LABEL, "SHP-1") == ()


def test_a_superseded_artifact_is_not_the_active_one() -> None:
    """The single label action resolves to the declared active artifact, never `labels[0]`."""
    detail = record(
        shipments=(parcel_shipment(),),
        artifacts=(
            artifact(
                ReturnArtifactType.SHIPPING_LABEL,
                artifact_id="LBL-1",
                shipment_id="SHP-1",
                superseded_by="LBL-2",
            ),
            artifact(ReturnArtifactType.SHIPPING_LABEL, artifact_id="LBL-2", shipment_id="SHP-1"),
        ),
    )
    assert [
        item.artifactId for item in detail.active_artifacts(ReturnArtifactType.SHIPPING_LABEL)
    ] == ["LBL-2"]
    # Superseded, not deleted: it is still on the projection for the audit.
    assert detail.artifacts is not None
    assert [item.artifactId for item in detail.artifacts] == ["LBL-1", "LBL-2"]


def test_an_artifact_carries_no_storage_url() -> None:
    """Artifacts are served through an opaque authenticated endpoint."""
    assert not [name for name in ReturnArtifactProjection.model_fields if "url" in name.lower()]


def test_a_shipment_cannot_borrow_order_source_as_a_carrier() -> None:
    """`orderSource` as carrier and `shippingPathExpectation` as ETA were live defects."""
    fields = set(ShipmentProjection.model_fields)
    assert "carrier" in fields
    assert "estimatedDeliveryAt" in fields
    assert "orderSource" not in fields
    assert "shippingPathExpectation" not in fields


def test_a_support_projection_has_no_type_field() -> None:
    """Route context travels as a configured queue. Do not add a work-item type."""
    assert "type" not in SupportProjection.model_fields
    assert "queue" in SupportProjection.model_fields


def test_effective_decision_must_agree_with_the_override() -> None:
    with pytest.raises(ValidationError, match="effectiveDecision"):
        PolicyEvaluationProjection(
            route=PolicyRoute.STANDARD_RETURN,
            originalDecision=EligibilityDecision.REVIEW_REQUIRED,
            effectiveDecision=EligibilityDecision.REVIEW_REQUIRED,
            override=supervisor_override(EligibilityDecision.APPROVE),
        )


def test_a_routed_evaluation_carries_neither_a_decision_nor_an_override() -> None:
    with pytest.raises(ValidationError, match="verification hand-off"):
        PolicyEvaluationProjection(
            route=PolicyRoute.WARRANTY,
            originalDecision=EligibilityDecision.APPROVE,
            effectiveDecision=EligibilityDecision.APPROVE,
        )
    with pytest.raises(ValidationError, match="verified by Support"):
        PolicyEvaluationProjection(
            route=PolicyRoute.DELIVERY_CLAIM,
            override=supervisor_override(),
            effectiveDecision=EligibilityDecision.APPROVE,
        )


# --------------------------------------------------------------------------
# 6.6 -- stage derivation, exhaustive over ReturnCaseStatus
# --------------------------------------------------------------------------

#: Every status on a case that carries nothing else. This is the exhaustive
#: half of the coverage; the next table is the same statuses on a case that has
#: an RMA, and together they show the relationship is many-to-many.
_BARE_STAGE_BY_STATUS: dict[ReturnCaseStatus, CopilotStage] = {
    ReturnCaseStatus.GATHERING_INFO: CopilotStage.DISCOVERY,
    ReturnCaseStatus.AWAITING_POLICY_REVIEW: CopilotStage.APPROVAL_REQUIRED,
    ReturnCaseStatus.AWAITING_SUPPORT: CopilotStage.AWAITING_SUPPORT,
    # Nothing has been projected onto the case yet, so there is nothing for the
    # Copilot to render beyond discovery. Honest rather than flattering: the
    # status alone is not evidence of an RMA.
    ReturnCaseStatus.PROCESSING_RETURN: CopilotStage.DISCOVERY,
    ReturnCaseStatus.COMPLETED: CopilotStage.COMPLETED,
    ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT: CopilotStage.COMPLETED,
    ReturnCaseStatus.POLICY_REJECTED: CopilotStage.COMPLETED,
    ReturnCaseStatus.CANCELLED: CopilotStage.COMPLETED,
    ReturnCaseStatus.EXPIRED: CopilotStage.COMPLETED,
    ReturnCaseStatus.RECOVERY_REQUIRED: CopilotStage.DISCOVERY,
}

#: The same statuses, on a case whose RMA has been issued.
_WITH_RMA_STAGE_BY_STATUS: dict[ReturnCaseStatus, CopilotStage] = {
    ReturnCaseStatus.GATHERING_INFO: CopilotStage.AUTHORIZED_RMA,
    ReturnCaseStatus.AWAITING_POLICY_REVIEW: CopilotStage.AUTHORIZED_RMA,
    # The many-to-many proof: same persisted status as the bare table, different
    # stage, because Support has answered and the workflow has not been
    # signalled yet.
    ReturnCaseStatus.AWAITING_SUPPORT: CopilotStage.AUTHORIZED_RMA,
    ReturnCaseStatus.PROCESSING_RETURN: CopilotStage.AUTHORIZED_RMA,
    ReturnCaseStatus.COMPLETED: CopilotStage.COMPLETED,
    ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT: CopilotStage.COMPLETED,
    ReturnCaseStatus.POLICY_REJECTED: CopilotStage.COMPLETED,
    ReturnCaseStatus.CANCELLED: CopilotStage.COMPLETED,
    ReturnCaseStatus.EXPIRED: CopilotStage.COMPLETED,
    ReturnCaseStatus.RECOVERY_REQUIRED: CopilotStage.AUTHORIZED_RMA,
}


def test_both_status_tables_are_exhaustive() -> None:
    assert set(_BARE_STAGE_BY_STATUS) == set(ReturnCaseStatus)
    assert set(_WITH_RMA_STAGE_BY_STATUS) == set(ReturnCaseStatus)


@pytest.mark.parametrize(("status", "expected"), sorted(_BARE_STAGE_BY_STATUS.items()))
def test_every_status_derives_a_stage_on_a_bare_case(
    status: ReturnCaseStatus, expected: CopilotStage
) -> None:
    assert derive_copilot_stage(case(status=status)) is expected


@pytest.mark.parametrize(("status", "expected"), sorted(_WITH_RMA_STAGE_BY_STATUS.items()))
def test_every_status_derives_a_stage_on_a_case_with_an_rma(
    status: ReturnCaseStatus, expected: CopilotStage
) -> None:
    assert derive_copilot_stage(case(status=status, returnRecords=(record(),))) is expected


def test_the_status_to_stage_relationship_is_many_to_many() -> None:
    """Which is why it is never implemented as a lookup table."""
    one_status_many_stages = {
        _BARE_STAGE_BY_STATUS[ReturnCaseStatus.AWAITING_SUPPORT],
        _WITH_RMA_STAGE_BY_STATUS[ReturnCaseStatus.AWAITING_SUPPORT],
    }
    assert len(one_status_many_stages) == 2

    many_statuses_one_stage = {
        status for status, stage in _BARE_STAGE_BY_STATUS.items() if stage is CopilotStage.COMPLETED
    }
    assert len(many_statuses_one_stage) == 5


# --------------------------------------------------------------------------
# 6.6 -- the frozen precedence, level by level
# --------------------------------------------------------------------------


def test_terminal_outranks_everything_below_it() -> None:
    settled = approved_case(
        status=ReturnCaseStatus.COMPLETED,
        settlement=SettlementProjection(status=SettlementStatus.PENDING),
        warehouse=WarehouseProjection(receivedAt=NOW),
    )
    assert derive_copilot_stage(settled) is CopilotStage.COMPLETED


def test_settlement_outranks_warehouse() -> None:
    state = approved_case(
        settlement=SettlementProjection(status=SettlementStatus.PENDING),
        warehouse=WarehouseProjection(receivedAt=NOW),
    )
    assert derive_copilot_stage(state) is CopilotStage.RETURN_SETTLEMENT


def test_a_not_integrated_settlement_is_not_a_settlement_stage() -> None:
    """`NOT_INTEGRATED` names an absent producer; it is not progress."""
    state = approved_case(settlement=SettlementProjection(status=SettlementStatus.NOT_INTEGRATED))
    assert derive_copilot_stage(state) is CopilotStage.AUTHORIZED_RMA


def test_warehouse_outranks_carrier_transit() -> None:
    state = approved_case(
        returnRecords=(record(shipments=(shipment(status=ShipmentStatus.IN_TRANSIT),)),),
        warehouse=WarehouseProjection(warehouseStatus="WAREHOUSE_RECEIVED"),
    )
    assert derive_copilot_stage(state) is CopilotStage.WAREHOUSE_RECEIVING


def test_an_empty_warehouse_block_is_not_a_receipt() -> None:
    state = approved_case(warehouse=WarehouseProjection())
    assert derive_copilot_stage(state) is CopilotStage.AUTHORIZED_RMA


def test_a_bay_placement_is_not_a_receipt_however_complete_it_is() -> None:
    """Every placement field at once still does not book goods in.

    `bayReason` is a field of its own rather than a value in `warehouseStatus`
    for exactly this: the reason has a producer and the status does not, and
    merging them would let a recommendation light the receiving pane.
    """
    state = approved_case(
        warehouse=WarehouseProjection(
            facilityId="WH-ATL-01", bayId="BAY-3", bayReason="RECOMMENDED"
        )
    )
    assert state.warehouse is not None
    assert state.warehouse.has_receipt is False
    assert derive_copilot_stage(state) is CopilotStage.AUTHORIZED_RMA


def test_an_rma_outranks_an_open_support_work_item() -> None:
    """Support answering with an RMA moves the pane off "waiting for Support"."""
    state = case(
        status=ReturnCaseStatus.AWAITING_SUPPORT,
        support=SupportProjection(workItemId="WI-1", queue="RETURNS_SUPPORT"),
        returnRecords=(record(),),
    )
    assert derive_copilot_stage(state) is CopilotStage.AUTHORIZED_RMA


def test_a_record_without_a_reference_is_not_an_authorized_rma() -> None:
    state = case(
        status=ReturnCaseStatus.PROCESSING_RETURN,
        policyEvaluation=evaluation(EligibilityDecision.APPROVE),
        returnRecords=(record(reference=None),),
    )
    assert derive_copilot_stage(state) is CopilotStage.POLICY_EVALUATION


@pytest.mark.parametrize("route", [PolicyRoute.WARRANTY, PolicyRoute.DELIVERY_CLAIM])
def test_a_verification_route_lands_on_the_existing_awaiting_support_stage(
    route: PolicyRoute,
) -> None:
    """No new stage, no UI change."""
    state = case(
        status=ReturnCaseStatus.AWAITING_SUPPORT, policyEvaluation=evaluation(None, route=route)
    )
    assert derive_copilot_stage(state) is CopilotStage.AWAITING_SUPPORT


def test_approval_required_outranks_policy_evaluation_within_their_level() -> None:
    state = case(policyEvaluation=evaluation(EligibilityDecision.REVIEW_REQUIRED))
    assert derive_copilot_stage(state) is CopilotStage.APPROVAL_REQUIRED


def test_return_facts_needs_a_return_fact_not_merely_a_fact() -> None:
    """An order number found during discovery must not read as return facts."""
    discovery_only = case(
        confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
        selectedItems=(SelectedItemProjection(returnItemId="RI-1", orderLineReference="L1"),),
        facts=(fact(fact_id="F-1", fact_name="order_number", value="SO-1"),),
    )
    assert derive_copilot_stage(discovery_only) is CopilotStage.ITEM_SELECTION

    with_return_fact = case(
        confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
        selectedItems=(SelectedItemProjection(returnItemId="RI-1", orderLineReference="L1"),),
        facts=(fact(fact_id="F-2", fact_name="return_reason", value="DAMAGED"),),
    )
    assert derive_copilot_stage(with_return_fact) is CopilotStage.RETURN_FACTS


def test_item_selection_outranks_order_confirmation() -> None:
    state = case(
        confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
        selectedItems=(SelectedItemProjection(returnItemId="RI-1", orderLineReference="L1"),),
    )
    assert derive_copilot_stage(state) is CopilotStage.ITEM_SELECTION
    assert (
        derive_copilot_stage(case(confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1")))
        is CopilotStage.ORDER_CONFIRMATION
    )


def test_an_empty_selection_is_not_a_selection() -> None:
    state = case(confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"), selectedItems=())
    assert derive_copilot_stage(state) is CopilotStage.ORDER_CONFIRMATION


def test_an_evaluation_missing_a_required_fact_is_not_arrival() -> None:
    """The trap a live case fell into on 2026-08-16, twice.

    A turn confirmed the order and closed the exchange in the same breath, so
    the case was raised with nothing selected and the evaluator ran against it.
    It reported REQUIRED_FACT_UNKNOWN and failed safe to REVIEW_REQUIRED -- an
    honest "nobody has told me yet".

    Stage derivation read that as arrival. `APPROVAL_REQUIRED` and
    `POLICY_EVALUATION` both outrank `ITEM_SELECTION` and `ORDER_CONFIRMATION`,
    so the Copilot drew the evaluation pane, offered a supervisor an override of
    a decision nobody had made on the merits, and **could never leave**:
    `_has_selected_items` ranks below, and the only screen that can create a
    selection is the item pane that had just become unreachable.

    The signal is the evaluator's own reason code and deliberately not a count
    of `selectedItems` -- that list omits every item an RMA already covers, so
    it empties as a case *progresses*, and gating on it demoted cases that were
    genuinely evaluated.
    """
    fail_safe = case(
        confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
        status=ReturnCaseStatus.AWAITING_POLICY_REVIEW,
        policyEvaluation=evaluation(
            EligibilityDecision.REVIEW_REQUIRED,
            reasons=(PolicyReasonCode.REQUIRED_FACT_UNKNOWN,),
        ),
    )
    assert derive_copilot_stage(fail_safe) is CopilotStage.ORDER_CONFIRMATION


def test_an_evaluation_that_decided_is_arrival() -> None:
    """The guard narrows one case and must leave every other one alone."""
    decided = case(
        confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
        policyEvaluation=evaluation(EligibilityDecision.APPROVE),
    )
    assert derive_copilot_stage(decided) is CopilotStage.POLICY_EVALUATION


def test_a_review_on_the_merits_still_reaches_a_supervisor() -> None:
    """REVIEW_REQUIRED for a stated reason is a finding, not an absence.

    The distinction the guard rests on: an evaluation that weighed the facts and
    wants a human is exactly what the approval pane is for.
    """
    on_the_merits = case(
        confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
        selectedItems=(SelectedItemProjection(returnItemId="RI-1", orderLineReference="1"),),
        policyEvaluation=evaluation(
            EligibilityDecision.REVIEW_REQUIRED,
            reasons=(PolicyReasonCode.OUTSIDE_STANDARD_RETURN_WINDOW,),
        ),
    )
    assert derive_copilot_stage(on_the_merits) is CopilotStage.APPROVAL_REQUIRED


def test_support_holding_a_case_survives_an_undecided_evaluation() -> None:
    """An open Support work item is an observation, not an inference.

    Gating it would hide something a person is actually doing.
    """
    state = case(
        confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
        status=ReturnCaseStatus.AWAITING_SUPPORT,
        policyEvaluation=evaluation(
            EligibilityDecision.REVIEW_REQUIRED,
            reasons=(PolicyReasonCode.REQUIRED_FACT_UNKNOWN,),
        ),
    )
    assert derive_copilot_stage(state) is CopilotStage.AWAITING_SUPPORT


def test_a_case_with_nothing_on_it_is_discovery() -> None:
    assert derive_copilot_stage(case()) is CopilotStage.DISCOVERY
    assert derive_copilot_stage(case(customer=CustomerProjection(displayName="Ada"))) is (
        CopilotStage.DISCOVERY
    )


# --------------------------------------------------------------------------
# 6.6 -- mixed shipments resolve to the furthest-progressed package
# --------------------------------------------------------------------------


def test_one_package_in_transit_and_one_received_is_warehouse_receiving() -> None:
    state = approved_case(
        returnRecords=(
            record(
                shipments=(
                    shipment("SHP-A", status=ShipmentStatus.IN_TRANSIT),
                    shipment("SHP-B", status=ShipmentStatus.RECEIVED),
                )
            ),
        )
    )
    assert derive_copilot_stage(state) is CopilotStage.WAREHOUSE_RECEIVING


def test_a_delivered_package_already_counts_as_receiving() -> None:
    state = approved_case(
        returnRecords=(
            record(
                shipments=(
                    shipment("SHP-A", status=ShipmentStatus.AWAITING_HANDOFF),
                    shipment("SHP-B", status=ShipmentStatus.DELIVERED),
                )
            ),
        )
    )
    assert derive_copilot_stage(state) is CopilotStage.WAREHOUSE_RECEIVING


def test_the_leading_edge_wins_across_two_rmas() -> None:
    state = approved_case(
        returnRecords=(
            record(
                record_id="REC-1",
                shipments=(shipment("SHP-A", status=ShipmentStatus.AWAITING_HANDOFF),),
            ),
            record(
                record_id="REC-2",
                reference="RMA-2",
                shipments=(shipment("SHP-B", status=ShipmentStatus.IN_TRANSIT),),
            ),
        )
    )
    assert derive_copilot_stage(state) is CopilotStage.CARRIER_TRANSIT


def test_a_cancelled_package_contributes_nothing() -> None:
    state = approved_case(
        returnRecords=(record(shipments=(shipment("SHP-A", status=ShipmentStatus.CANCELLED),)),),
    )
    assert derive_copilot_stage(state) is CopilotStage.AUTHORIZED_RMA


# --------------------------------------------------------------------------
# 6.6 -- monotonicity
# --------------------------------------------------------------------------

#: One case walked from discovery to completion. Each entry is the state after
#: the event named beside it.
_LIFECYCLE: tuple[tuple[str, CaseProjectionState], ...] = (
    ("conversation opened", case()),
    (
        "order confirmed",
        case(confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1")),
    ),
    (
        "lines selected",
        case(
            confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
            selectedItems=(SelectedItemProjection(returnItemId="RI-1", orderLineReference="L1"),),
        ),
    ),
    (
        "return facts captured",
        case(
            confirmedOrder=ConfirmedOrderProjection(orderReference="SO-1"),
            selectedItems=(SelectedItemProjection(returnItemId="RI-1", orderLineReference="L1"),),
            facts=(fact(fact_id="F-1", fact_name="return_reason", value="DAMAGED"),),
        ),
    ),
    (
        "review required",
        case(
            status=ReturnCaseStatus.AWAITING_POLICY_REVIEW,
            policyEvaluation=evaluation(EligibilityDecision.REVIEW_REQUIRED),
        ),
    ),
    (
        "supervisor overrode the review to approve",
        case(
            status=ReturnCaseStatus.PROCESSING_RETURN,
            policyEvaluation=evaluation(
                EligibilityDecision.REVIEW_REQUIRED, override=supervisor_override()
            ),
        ),
    ),
    (
        "support work item opened",
        case(
            status=ReturnCaseStatus.AWAITING_SUPPORT,
            policyEvaluation=evaluation(EligibilityDecision.APPROVE),
            support=SupportProjection(workItemId="WI-1", queue="RETURNS_SUPPORT"),
        ),
    ),
    ("rma issued", approved_case(returnRecords=(record(shipments=None),))),
    (
        "parcel handed to the carrier",
        approved_case(
            returnRecords=(
                record(shipments=(shipment("SHP-1", status=ShipmentStatus.IN_TRANSIT),)),
            )
        ),
    ),
    (
        "parcel received",
        approved_case(
            returnRecords=(record(shipments=(shipment("SHP-1", status=ShipmentStatus.RECEIVED),)),)
        ),
    ),
    (
        "credit raised",
        approved_case(
            returnRecords=(record(shipments=(shipment("SHP-1", status=ShipmentStatus.RECEIVED),)),),
            settlement=SettlementProjection(status=SettlementStatus.PENDING),
        ),
    ),
    ("workflow completed", approved_case(status=ReturnCaseStatus.COMPLETED)),
)


def test_the_lifecycle_never_regresses() -> None:
    for (before_name, before), (after_name, after) in itertools.combinations(_LIFECYCLE, 2):
        transition = classify_stage_transition(before, after)
        assert not transition.regressed, (
            f"{before_name} -> {after_name} regressed "
            f"{transition.previous.value} -> {transition.current.value}"
        )


def test_the_lifecycle_actually_advances() -> None:
    """A monotonicity proof over a sequence that never moves proves nothing."""
    stages = [derive_copilot_stage(state) for _, state in _LIFECYCLE]
    assert stages[0] is CopilotStage.DISCOVERY
    assert stages[-1] is CopilotStage.COMPLETED
    assert len(set(stages)) >= 9


def test_an_overridden_review_is_not_a_regression() -> None:
    """`APPROVAL_REQUIRED -> POLICY_EVALUATION` is one precedence level, not two."""
    before = case(
        status=ReturnCaseStatus.AWAITING_POLICY_REVIEW,
        policyEvaluation=evaluation(EligibilityDecision.REVIEW_REQUIRED),
    )
    after = case(
        status=ReturnCaseStatus.PROCESSING_RETURN,
        policyEvaluation=evaluation(
            EligibilityDecision.REVIEW_REQUIRED, override=supervisor_override()
        ),
    )
    transition = classify_stage_transition(before, after)
    assert transition.previous is CopilotStage.APPROVAL_REQUIRED
    assert transition.current is CopilotStage.POLICY_EVALUATION
    assert not transition.regressed


def test_a_secondary_package_moving_does_not_drag_the_stage_backwards() -> None:
    """The audit's monotonicity case, stated exactly."""
    before = approved_case(
        returnRecords=(
            record(
                shipments=(
                    shipment("SHP-A", status=ShipmentStatus.RECEIVED),
                    shipment("SHP-B", status=ShipmentStatus.AWAITING_HANDOFF),
                )
            ),
        )
    )
    after = approved_case(
        returnRecords=(
            record(
                shipments=(
                    shipment("SHP-A", status=ShipmentStatus.RECEIVED),
                    shipment("SHP-B", status=ShipmentStatus.IN_TRANSIT),
                )
            ),
        )
    )
    transition = classify_stage_transition(before, after)
    assert transition.previous is CopilotStage.WAREHOUSE_RECEIVING
    assert transition.current is CopilotStage.WAREHOUSE_RECEIVING
    assert not transition.regressed


@pytest.mark.parametrize("closing_status", [ReturnCaseStatus.CANCELLED, ReturnCaseStatus.EXPIRED])
def test_closing_a_case_does_not_regress_the_stage(closing_status: ReturnCaseStatus) -> None:
    """Cancellation is enumerated as permitted; under this derivation it never happens.

    A terminal case is at `COMPLETED`, the highest stage, so closing a return can
    only move the stage forward. The reason stays enumerated because a future
    rule change could reach it.
    """
    for _, before in _LIFECYCLE:
        after = before.model_copy(update={"status": closing_status})
        assert not classify_stage_transition(before, after).regressed


def test_recovery_does_not_regress_the_stage() -> None:
    """`RECOVERY_REQUIRED` is non-terminal, so the records still speak for the case."""
    for _, before in _LIFECYCLE:
        if before.status in TERMINAL_RETURN_CASE_STATUSES:
            continue
        after = before.model_copy(update={"status": ReturnCaseStatus.RECOVERY_REQUIRED})
        assert not classify_stage_transition(before, after).regressed


def test_a_replacement_shipment_is_the_one_regression_and_it_is_named() -> None:
    before = approved_case(
        returnRecords=(record(shipments=(shipment("SHP-1", status=ShipmentStatus.IN_TRANSIT),)),)
    )
    after = approved_case(
        returnRecords=(
            record(
                shipments=(
                    shipment("SHP-1", status=ShipmentStatus.CANCELLED),
                    shipment("SHP-2", status=ShipmentStatus.AWAITING_HANDOFF),
                )
            ),
        )
    )
    transition = classify_stage_transition(before, after)
    assert transition.previous is CopilotStage.CARRIER_TRANSIT
    assert transition.current is CopilotStage.AUTHORIZED_RMA
    assert transition.regressed
    assert transition.permitted_by is StageRegressionReason.REPLACEMENT_SHIPMENT


def test_every_enumerated_regression_reason_is_reachable_or_deliberately_not() -> None:
    assert set(StageRegressionReason) == {
        StageRegressionReason.CANCELLATION,
        StageRegressionReason.RECOVERY,
        StageRegressionReason.REPLACEMENT_SHIPMENT,
    }


# --------------------------------------------------------------------------
# 6.4 -- the requirement table
# --------------------------------------------------------------------------

_EXPECTED_REQUIREMENTS: dict[str, frozenset[AwaitingDimension]] = {
    "PREPAID_PARCEL": frozenset(
        {AwaitingDimension.RMA, AwaitingDimension.LABEL, AwaitingDimension.TRACKING}
    ),
    "BRANCH_UPS": frozenset(
        {AwaitingDimension.RMA, AwaitingDimension.LABEL, AwaitingDimension.TRACKING}
    ),
    "BRANCH_LTL": frozenset(
        {AwaitingDimension.RMA, AwaitingDimension.BOL, AwaitingDimension.PICKUP}
    ),
    "OFFSITE_PARCEL": frozenset(
        {
            AwaitingDimension.RMA,
            AwaitingDimension.LABEL,
            AwaitingDimension.TRACKING,
            AwaitingDimension.RETURN_LOCATION,
        }
    ),
    "OFFSITE_LTL": frozenset(
        {
            AwaitingDimension.RMA,
            AwaitingDimension.BOL,
            AwaitingDimension.PICKUP,
            AwaitingDimension.RETURN_LOCATION,
        }
    ),
    "DIRECT_VENDOR": frozenset({AwaitingDimension.RMA, AwaitingDimension.RETURN_LOCATION}),
    "FIELD_SCRAP": frozenset({AwaitingDimension.RMA}),
    "NO_PHYSICAL_RETURN": frozenset({AwaitingDimension.RMA}),
    "CUSTOMER_KEEP": frozenset({AwaitingDimension.RMA}),
}


def test_the_table_covers_every_configured_method_except_unknown() -> None:
    """The vocabulary is `return_policy.normalized_return_methods`, all ten of it."""
    catalogue = {method.value for method in NormalizedReturnMethod}
    assert len(catalogue) == 10
    assert set(_EXPECTED_REQUIREMENTS) == catalogue - {"UNKNOWN"}
    assert {str(row.method) for row in DEFAULT_RETURN_METHOD_REQUIREMENTS.rows} == set(
        _EXPECTED_REQUIREMENTS
    )


@pytest.mark.parametrize(("method", "expected"), sorted(_EXPECTED_REQUIREMENTS.items()))
def test_each_method_requires_what_the_table_says(
    method: str, expected: frozenset[AwaitingDimension]
) -> None:
    assert DEFAULT_RETURN_METHOD_REQUIREMENTS.requirements_for(method) is not None
    assert set(DEFAULT_RETURN_METHOD_REQUIREMENTS.requirements_for(method) or ()) == expected


def test_unknown_has_no_row_and_cannot_be_given_one() -> None:
    assert DEFAULT_RETURN_METHOD_REQUIREMENTS.requirements_for("UNKNOWN") is None
    with pytest.raises(ValidationError, match="absence of a return method"):
        ReturnMethodRequirement(method="UNKNOWN", requires=(AwaitingDimension.RMA,))


def test_a_requirement_row_can_never_be_empty() -> None:
    """Completion cannot be reached by an empty requirement set, structurally."""
    with pytest.raises(ValidationError):
        ReturnMethodRequirement(method="PREPAID_PARCEL", requires=())
    with pytest.raises(ValidationError, match="must require RMA"):
        ReturnMethodRequirement(method="PREPAID_PARCEL", requires=(AwaitingDimension.LABEL,))


def test_a_row_cannot_require_an_unresolved_dimension() -> None:
    with pytest.raises(ValidationError, match="not fulfilment"):
        ReturnMethodRequirement(
            method="PREPAID_PARCEL", requires=(AwaitingDimension.RMA, AwaitingDimension.POLICY)
        )


def test_a_method_appears_at_most_once() -> None:
    with pytest.raises(ValidationError, match="only once"):
        ReturnMethodRequirementTable(
            rows=(
                ReturnMethodRequirement(method="PREPAID_PARCEL", requires=(AwaitingDimension.RMA,)),
                ReturnMethodRequirement(method="prepaid_parcel", requires=(AwaitingDimension.RMA,)),
            )
        )


def test_requirement_and_unresolved_dimensions_partition_the_vocabulary() -> None:
    assert REQUIREMENT_DIMENSIONS | UNRESOLVED_DIMENSIONS == set(AwaitingDimension)
    assert not REQUIREMENT_DIMENSIONS & UNRESOLVED_DIMENSIONS


def test_an_unmapped_method_leaves_the_profile_unresolved() -> None:
    """An operator-added method the table has not been taught waits; it does not complete."""
    detail = record(method="OFFSITE_HEAVY_PICKUP")
    assert resolve_method_requirements(detail, requirements=BASELINE) is None
    assessment = resolve_completion(approved_case(returnRecords=(detail,)), requirements=BASELINE)
    assert assessment.awaiting == (AwaitingDimension.RETURN_METHOD,)
    assert not assessment.business_complete


# --------------------------------------------------------------------------
# 6.4 -- awaiting, per return method
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "expected"), sorted(_EXPECTED_REQUIREMENTS.items()))
def test_awaiting_on_an_rma_alone_is_the_requirement_set_minus_the_rma(
    method: str, expected: frozenset[AwaitingDimension]
) -> None:
    assessment = resolve_completion(
        approved_case(returnRecords=(record(method=method),)), requirements=BASELINE
    )
    assert set(assessment.awaiting) == expected - {AwaitingDimension.RMA}


@pytest.mark.parametrize(
    "method",
    [
        NormalizedReturnMethod.NO_PHYSICAL_RETURN.value,
        NormalizedReturnMethod.CUSTOMER_KEEP.value,
        NormalizedReturnMethod.FIELD_SCRAP.value,
    ],
)
def test_a_method_with_no_physical_leg_completes_on_the_rma_alone(method: str) -> None:
    """A hardcoded "needs tracking and label" rule would hang these forever."""
    assessment = resolve_completion(
        approved_case(returnRecords=(record(method=method),)), requirements=BASELINE
    )
    assert assessment.awaiting == ()
    assert assessment.completion_profile_resolved
    assert assessment.business_complete
    assert not assessment.is_terminal


def test_a_prepaid_parcel_completes_on_rma_label_and_tracking() -> None:
    assessment = resolve_completion(approved_case(), requirements=BASELINE)
    assert assessment.awaiting == ()
    assert assessment.business_complete


def test_the_prepaid_parcel_sequence_drains_awaiting_one_signal_at_a_time() -> None:
    """Gate 4, as the plan states it: RMA, then tracking, then label."""
    method = NormalizedReturnMethod.PREPAID_PARCEL.value

    rma_only = approved_case(returnRecords=(record(method=method),))
    assert set(resolve_completion(rma_only, requirements=BASELINE).awaiting) == {
        AwaitingDimension.TRACKING,
        AwaitingDimension.LABEL,
    }

    with_tracking = approved_case(
        returnRecords=(record(method=method, shipments=(shipment(tracking="TRK-1"),)),)
    )
    assert resolve_completion(with_tracking, requirements=BASELINE).awaiting == (
        AwaitingDimension.LABEL,
    )

    with_label = approved_case(
        returnRecords=(
            record(
                method=method,
                shipments=(shipment(tracking="TRK-1"),),
                artifacts=(artifact(ReturnArtifactType.SHIPPING_LABEL, shipment_id="SHP-1"),),
            ),
        )
    )
    complete = resolve_completion(with_label, requirements=BASELINE)
    assert complete.awaiting == ()
    assert complete.business_complete


def test_an_offsite_freight_return_waits_on_bol_pickup_and_location() -> None:
    method = NormalizedReturnMethod.OFFSITE_LTL.value
    waiting = approved_case(returnRecords=(record(method=method, shipments=(shipment(),)),))
    assert set(resolve_completion(waiting, requirements=BASELINE).awaiting) == {
        AwaitingDimension.BOL,
        AwaitingDimension.PICKUP,
        AwaitingDimension.RETURN_LOCATION,
    }

    done = approved_case(
        returnRecords=(
            record(
                method=method,
                location="DC-7",
                shipments=(shipment(),),
                artifacts=(artifact(ReturnArtifactType.BILL_OF_LADING, shipment_id="SHP-1"),),
            ),
        ),
        pickup=scheduled_pickup(),
    )
    assert resolve_completion(done, requirements=BASELINE).business_complete


def test_a_parcel_label_does_not_satisfy_a_bill_of_lading() -> None:
    state = approved_case(
        returnRecords=(
            record(
                method=NormalizedReturnMethod.BRANCH_LTL.value,
                shipments=(shipment(),),
                artifacts=(artifact(ReturnArtifactType.SHIPPING_LABEL, shipment_id="SHP-1"),),
            ),
        ),
        pickup=scheduled_pickup(),
    )
    assert resolve_completion(state, requirements=BASELINE).awaiting == (AwaitingDimension.BOL,)


def test_a_pickup_that_is_only_requested_is_not_scheduled() -> None:
    state = approved_case(
        returnRecords=(
            record(
                method=NormalizedReturnMethod.BRANCH_LTL.value,
                shipments=(shipment(),),
                artifacts=(artifact(ReturnArtifactType.BILL_OF_LADING, shipment_id="SHP-1"),),
            ),
        ),
        pickup=PickupProjection(contactName="Ada", status="REQUESTED"),
    )
    assert resolve_completion(state, requirements=BASELINE).awaiting == (AwaitingDimension.PICKUP,)


def test_a_second_unlabelled_package_keeps_the_case_open() -> None:
    """Every package, not any package. `labels[0]` in a different shape."""
    state = approved_case(
        returnRecords=(
            record(
                shipments=(
                    parcel_shipment("SHP-1"),
                    shipment("SHP-2", tracking="TRK-2"),
                ),
                artifacts=(parcel_label("SHP-1"),),
            ),
        )
    )
    assert resolve_completion(state, requirements=BASELINE).awaiting == (AwaitingDimension.LABEL,)


def test_one_packages_label_does_not_paper_another() -> None:
    """Two packages, two labels, and neither borrows the other's.

    The regression this guards is the one having two homes for a document
    invited: a label reachable from the wrong shipment completes a parcel that
    has nothing on it.
    """
    both = approved_case(
        returnRecords=(
            record(
                shipments=(parcel_shipment("SHP-1"), parcel_shipment("SHP-2")),
                artifacts=(parcel_label("SHP-1"), parcel_label("SHP-2")),
            ),
        )
    )
    assert resolve_completion(both, requirements=BASELINE).business_complete

    misattributed = approved_case(
        returnRecords=(
            record(
                shipments=(parcel_shipment("SHP-1"), parcel_shipment("SHP-2")),
                # Two labels, both naming the first package. The second parcel
                # still has nothing on it.
                artifacts=(
                    parcel_label("SHP-1"),
                    artifact(
                        ReturnArtifactType.SHIPPING_LABEL,
                        artifact_id="LBL-SHP-2",
                        shipment_id="SHP-1",
                    ),
                ),
            ),
        )
    )
    assert resolve_completion(misattributed, requirements=BASELINE).awaiting == (
        AwaitingDimension.LABEL,
    )


def test_a_superseded_label_does_not_satisfy_the_label_requirement() -> None:
    state = approved_case(
        returnRecords=(
            record(
                shipments=(shipment(tracking="TRK-1"),),
                artifacts=(
                    artifact(
                        ReturnArtifactType.SHIPPING_LABEL,
                        artifact_id="LBL-1",
                        shipment_id="SHP-1",
                        superseded_by="LBL-2",
                    ),
                ),
            ),
        )
    )
    assert resolve_completion(state, requirements=BASELINE).awaiting == (AwaitingDimension.LABEL,)


def test_awaiting_is_stable_in_declaration_order() -> None:
    state = approved_case(returnRecords=(record(method=NormalizedReturnMethod.OFFSITE_LTL.value),))
    awaiting = resolve_completion(state, requirements=BASELINE).awaiting
    assert list(awaiting) == sorted(awaiting, key=AWAITING_DIMENSION_ORDER.index)


def test_settlement_never_enters_awaiting_and_never_blocks_completion() -> None:
    state = approved_case(settlement=SettlementProjection(status=SettlementStatus.NOT_INTEGRATED))
    assessment = resolve_completion(state, requirements=BASELINE)
    assert assessment.awaiting == ()
    assert assessment.business_complete


# --------------------------------------------------------------------------
# 6.4 -- the false-completion guard
# --------------------------------------------------------------------------


def _fully_satisfied(**overrides: Any) -> CaseProjectionState:
    """Everything a `PREPAID_PARCEL` return needs, so only the guard can fail it."""
    return approved_case(**overrides)


def test_the_guard_baseline_actually_completes() -> None:
    """Without this, every guard test below could be passing for the wrong reason."""
    assert resolve_completion(_fully_satisfied(), requirements=BASELINE).business_complete


@pytest.mark.parametrize(
    ("label", "state"),
    [
        (
            "approval required",
            case(
                status=ReturnCaseStatus.AWAITING_POLICY_REVIEW,
                policyEvaluation=evaluation(EligibilityDecision.REVIEW_REQUIRED),
                returnRecords=(parcel_record(),),
            ),
        ),
        (
            "recovery required",
            _fully_satisfied(status=ReturnCaseStatus.RECOVERY_REQUIRED),
        ),
        (
            "return method missing",
            approved_case(returnRecords=(parcel_record(method=None),)),
        ),
        (
            "return method UNKNOWN",
            approved_case(returnRecords=(parcel_record(method="UNKNOWN"),)),
        ),
        (
            "no rma at all",
            case(
                status=ReturnCaseStatus.PROCESSING_RETURN,
                policyEvaluation=evaluation(EligibilityDecision.APPROVE),
            ),
        ),
        (
            "warranty verification pending",
            case(
                status=ReturnCaseStatus.AWAITING_SUPPORT,
                policyEvaluation=evaluation(None, route=PolicyRoute.WARRANTY),
            ),
        ),
        (
            "delivery claim verification pending",
            case(
                status=ReturnCaseStatus.AWAITING_SUPPORT,
                policyEvaluation=evaluation(None, route=PolicyRoute.DELIVERY_CLAIM),
            ),
        ),
    ],
)
def test_unresolved_but_active_cases_are_never_complete(
    label: str, state: CaseProjectionState
) -> None:
    """Awaiting non-empty, `isTerminal` false, `businessComplete` false."""
    assessment = resolve_completion(state, requirements=BASELINE)
    assert assessment.awaiting, label
    assert not assessment.is_terminal, label
    assert not assessment.business_complete, label


@pytest.mark.parametrize(
    "status",
    sorted(UNSUCCESSFUL_TERMINAL_STATUSES),
)
def test_terminal_unsuccessful_cases_are_never_complete(status: ReturnCaseStatus) -> None:
    """Awaiting may be empty; `isTerminal` is true; `businessComplete` is false.

    The cancelled case is the sharp one: it satisfies every requirement
    `PREPAID_PARCEL` has, so only `COMPLETION_FORBIDDING_STATUSES` stops it
    reporting itself as a completed return.
    """
    assessment = resolve_completion(_fully_satisfied(status=status), requirements=BASELINE)
    assert assessment.awaiting == ()
    assert assessment.is_terminal
    assert not assessment.business_complete
    assert status in COMPLETION_FORBIDDING_STATUSES


@pytest.mark.parametrize("status", sorted(SUCCESSFUL_TERMINAL_STATUSES))
def test_a_successful_terminal_case_is_complete_and_terminal(status: ReturnCaseStatus) -> None:
    assessment = resolve_completion(_fully_satisfied(status=status), requirements=BASELINE)
    assert assessment.awaiting == ()
    assert assessment.is_terminal
    assert assessment.business_complete


def test_recovery_required_is_awaited_rather_than_terminal() -> None:
    """A case about to resume must not stop the client polling."""
    assessment = resolve_completion(
        _fully_satisfied(status=ReturnCaseStatus.RECOVERY_REQUIRED), requirements=BASELINE
    )
    assert assessment.awaiting == (AwaitingDimension.RECOVERY,)
    assert not assessment.is_terminal
    assert not assessment.business_complete


def test_a_rejected_case_is_terminal_and_awaits_nothing() -> None:
    assessment = resolve_completion(
        case(
            status=ReturnCaseStatus.POLICY_REJECTED,
            policyEvaluation=evaluation(EligibilityDecision.REJECT),
        ),
        requirements=BASELINE,
    )
    assert assessment.awaiting == ()
    assert assessment.is_terminal
    assert not assessment.business_complete


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        (PolicyRoute.WARRANTY, AwaitingDimension.WARRANTY_VERIFICATION),
        (PolicyRoute.DELIVERY_CLAIM, AwaitingDimension.DELIVERY_CLAIM_VERIFICATION),
    ],
)
def test_a_verification_route_awaits_only_its_verification(
    route: PolicyRoute, expected: AwaitingDimension
) -> None:
    """Not `POLICY`: the evaluator did decide, and what it decided was Support's to verify."""
    assessment = resolve_completion(
        case(
            status=ReturnCaseStatus.AWAITING_SUPPORT, policyEvaluation=evaluation(None, route=route)
        ),
        requirements=BASELINE,
    )
    assert assessment.awaiting == (expected,)
    assert not assessment.is_terminal
    assert not assessment.business_complete


# --------------------------------------------------------------------------
# D3 -- a verification route is a hand-off, never a dead end
#
# The two routes carry `decision: null` by construction, so
# `effectiveDecision == APPROVE` is unreachable for them. A completion rule
# that only knew that test raised the verification dimension unconditionally
# and cleared it with nothing: a warranty case with its RMA, its label and its
# tracking number read `awaiting: [WARRANTY_VERIFICATION]` and
# `businessComplete: false` for the rest of its life, which is exactly the
# terminal route that not adding `ROUTED_WARRANTY` was meant to prevent.
#
# What clears it is Support's answer, recorded as the authorization it issued.
# Nothing else can put an RMA on a case routed to a verification queue.
# --------------------------------------------------------------------------

VERIFICATION_ROUTES = [PolicyRoute.WARRANTY, PolicyRoute.DELIVERY_CLAIM]


def verified_case(route: PolicyRoute, **overrides: Any) -> CaseProjectionState:
    """A routed case Support has answered: the RMA is the recorded verification.

    Deliberately the same artifacts as `approved_case` -- one RMA, one fully
    papered parcel -- so the A/B against the standard route compares completion
    and nothing else.
    """
    defaults: dict[str, Any] = {
        "status": ReturnCaseStatus.PROCESSING_RETURN,
        "policyEvaluation": evaluation(None, route=route),
        "returnRecords": (parcel_record(),),
    }
    return case(**{**defaults, **overrides})


@pytest.mark.parametrize("route", VERIFICATION_ROUTES)
def test_a_verified_route_completes_exactly_as_the_standard_route(route: PolicyRoute) -> None:
    """Identical artifact completeness, identical answer. That is the whole fix.

    Asserted as an A/B rather than as three literals, because the claim is not
    "a warranty case can complete" but "a verified warranty case is the standard
    route": the same RMA, label and tracking produce the same assessment.
    """
    standard = resolve_completion(approved_case(), requirements=BASELINE)
    verified = resolve_completion(verified_case(route), requirements=BASELINE)

    assert verified == standard
    assert verified.awaiting == ()
    assert verified.business_complete is True
    assert verified.completion_profile_resolved is True


@pytest.mark.parametrize("route", VERIFICATION_ROUTES)
def test_an_unverified_route_still_waits_on_its_verification(route: PolicyRoute) -> None:
    """The other half. Clearing it unconditionally would be the opposite defect."""
    assessment = resolve_completion(verified_case(route, returnRecords=None), requirements=BASELINE)
    assert assessment.awaiting == (ROUTE_VERIFICATION_DIMENSIONS[route],)
    assert assessment.business_complete is False


@pytest.mark.parametrize("route", VERIFICATION_ROUTES)
def test_a_record_without_an_authorization_does_not_verify_a_claim(route: PolicyRoute) -> None:
    """`create_return_record` can mint a `DRAFT` row with no RMA on it.

    A placeholder is not an authorization, so it is not Support's answer either
    -- read the same way `_rma_satisfied` reads it.
    """
    assessment = resolve_completion(
        verified_case(route, returnRecords=(parcel_record(reference=None),)),
        requirements=BASELINE,
    )
    assert assessment.awaiting == (ROUTE_VERIFICATION_DIMENSIONS[route],)
    assert assessment.business_complete is False


@pytest.mark.parametrize("route", VERIFICATION_ROUTES)
def test_a_verified_route_with_no_method_awaits_the_method_and_never_policy(
    route: PolicyRoute,
) -> None:
    """Verified, and still unresolved -- but for the reason a standard case would be.

    `POLICY` must not appear: the evaluator decided, and what it decided was
    that Support verifies this. `route_authority_stands` is what keeps the two
    apart now that the early return is conditional.
    """
    assessment = resolve_completion(
        verified_case(route, returnRecords=(parcel_record(method="UNKNOWN"),)),
        requirements=BASELINE,
    )
    assert assessment.awaiting == (AwaitingDimension.RETURN_METHOD,)
    assert AwaitingDimension.POLICY not in assessment.awaiting
    assert ROUTE_VERIFICATION_DIMENSIONS[route] not in assessment.awaiting


@pytest.mark.parametrize("route", VERIFICATION_ROUTES)
def test_a_verified_route_rejoins_the_requirement_table(route: PolicyRoute) -> None:
    """An RMA with no paperwork owes what the table says it owes, not a verification."""
    assessment = resolve_completion(
        verified_case(route, returnRecords=(record(),)), requirements=BASELINE
    )
    assert set(assessment.awaiting) == {AwaitingDimension.LABEL, AwaitingDimension.TRACKING}
    assert assessment.business_complete is False


@pytest.mark.parametrize("route", VERIFICATION_ROUTES)
def test_a_cancelled_verification_route_is_still_forbidden_from_completing(
    route: PolicyRoute,
) -> None:
    """The status guard outranks the verification, exactly as it outranks an APPROVE."""
    assessment = resolve_completion(
        verified_case(route, status=ReturnCaseStatus.CANCELLED), requirements=BASELINE
    )
    assert assessment.awaiting == ()
    assert assessment.business_complete is False
    assert assessment.is_terminal is True


def test_every_verification_dimension_has_a_route_that_raises_it() -> None:
    """No orphan dimension, and no route mapped to a requirement dimension.

    A verification dimension no route produces could never be cleared either,
    which is the shape of the defect this table replaced.
    """
    assert set(ROUTE_VERIFICATION_DIMENSIONS.values()) == {
        AwaitingDimension.WARRANTY_VERIFICATION,
        AwaitingDimension.DELIVERY_CLAIM_VERIFICATION,
    }
    assert set(ROUTE_VERIFICATION_DIMENSIONS.values()) <= UNRESOLVED_DIMENSIONS
    assert PolicyRoute.STANDARD_RETURN not in ROUTE_VERIFICATION_DIMENSIONS
    assert set(ROUTE_VERIFICATION_DIMENSIONS) == set(PolicyRoute) - {PolicyRoute.STANDARD_RETURN}


def test_an_unevaluated_case_awaits_both_policy_and_method() -> None:
    assessment = resolve_completion(case(), requirements=BASELINE)
    assert assessment.awaiting == (AwaitingDimension.POLICY, AwaitingDimension.RETURN_METHOD)


# --------------------------------------------------------------------------
# A gate the operator has switched off
# --------------------------------------------------------------------------


def gate_suspended(*, reason: str = "eligibility gate suspended") -> CaseFactProjection:
    """What `EvaluateCaseEligibility` writes when the gate is off.

    Note what is *not* here: no evaluation, no route, no decision. A suspended
    gate records none of those on purpose, so this fact is the only thing on the
    case that distinguishes "the operator switched it off" from "it has not run
    yet" -- which is why the completion profile has to read it.
    """
    assert reason
    return fact(
        fact_id="F-POLICY-STATE",
        fact_name=POLICY_GATE_STATE_FACT,
        value=POLICY_GATE_SUSPENDED,
    )


def test_the_projection_and_the_workflow_mean_the_same_thing_by_a_suspended_gate() -> None:
    """The two constants are declared in different layers, and must not drift.

    `PolicyGateState` lives in the workflow module; the projection cannot import
    it without inverting the dependency and pulling `temporalio` into every
    reader of a case, so it names the strings itself. This is the seam that
    catches a rename -- the only alternative to which is nobody noticing until a
    suspended gate silently starts blocking completion again.
    """
    from return_platform.workflows.return_case_workflow import PolicyGateState

    assert POLICY_GATE_SUSPENDED == PolicyGateState.SKIPPED_BY_CONFIGURATION.value


def test_a_suspended_gate_is_not_awaited() -> None:
    """The defect, as the thing that was not true.

    `policy_evaluation.enabled = false` means no decision will ever be recorded,
    so reading the missing approval as "not yet" left the case waiting on POLICY
    for the rest of its life -- and Support's console printed *Waiting on
    POLICY* directly under *Policy Evaluation: Skipped by configuration*.
    """
    assessment = resolve_completion(case(facts=(gate_suspended(),)), requirements=BASELINE)

    assert AwaitingDimension.POLICY not in assessment.awaiting
    # The method is genuinely unresolved on a bare case, and stays reported.
    assert assessment.awaiting == (AwaitingDimension.RETURN_METHOD,)


def test_a_suspended_gate_lets_a_fulfilled_return_complete() -> None:
    """The consequence, which is the part that mattered: `businessComplete` was
    unreachable however fully the return was fulfilled."""
    state = case(
        status=ReturnCaseStatus.PROCESSING_RETURN,
        returnRecords=(parcel_record(),),
        facts=(gate_suspended(),),
    )
    assessment = resolve_completion(state, requirements=BASELINE)

    assert assessment.completion_profile_resolved
    assert assessment.awaiting == ()
    assert assessment.business_complete


def test_a_suspended_gate_is_not_an_approval() -> None:
    """Proceeding is not a verdict.

    The case carries no evaluation and no decision, and this fix does not give
    it one. What changed is that the profile stops waiting for an answer nobody
    will give -- not that an answer appeared.
    """
    state = case(facts=(gate_suspended(),))

    assert policy_gate_suspended(state)
    assert state.policyEvaluation is None
    assert effective_decision(state) is None


def test_any_other_gate_state_is_still_awaited() -> None:
    """Only the suspension clears it. `EVALUATION_FAILED` and
    `POLICY_NOT_CONFIGURED` are operational failures that park or hold the case,
    and reading either as authority would let a case nobody evaluated complete."""
    for state_value in ("EVALUATED", "EVALUATION_FAILED", "POLICY_NOT_CONFIGURED"):
        state = case(
            facts=(
                fact(
                    fact_id="F-POLICY-STATE",
                    fact_name=POLICY_GATE_STATE_FACT,
                    value=state_value,
                ),
            )
        )
        assessment = resolve_completion(state, requirements=BASELINE)
        assert not policy_gate_suspended(state), state_value
        assert AwaitingDimension.POLICY in assessment.awaiting, state_value


def test_a_verification_route_is_unaffected_by_the_gate_fact() -> None:
    """A warranty case reports its verification, not `POLICY`, and a stray gate
    fact must not turn Support's verification into something already satisfied."""
    state = case(
        policyEvaluation=evaluation(None, route=PolicyRoute.WARRANTY),
        facts=(gate_suspended(),),
    )
    assessment = resolve_completion(state, requirements=BASELINE)

    assert assessment.awaiting == (AwaitingDimension.WARRANTY_VERIFICATION,)
    assert not assessment.business_complete


# --------------------------------------------------------------------------
# 6.4 -- effectiveDecision, never originalDecision
# --------------------------------------------------------------------------


def test_an_overridden_review_resolves_the_completion_profile() -> None:
    state = approved_case(
        policyEvaluation=evaluation(
            EligibilityDecision.REVIEW_REQUIRED, override=supervisor_override()
        )
    )
    assessment = resolve_completion(state, requirements=BASELINE)
    assert state.policyEvaluation is not None
    assert state.policyEvaluation.originalDecision is EligibilityDecision.REVIEW_REQUIRED
    assert assessment.completion_profile_resolved
    assert assessment.business_complete


def test_an_un_overridden_review_does_not_resolve_the_completion_profile() -> None:
    state = approved_case(policyEvaluation=evaluation(EligibilityDecision.REVIEW_REQUIRED))
    assessment = resolve_completion(state, requirements=BASELINE)
    assert not assessment.completion_profile_resolved
    # The method is known, so only the policy is outstanding.
    assert assessment.awaiting == (AwaitingDimension.POLICY,)
    assert not assessment.business_complete


def test_an_override_to_reject_does_not_complete_an_approved_evaluation() -> None:
    state = approved_case(
        policyEvaluation=evaluation(
            EligibilityDecision.APPROVE,
            override=supervisor_override(EligibilityDecision.REJECT),
        )
    )
    assessment = resolve_completion(state, requirements=BASELINE)
    assert not assessment.completion_profile_resolved
    assert assessment.awaiting == (AwaitingDimension.POLICY,)


# --------------------------------------------------------------------------
# project_case -- the derived block agrees with the state it came from
# --------------------------------------------------------------------------


def test_project_case_derives_all_four_values() -> None:
    projection = project_case(approved_case(), requirements=BASELINE)
    assert projection.stage is CopilotStage.AUTHORIZED_RMA
    assert projection.awaiting == ()
    assert projection.businessComplete
    assert not projection.isTerminal
    assert projection.caseId == "CASE-1"
    assert projection.revision == 3


def test_project_case_preserves_absence() -> None:
    projection = project_case(case(), requirements=BASELINE)
    assert projection.customer is None
    assert projection.returnRecords is None
    assert projection.settlement is None
    assert projection.stage is CopilotStage.DISCOVERY


def test_project_case_agrees_with_the_functions_it_composes() -> None:
    for _, state in _LIFECYCLE:
        projection = project_case(state, requirements=BASELINE)
        assessment = resolve_completion(state, requirements=BASELINE)
        assert projection.stage is derive_copilot_stage(state)
        assert projection.awaiting == assessment.awaiting
        assert projection.businessComplete is assessment.business_complete
        assert projection.isTerminal is assessment.is_terminal


def test_project_case_cannot_be_called_without_a_requirement_table() -> None:
    """The wiring defect this closed cannot be reintroduced by omission.

    `project_case` kept a default of `DEFAULT_RETURN_METHOD_REQUIREMENTS` long
    after `resolve_completion` had lost one, and the workflow's completion read
    used it -- so "keep waiting or close this case" was decided by a code
    constant while the API answered from the operator's release. The two agreed
    row for row, so nothing failed; the first edit to
    `return_policy.return_method_requirements` would have split them.

    Asserted as a signature property rather than through a behaviour, because
    the failure mode was precisely that the behaviour looked right.
    """
    with pytest.raises(TypeError):
        project_case(approved_case())  # type: ignore[call-arg]

    assert (
        inspect.signature(project_case).parameters["requirements"].default
        is inspect.Parameter.empty
    )


def test_an_approved_item_records_the_quantity_it_authorized() -> None:
    item = ApprovedItemProjection(
        returnItemId="RI-1", orderLineReference="L1", quantityApproved=2, disposition="RTV"
    )
    assert item.quantityApproved == 2
    assert item.itemStatus is None
