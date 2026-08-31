"""Assembling the Copilot projection from persistence, and backfilling what predates it.

`test_case_projection.py` proves the *rules* over a `CaseProjectionState`. This
module proves the state is a faithful reading of what MongoDB holds, which is
the half the audit found nothing had been checked at all -- the console
fabricated `TRK-98421049281` because the read path could not tell a tracking
number it did not have from one that was null.

Seven properties, each one a defect that was live:

* **Absence survives the trip.** A case with no RMA, no shipment and no policy
  projects `None` for those blocks. Not an empty object, not an object of
  nulls -- the shape that says "we have a shipment and know nothing about it".
* **The legacy single-value fields project into the plural shapes.** A record
  with a label and a null tracking reference -- the real `4e372a39...` shape in
  the dev database -- yields one artifact on `returnRecords[].artifacts[]` and
  *zero* shipments. No package is invented to carry the label, and the label is
  no longer unreachable for want of one.
* **Multi-RMA attribution holds.** Two records with two labels and two tracking
  numbers keep them, and neither record acquires the other's.
* **The status mapping is total and loud.** Every member of the persisted
  `CaseStatus` has a reading; anything else raises rather than defaulting.
* **`CLOSED` is two endings and the projection can tell them apart.**
  `_close_business_complete` and a Support refusal write the same persisted
  value, so the `support_outcome` fact decides: a refused claim projects
  `POLICY_REJECTED` and awaits nothing, where it used to project
  `COMPLETED_EXTERNAL_SETTLEMENT` -- a credit settled elsewhere -- while still
  naming the verification the refusal had answered.
* **The backfill is idempotent.** The second run produces an empty plan, which
  is a stronger statement than "the second run wrote the same values".
* **A field with no producer is `None`, and settlement says so out loud.**
  Warehouse projects the three bay fields `ReturnCaseWorkflow` writes and leaves
  the seven receipt fields absent; settlement is `NOT_INTEGRATED` on every case,
  which is why a closed one reaches `COMPLETED_EXTERNAL_SETTLEMENT`. The
  `WAREHOUSE_RECEIVING` stage is asserted unreachable rather than assumed to be.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.configuration.return_configuration import (
    build_return_method_requirement_table,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.operations.case_projection import (
    AwaitingDimension,
    CopilotStage,
    ReturnArtifactType,
    ReturnCaseStatus,
    SettlementProjection,
    SettlementStatus,
    ShipmentStatus,
    SupportOutcome,
    project_case,
)
from return_platform.operations.case_projection.assembly import (
    SUPPORT_OUTCOME_FACT,
    SUPPORT_OUTCOME_REASON_FACT,
    CaseAggregateDocuments,
    assemble_case_projection_state,
    project_facts,
    project_policy_evaluation,
    project_return_artifacts,
    project_return_record,
    project_shipments,
    project_support,
    project_support_outcome,
)
from return_platform.operations.case_projection.backfill import (
    CaseBackfillAction,
    plan_case_backfill,
)
from return_platform.operations.case_projection.status_mapping import (
    UnmappedCaseStatusError,
    project_case_status,
)
from return_platform.operations.case_repository import CaseRepository
from return_platform.operations.models import CaseStatus
from return_platform.policy import EligibilityDecision, PolicyRoute
from return_platform.workflows.stage_results import FulfillmentTrackingStatus

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

#: The shipped release's requirement table, passed at every `project_case` call.
#:
#: There is no default to fall back on, by design -- see
#: `operations/case_projection/projection.py`. The *released* table rather than
#: `DEFAULT_RETURN_METHOD_REQUIREMENTS` because the completion assertions below
#: (a `PREPAID_PARCEL` case waiting on `LABEL` and `TRACKING`) are meant as
#: statements about what production does with an assembled case, and the code
#: constant is not what production reads.
RELEASED_REQUIREMENTS = build_return_method_requirement_table(
    load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
)

#: The record the dev database actually holds: an RMA, a label, and no tracking.
REAL_RECORD_ID = "4e372a39-1c1e-4f4c-9a2b-6f0f2b7d1a55"


# ---------------------------------------------------------------------------
# Document builders. Every one is the *minimum* document that makes its clause
# true, so a passing test says the clause did it.
# ---------------------------------------------------------------------------


def case_document(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "caseId": "CASE-1",
        "tenantId": "acme",
        "principalId": "assoc-1",
        "branchId": None,
        "status": CaseStatus.GATHERING_INFO.value,
        "channelAConversationId": "CONV-1",
        "channelBWorkItemId": None,
        "confirmedOrderReference": None,
        "confirmationKey": None,
        "version": 3,
        # Naive, exactly as pymongo hands it back.
        "createdAt": datetime(2026, 8, 15, 11, 0),
        "updatedAt": datetime(2026, 8, 15, 12, 0),
    }
    return {**base, **overrides}


def record_document(record_id: str = "RR-1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "returnRecordId": record_id,
        "caseId": "CASE-1",
        "returnReference": None,
        "status": "DRAFT",
        "returnLocation": None,
        "trackingReference": None,
        "labelReference": None,
        "shippingInstructionReference": None,
        "version": 0,
        "createdAt": datetime(2026, 8, 15, 11, 30),
        "updatedAt": datetime(2026, 8, 15, 11, 45),
    }
    return {**base, **overrides}


def item_document(item_id: str = "ITEM-1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "returnItemId": item_id,
        "caseId": "CASE-1",
        "returnRecordId": None,
        "orderLineId": "LINE-1",
        "productReference": "SKU-1",
        "quantity": 2,
        "reason": None,
        "condition": None,
        "packageReference": None,
        "version": 0,
        "createdAt": datetime(2026, 8, 15, 11, 10),
        "updatedAt": datetime(2026, 8, 15, 11, 10),
    }
    return {**base, **overrides}


def fact_document(name: str, value: Any, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "factId": f"{name}-1",
        "caseId": "CASE-1",
        "factName": name,
        "value": value,
        "agentId": "test-agent",
        "channel": "SYSTEM",
        "turnId": None,
        "sourceSystem": None,
        "sourcePath": None,
        "acquisitionMethod": "DERIVED",
        "observedAt": datetime(2026, 8, 15, 11, 55),
        "recordedAt": datetime(2026, 8, 15, 11, 55),
        "supersedesFactId": None,
        "correlationId": None,
    }
    return {**base, **overrides}


def latest(*facts: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The shape `CaseRepository.latest_case_facts` returns."""
    return {str(fact["factName"]): dict(fact) for fact in facts}


def policy_facts(
    *,
    route: str = PolicyRoute.STANDARD_RETURN.value,
    decision: str | None = EligibilityDecision.APPROVE.value,
    extra: Iterable[Mapping[str, Any]] = (),
) -> dict[str, dict[str, Any]]:
    documents = [
        fact_document("policy_route", route),
        fact_document("policy_id", "FERGUSON_RETURNS"),
        fact_document("policy_version", "2025.05"),
        fact_document("policy_evaluated_at", "2026-08-15T11:50:00+00:00"),
    ]
    if decision is not None:
        documents.append(fact_document("policy_decision", decision))
        documents.append(fact_document("policy_effective_decision", decision))
    documents.extend(dict(entry) for entry in extra)
    return latest(*documents)


# ---------------------------------------------------------------------------
# 1. Absence is data.
# ---------------------------------------------------------------------------


def test_a_bare_case_projects_every_optional_block_as_absent() -> None:
    """No RMA, no shipment, no policy -- and no empty object standing in for one."""
    state = assemble_case_projection_state(CaseAggregateDocuments(case=case_document()))

    assert state.returnRecords is None
    assert state.all_shipments() == ()
    assert state.policyEvaluation is None
    assert state.customer is None
    assert state.confirmedOrder is None
    assert state.selectedItems is None
    assert state.facts is None
    assert state.support is None
    assert state.pickup is None
    assert state.warehouse is None

    # Settlement is the one block that is *stated* rather than omitted, and it
    # is not an exception to the rule above -- `status` is required and holds a
    # real answer, so this is not the "object of nulls" shape. `None` would say
    # "not computed"; there is nothing to compute, and saying so is the point.
    assert state.settlement is not None
    assert state.settlement.status is SettlementStatus.NOT_INTEGRATED
    assert state.settlement.creditMemoReference is None
    assert state.settlement.settledAmount is None
    assert state.settlement.settledAt is None

    assert state.caseId == "CASE-1"
    assert state.conversationId == "CONV-1"
    assert state.revision == 3
    assert state.status is ReturnCaseStatus.GATHERING_INFO
    # Naive persistence timestamps arrive aware; the contract requires it.
    assert state.updatedAt == NOW
    assert state.updatedAt.tzinfo is not None


def test_a_record_with_nothing_on_it_projects_no_shipment_and_no_artifacts() -> None:
    """An RMA that exists is a record. It is not evidence of a package."""
    record = project_return_record(record_document(returnReference="RMA-1"))

    assert record is not None
    assert record.returnReference == "RMA-1"
    assert record.shipments is None
    assert record.active_shipments() == ()


def test_blank_strings_read_as_absent() -> None:
    """`returnReference: ""` would satisfy "an RMA exists" and complete a case that has none."""
    record = project_return_record(
        record_document(returnReference="   ", trackingReference="", labelReference="")
    )

    assert record is not None
    assert record.returnReference is None
    assert record.shipments is None
    assert project_return_artifacts(record_document(labelReference="  ")) is None


def test_an_empty_customer_block_is_never_built_but_a_branch_alone_builds_one() -> None:
    without = assemble_case_projection_state(CaseAggregateDocuments(case=case_document()))
    with_branch = assemble_case_projection_state(
        CaseAggregateDocuments(case=case_document(branchId="BR-14"))
    )

    assert without.customer is None
    assert with_branch.customer is not None
    assert with_branch.customer.branchReference == "BR-14"
    assert with_branch.customer.customerReference is None


# ---------------------------------------------------------------------------
# 2. The legacy single-value fields.
# ---------------------------------------------------------------------------


def real_label_without_tracking_document() -> dict[str, Any]:
    """`4e372a39-882a-4617-b2c8-60c14e094c64` as the dev database holds it.

    `RMA-OPS01-CD4364`, `labelReference: LBL-OPS01`, `trackingReference: null`.
    The state a Copilot was found stuck in.
    """
    return record_document(
        REAL_RECORD_ID,
        returnReference="RMA-OPS01-CD4364",
        labelReference="LBL-OPS01",
        trackingReference=None,
    )


def test_a_label_with_null_tracking_is_visible_on_the_projected_record() -> None:
    """The real `4e372a39...` shape, asserted on the projection rather than a helper.

    This is the assertion the contract could not previously carry: artifacts
    were reachable only through a shipment, and this record has none. The label
    now lands on `returnRecords[].artifacts[]` attributed to nothing, and no
    package is invented to hold it.
    """
    record = project_return_record(real_label_without_tracking_document())

    assert record is not None
    assert record.returnReference == "RMA-OPS01-CD4364"
    # No package. Absent, not a shipment of nulls.
    assert record.shipments is None
    assert record.active_shipments() == ()

    assert record.artifacts is not None
    assert len(record.artifacts) == 1
    label = record.artifacts[0]
    assert label.artifactId == "LBL-OPS01"
    assert label.artifactType is ReturnArtifactType.SHIPPING_LABEL
    assert label.active is True
    assert label.supersededBy is None
    assert label.is_active
    # No package exists, so the artifact belongs to none. Absent, not invented.
    assert label.shipmentId is None

    active = record.active_artifacts(ReturnArtifactType.SHIPPING_LABEL)
    assert [item.artifactId for item in active] == ["LBL-OPS01"]


def test_the_real_label_without_tracking_case_still_awaits_tracking() -> None:
    """Completion semantics are unchanged by the artifact moving home.

    An approved `PREPAID_PARCEL` RMA with a label and no package awaits both
    `LABEL` and `TRACKING`: the label is on the record, but the `LABEL`
    requirement asks that every package be papered and there is no package.
    """
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(status=CaseStatus.POLICY_APPROVED.value),
            facts=policy_facts(extra=(fact_document("approved_return_method", "PREPAID_PARCEL"),)),
            return_records=(real_label_without_tracking_document(),),
        )
    )
    projected = project_case(state, requirements=RELEASED_REQUIREMENTS)

    assert projected.stage is CopilotStage.AUTHORIZED_RMA
    assert AwaitingDimension.TRACKING in projected.awaiting
    assert AwaitingDimension.LABEL in projected.awaiting
    assert projected.businessComplete is False

    record = projected.records()[0]
    assert record.artifacts is not None
    assert [item.artifactId for item in record.artifacts] == ["LBL-OPS01"]


def test_tracking_projects_one_shipment_and_the_label_names_it() -> None:
    document = record_document(
        returnReference="RMA-1",
        trackingReference="1Z-REAL-0001",
        labelReference="LBL-1",
    )

    shipments = project_shipments(document)

    assert shipments is not None
    assert len(shipments) == 1
    shipment = shipments[0]
    assert shipment.trackingNumber == "1Z-REAL-0001"
    # The parcel's identity is its tracking number, exactly as the artifact's is
    # its label reference. Persistence offers no other handle on a package, and
    # the record id -- which this used to be -- names no particular package once
    # an RMA can carry two.
    assert shipment.shipmentId == "1Z-REAL-0001"
    # This record carries no carrier -- Support said nothing about one -- and
    # nothing in the case aggregate says what the state is. `carrier` has a
    # producer now (`ReturnRecordView.carrier`); it is simply unset here.
    assert shipment.carrier is None
    assert shipment.shipmentStatus is None
    assert shipment.estimatedDeliveryAt is None

    record = project_return_record(document)
    assert record is not None
    assert record.artifacts is not None
    assert [artifact.artifactId for artifact in record.artifacts] == ["LBL-1"]
    # Attribution is the field, and it names the package the record projected.
    assert record.artifacts[0].shipmentId == "1Z-REAL-0001"
    assert (
        record.active_artifacts_for_shipment(ReturnArtifactType.SHIPPING_LABEL, "1Z-REAL-0001")
        == record.artifacts
    )


def test_a_record_with_a_package_and_no_label_carries_no_artifacts() -> None:
    document = record_document(trackingReference="1Z-REAL-0002", labelReference=None)
    record = project_return_record(document)

    assert record is not None
    assert record.shipments is not None
    assert record.artifacts is None
    assert record.active_artifacts(ReturnArtifactType.SHIPPING_LABEL) == ()
    assert project_return_artifacts(document) is None


# ---------------------------------------------------------------------------
# 3. Multi-RMA attribution.
# ---------------------------------------------------------------------------


def test_two_records_keep_their_own_labels_shipments_and_items() -> None:
    """One case, two RMAs. Nothing on one may appear on the other."""
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(),
            return_records=(
                record_document(
                    "RR-1",
                    returnReference="RMA-1",
                    trackingReference="1Z-AAA",
                    labelReference="LBL-A",
                    returnLocation="BRANCH-7",
                ),
                record_document(
                    "RR-2",
                    returnReference="RMA-2",
                    trackingReference="1Z-BBB",
                    labelReference="LBL-B",
                ),
            ),
            return_items=(
                item_document("ITEM-1", returnRecordId="RR-1", orderLineId="LINE-1"),
                item_document("ITEM-2", returnRecordId="RR-2", orderLineId="LINE-2"),
                item_document("ITEM-3", returnRecordId=None, orderLineId="LINE-3"),
            ),
        )
    )

    records = state.records()
    assert [record.returnRecordId for record in records] == ["RR-1", "RR-2"]

    first, second = records
    assert first.returnLocation == "BRANCH-7"
    assert second.returnLocation is None

    assert first.shipments is not None
    assert second.shipments is not None
    assert [shipment.trackingNumber for shipment in first.shipments] == ["1Z-AAA"]
    assert [shipment.trackingNumber for shipment in second.shipments] == ["1Z-BBB"]

    assert first.artifacts is not None
    assert second.artifacts is not None
    assert [a.artifactId for a in first.artifacts] == ["LBL-A"]
    assert [a.artifactId for a in second.artifacts] == ["LBL-B"]
    assert first.artifacts[0].shipmentId == "1Z-AAA"
    assert second.artifacts[0].shipmentId == "1Z-BBB"
    # Each record's label papers its own package and nobody else's.
    assert [
        a.artifactId
        for a in first.active_artifacts_for_shipment(ReturnArtifactType.SHIPPING_LABEL, "1Z-AAA")
    ] == ["LBL-A"]
    assert first.active_artifacts_for_shipment(ReturnArtifactType.SHIPPING_LABEL, "1Z-BBB") == ()
    assert second.active_artifacts_for_shipment(ReturnArtifactType.SHIPPING_LABEL, "1Z-AAA") == ()

    assert first.approvedItems is not None
    assert second.approvedItems is not None
    assert [item.returnItemId for item in first.approvedItems] == ["ITEM-1"]
    assert [item.returnItemId for item in second.approvedItems] == ["ITEM-2"]
    assert first.approvedItems[0].quantityApproved == 2

    # The unassigned line is a selected item and is not folded into a record.
    assert state.selectedItems is not None
    assert [item.returnItemId for item in state.selectedItems] == ["ITEM-3"]


def test_one_record_with_a_label_beside_one_with_tracking_does_not_share_either() -> None:
    """The mixed shape: an RMA still waiting for a package, and one already moving."""
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(),
            return_records=(
                record_document(REAL_RECORD_ID, returnReference="RMA-1", labelReference="LBL-A"),
                record_document("RR-2", returnReference="RMA-2", trackingReference="1Z-BBB"),
            ),
        )
    )

    labelled, shipped = state.records()
    assert labelled.shipments is None
    assert labelled.artifacts is not None
    assert [a.artifactId for a in labelled.artifacts] == ["LBL-A"]
    assert labelled.artifacts[0].shipmentId is None
    assert shipped.shipments is not None
    assert shipped.artifacts is None
    # The unattributed label of the first RMA papers nothing on the second.
    assert shipped.active_artifacts(ReturnArtifactType.SHIPPING_LABEL) == ()
    assert len(state.all_shipments()) == 1


# ---------------------------------------------------------------------------
# 4. The status mapping.
# ---------------------------------------------------------------------------

#: Written out literally rather than derived from the code under test. A table
#: that imported its own answers would assert only that the module is
#: self-consistent.
EXPECTED_STATUS_PROJECTION: dict[CaseStatus, ReturnCaseStatus] = {
    CaseStatus.GATHERING_INFO: ReturnCaseStatus.GATHERING_INFO,
    CaseStatus.AWAITING_BAY: ReturnCaseStatus.PROCESSING_RETURN,
    CaseStatus.POLICY_APPROVED: ReturnCaseStatus.PROCESSING_RETURN,
    CaseStatus.AWAITING_POLICY_REVIEW: ReturnCaseStatus.AWAITING_POLICY_REVIEW,
    CaseStatus.POLICY_REJECTED: ReturnCaseStatus.POLICY_REJECTED,
    CaseStatus.RECOVERY_REQUIRED: ReturnCaseStatus.RECOVERY_REQUIRED,
    CaseStatus.AWAITING_SUPPORT: ReturnCaseStatus.AWAITING_SUPPORT,
    # Waiting on an approval of the message *to* Support reads, from outside, as
    # waiting on Support. `ReturnCaseStatus` is the frozen contract vocabulary,
    # so the review maps onto the wait the caller already sees rather than
    # adding a member to it.
    CaseStatus.AWAITING_TEMPLATE_REVIEW: ReturnCaseStatus.AWAITING_SUPPORT,
    CaseStatus.RMA_RECEIVED: ReturnCaseStatus.PROCESSING_RETURN,
    CaseStatus.IN_TRANSIT: ReturnCaseStatus.PROCESSING_RETURN,
    CaseStatus.CLOSED: ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT,
    CaseStatus.CANCELLED: ReturnCaseStatus.CANCELLED,
}


def test_every_persisted_status_has_a_reading() -> None:
    assert set(EXPECTED_STATUS_PROJECTION) == set(CaseStatus)


@pytest.mark.parametrize(("persisted", "projected"), sorted(EXPECTED_STATUS_PROJECTION.items()))
def test_each_persisted_status_projects_as_expected(
    persisted: CaseStatus, projected: ReturnCaseStatus
) -> None:
    assert project_case_status(persisted) is projected
    # The stored string, which is what Mongo actually hands back.
    assert project_case_status(persisted.value) is projected


def test_closed_splits_on_the_support_answer_before_it_splits_on_settlement() -> None:
    """A refusal is not a settlement, and the settlement question is never asked of it.

    `ReturnCaseWorkflow` writes one `CLOSED` for two opposite endings --
    `_close_business_complete` and `_record_support_outcome` when Support
    rejected -- so the persisted status alone cannot say which happened. The
    `support_outcome` fact is what says it, and it is read first: a refused
    return has no credit for anybody to have settled, so reaching
    `COMPLETED_EXTERNAL_SETTLEMENT` would announce a payment nobody made.
    """
    assert (
        project_case_status(CaseStatus.CLOSED, support_outcome=SupportOutcome.REJECTED)
        is ReturnCaseStatus.POLICY_REJECTED
    )
    # Even with a settlement on file, which is the ordering this asserts.
    assert (
        project_case_status(
            CaseStatus.CLOSED,
            settlement=SettlementProjection(status=SettlementStatus.SETTLED),
            support_outcome=SupportOutcome.REJECTED,
        )
        is ReturnCaseStatus.POLICY_REJECTED
    )
    # An authorization is not a refusal, and neither is silence.
    assert (
        project_case_status(CaseStatus.CLOSED, support_outcome=SupportOutcome.AUTHORIZED)
        is ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT
    )
    assert (
        project_case_status(CaseStatus.CLOSED, support_outcome=None)
        is ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT
    )


@pytest.mark.parametrize(
    "persisted", sorted(set(CaseStatus) - {CaseStatus.CLOSED}, key=lambda status: status.value)
)
def test_only_closed_consults_the_support_answer(persisted: CaseStatus) -> None:
    """A rejection recorded on a case that has not closed changes nothing.

    Support's answer and the workflow's status write are two acts, and a poll
    landing between them must read the case as it is -- still open -- rather
    than as the ending it is about to reach.
    """
    assert project_case_status(
        persisted, support_outcome=SupportOutcome.REJECTED
    ) is project_case_status(persisted)


def test_closed_splits_on_settlement() -> None:
    """A completed return count must never be readable as a settled return count."""
    assert (
        project_case_status(CaseStatus.CLOSED, settlement=None)
        is ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT
    )
    assert (
        project_case_status(
            CaseStatus.CLOSED,
            settlement=SettlementProjection(status=SettlementStatus.NOT_INTEGRATED),
        )
        is ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT
    )
    assert (
        project_case_status(
            CaseStatus.CLOSED,
            settlement=SettlementProjection(status=SettlementStatus.SETTLED),
        )
        is ReturnCaseStatus.COMPLETED
    )


@pytest.mark.parametrize("value", ["PROCESSING_RETURN", "", "closed", None, 7])
def test_an_unmapped_status_raises_rather_than_defaulting(value: object) -> None:
    with pytest.raises(UnmappedCaseStatusError):
        project_case_status(value)


def test_an_unmapped_status_fails_the_whole_assembly() -> None:
    with pytest.raises(UnmappedCaseStatusError):
        assemble_case_projection_state(
            CaseAggregateDocuments(case=case_document(status="SOMETHING_ELSE"))
        )


# ---------------------------------------------------------------------------
# Facts, policy, support, warehouse.
# ---------------------------------------------------------------------------


def test_facts_project_with_their_provenance_in_a_stable_order() -> None:
    facts = project_facts(
        latest(
            fact_document("return_reason", "DAMAGED"),
            fact_document("customer_id", "CUST-9"),
        )
    )

    assert facts is not None
    assert [fact.factName for fact in facts] == ["customer_id", "return_reason"]
    assert facts[0].value == "CUST-9"
    assert facts[0].acquisitionMethod == "DERIVED"
    assert facts[0].recordedAt is not None
    assert facts[0].recordedAt.tzinfo is not None


def test_a_command_fact_carries_the_principal_that_authorised_it() -> None:
    """The actor reaches the REST view, and it is not the agent.

    Asserted as a pair rather than as `actorId is not None`, because a
    projection that stamped the agent id into both fields would satisfy the
    weaker check while destroying the distinction the field exists for: the
    agent is what produced the observation, the actor is on whose authority a
    command caused it.
    """
    facts = project_facts(
        latest(
            fact_document("support_clarification_answered", "yes", actorId="associate-7"),
            fact_document("return_reason", "DAMAGED"),
        )
    )

    assert facts is not None
    by_name = {fact.factName: fact for fact in facts}
    answered = by_name["support_clarification_answered"]
    assert (answered.agentId, answered.actorId) == ("test-agent", "associate-7")
    # An observation has no actor at all, and `None` is the honest projection of
    # that -- not a gap to be filled with the agent.
    assert by_name["return_reason"].actorId is None


def test_a_non_scalar_fact_is_carried_as_deterministic_json_rather_than_dropped() -> None:
    """`confirmed_order_lines` holds a list and the contract's value is scalar."""
    facts = project_facts(latest(fact_document("confirmed_order_lines", ["LINE-2", "LINE-1"])))

    assert facts is not None
    assert facts[0].value == '["LINE-2", "LINE-1"]'


def test_the_customer_and_confirmed_order_come_from_the_case_and_its_facts() -> None:
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(
                branchId="BR-14",
                confirmedOrderReference="ORD-77",
                confirmationKey="acme|CONV-1|ORD-77|LINE-1",
            ),
            facts=latest(
                fact_document("customer_id", "CUST-9"),
                fact_document("customer_name", "Acme Plumbing"),
                fact_document("confirmed_order_reference", "ORD-77"),
            ),
        )
    )

    assert state.customer is not None
    assert state.customer.customerReference == "CUST-9"
    assert state.customer.displayName == "Acme Plumbing"
    assert state.customer.branchReference == "BR-14"
    assert state.confirmedOrder is not None
    assert state.confirmedOrder.orderReference == "ORD-77"
    assert state.confirmedOrder.confirmationKey == "acme|CONV-1|ORD-77|LINE-1"
    assert state.confirmedOrder.confirmedAt is not None


def test_the_policy_block_reads_the_evaluator_facts() -> None:
    evaluation = project_policy_evaluation(
        policy_facts(
            extra=(
                fact_document(
                    "policy_reason_codes",
                    "WITHIN_STANDARD_RETURN_WINDOW,NOT_A_REAL_CODE",
                ),
            )
        )
    )

    assert evaluation is not None
    assert evaluation.route is PolicyRoute.STANDARD_RETURN
    assert evaluation.originalDecision is EligibilityDecision.APPROVE
    assert evaluation.effectiveDecision is EligibilityDecision.APPROVE
    assert evaluation.override is None
    assert evaluation.policyId == "FERGUSON_RETURNS"
    assert evaluation.evaluatedAt is not None
    # The retired code is skipped; the live one survives. Provenance must not
    # make a case unreadable.
    assert evaluation.reasonCodes is not None
    assert [code.value for code in evaluation.reasonCodes] == ["WITHIN_STANDARD_RETURN_WINDOW"]


def test_an_override_resolves_the_effective_decision() -> None:
    evaluation = project_policy_evaluation(
        policy_facts(
            decision=EligibilityDecision.REVIEW_REQUIRED.value,
            extra=(
                fact_document("policy_override_decision", EligibilityDecision.APPROVE.value),
                fact_document("policy_override_reason_code", "SUPERVISOR_DISCRETION"),
                fact_document("policy_override_actor", "supervisor-1"),
                fact_document("policy_override_at", "2026-08-15T11:58:00+00:00"),
            ),
        )
    )

    assert evaluation is not None
    assert evaluation.originalDecision is EligibilityDecision.REVIEW_REQUIRED
    assert evaluation.effectiveDecision is EligibilityDecision.APPROVE
    assert evaluation.override is not None
    assert evaluation.override.actor == "supervisor-1"


def test_a_half_written_override_is_not_projected() -> None:
    """No actor, no audit. Under-claiming is the safe direction."""
    evaluation = project_policy_evaluation(
        policy_facts(
            decision=EligibilityDecision.REVIEW_REQUIRED.value,
            extra=(
                fact_document("policy_override_decision", EligibilityDecision.APPROVE.value),
                fact_document("policy_override_reason_code", "SUPERVISOR_DISCRETION"),
                fact_document("policy_effective_decision", EligibilityDecision.APPROVE.value),
            ),
        )
    )

    assert evaluation is not None
    assert evaluation.override is None
    assert evaluation.effectiveDecision is EligibilityDecision.REVIEW_REQUIRED


def test_a_verification_route_carries_no_decision_and_no_override() -> None:
    evaluation = project_policy_evaluation(
        policy_facts(
            route=PolicyRoute.WARRANTY.value,
            decision=None,
            extra=(
                fact_document("policy_override_decision", EligibilityDecision.APPROVE.value),
                fact_document("policy_override_reason_code", "SUPERVISOR_DISCRETION"),
                fact_document("policy_override_actor", "supervisor-1"),
                fact_document("policy_override_at", "2026-08-15T11:58:00+00:00"),
            ),
        )
    )

    assert evaluation is not None
    assert evaluation.route is PolicyRoute.WARRANTY
    assert evaluation.originalDecision is None
    assert evaluation.effectiveDecision is None
    assert evaluation.override is None


def test_no_route_fact_is_no_evaluation_and_an_unreadable_one_is_loud() -> None:
    assert project_policy_evaluation({}) is None
    with pytest.raises(ValueError, match="policy_route"):
        project_policy_evaluation(policy_facts(route="NOT_A_ROUTE"))


def test_the_warehouse_block_needs_a_bay_fact_to_exist_at_all() -> None:
    assert project_facts({}) is None
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(),
            facts=latest(fact_document("bay_reference", "BAY-3")),
        )
    )

    assert state.warehouse is not None
    assert state.warehouse.bayId == "BAY-3"
    # A recommended bay is not goods booked in.
    assert state.warehouse.has_receipt is False
    projection = project_case(state, requirements=RELEASED_REQUIREMENTS)
    assert projection.stage is not CopilotStage.WAREHOUSE_RECEIVING


# ---------------------------------------------------------------------------
# 6. Phase 9 -- warehouse fields have producers or they are absent, and
#    settlement is honest about not existing.
# ---------------------------------------------------------------------------


def bay_facts(**values: Any) -> dict[str, dict[str, Any]]:
    """The bay facts exactly as `_record_bay_facts` writes them.

    That writer skips a `None`, so a fact absent here is a fact the workflow
    would not have written -- which is the shape the projection has to read.
    """
    return latest(*(fact_document(name, value) for name, value in values.items()))


def test_a_case_with_bay_facts_projects_them_to_their_named_fields() -> None:
    """The three fields with a producer, from the three facts that produce them."""
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(),
            facts=bay_facts(
                bay_warehouse_reference="WH-ATL-01",
                bay_reference="BAY-3",
                bay_reason="RECOMMENDED",
            ),
        )
    )

    warehouse = state.warehouse
    assert warehouse is not None
    assert warehouse.facilityId == "WH-ATL-01"
    assert warehouse.bayId == "BAY-3"
    assert warehouse.bayReason == "RECOMMENDED"


def test_every_warehouse_field_without_a_producer_is_none() -> None:
    """The audit's `Bay 14-B` and `Tier 2 Technical Inspection`, structurally impossible.

    Nothing on the case path writes a receipt, an inspection, a condition, a
    disposition, a QA state or a warehouse status -- `ReturnSessionView.
    warehouseStatus` and the handling units are keyed by session, and a Copilot
    case has no session. Asserted over the whole richest fact log the workflow
    can produce, so a future field filled from something merely plausible fails
    here rather than on a screen.
    """
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(),
            facts=bay_facts(
                bay_warehouse_reference="WH-ATL-01",
                bay_reference="BAY-3",
                bay_return_location="WH-ATL-01/BAY-3",
                bay_confidence_millionths=750_000,
                bay_reason="RECOMMENDED",
                bay_evidence_reference="WAREHOUSE_OBSERVED:gen-9:4",
                bay_capacity_evidence="LIVE",
            ),
        )
    )

    warehouse = state.warehouse
    assert warehouse is not None
    assert warehouse.facilityName is None
    assert warehouse.receivedAt is None
    assert warehouse.receivedQuantity is None
    assert warehouse.inspectionStatus is None
    assert warehouse.condition is None
    assert warehouse.disposition is None
    assert warehouse.qaStatus is None
    assert warehouse.warehouseStatus is None


def test_a_case_without_bay_facts_has_no_warehouse_block_at_all() -> None:
    """`None`, never an empty block. An all-null warehouse asserts a placement."""
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(),
            facts=latest(fact_document("return_reason", "DAMAGED")),
        )
    )

    assert state.warehouse is None


def test_a_bay_reason_with_no_bay_is_an_explanation_and_not_an_error() -> None:
    """The shape `BAY_PLACEMENT_NOT_CONFIGURED` produces: a reason and nothing else.

    A case with no bay is the normal state of most cases for most of their
    lives. The block exists so the reason is readable; it carries no bay, it is
    not a receipt, it does not advance the stage, and it makes the case await
    nothing new.
    """
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(),
            facts=bay_facts(bay_reason="BAY_PLACEMENT_NOT_CONFIGURED"),
        )
    )

    warehouse = state.warehouse
    assert warehouse is not None
    assert warehouse.bayReason == "BAY_PLACEMENT_NOT_CONFIGURED"
    assert warehouse.bayId is None
    assert warehouse.facilityId is None
    assert warehouse.has_receipt is False

    projection = project_case(state, requirements=RELEASED_REQUIREMENTS)
    assert projection.stage is CopilotStage.DISCOVERY
    assert AwaitingDimension.RMA not in projection.awaiting


@pytest.mark.parametrize(
    "reason",
    [
        "RECOMMENDED",
        "NO_ELIGIBLE_BAY",
        "PRE_ARRIVAL_NOT_ALLOWED",
        "PHYSICAL_RECEIPT_REQUIRED",
        "WAREHOUSE_ABSENT_NO_WAREHOUSE_REFERENCE",
        "WAREHOUSE_UNAVAILABLE_UNKNOWN",
        "BAY_PLACEMENT_NOT_CONFIGURED",
        "REQUEST_FAILED",
    ],
)
def test_no_placement_reason_the_platform_can_write_reads_as_a_receipt(reason: str) -> None:
    """Every reason `CaseBayPlacement` and `ReturnCaseWorkflow` can emit, enumerated.

    `bayReason` is a separate field from `warehouseStatus` precisely so this
    holds: folding the reason into the status would make `has_receipt` true for
    every case that merely asked for a bay, and the Copilot would render a
    receiving pane over a recommendation.
    """
    state = assemble_case_projection_state(
        CaseAggregateDocuments(case=case_document(), facts=bay_facts(bay_reason=reason))
    )

    assert state.warehouse is not None
    assert state.warehouse.has_receipt is False
    projection = project_case(state, requirements=RELEASED_REQUIREMENTS)
    assert projection.stage is not CopilotStage.WAREHOUSE_RECEIVING


def test_settlement_is_always_not_integrated_on_an_assembled_case() -> None:
    """No producer exists, so there is one answer and every case gives it.

    Never `NOT_STARTED`: that would say a producer exists and has not run.
    """
    for facts in ({}, bay_facts(bay_reference="BAY-3"), policy_facts()):
        state = assemble_case_projection_state(
            CaseAggregateDocuments(case=case_document(), facts=facts)
        )
        assert state.settlement is not None
        assert state.settlement.status is SettlementStatus.NOT_INTEGRATED


def test_settlement_never_awaits_and_never_blocks_an_assembled_case() -> None:
    """Asserted on the assembled path, not only on a hand-built state.

    The `PREPAID_PARCEL` return below has its RMA, its label and its tracking,
    so nothing but settlement could hold it back -- and it completes, because
    `businessComplete` is completion within configured platform responsibility
    and settlement is outside that boundary.
    """
    projection = project_case(
        assemble_case_projection_state(
            CaseAggregateDocuments(
                case=case_document(status=CaseStatus.RMA_RECEIVED.value),
                facts=policy_facts(
                    extra=(fact_document("return_method", "PREPAID_PARCEL"),),
                ),
                return_records=(
                    record_document(
                        returnReference="RMA-1",
                        trackingReference="1Z-1",
                        labelReference="LBL-1",
                    ),
                ),
            )
        ),
        requirements=RELEASED_REQUIREMENTS,
    )

    assert projection.settlement is not None
    assert projection.settlement.status is SettlementStatus.NOT_INTEGRATED
    assert projection.awaiting == ()
    assert projection.businessComplete is True
    # And it could not have entered `awaiting` even if a rule tried: the
    # vocabulary has no settlement member to put there.
    assert not [dimension for dimension in AwaitingDimension if "SETTLE" in dimension.value]


def test_a_closed_case_reaches_completed_external_settlement_never_completed() -> None:
    """The whole point of Phase 9's settlement rule, on the assembled path.

    A completed-return count must never be readable as a settled-return count.
    """
    projection = project_case(
        assemble_case_projection_state(
            CaseAggregateDocuments(
                case=case_document(status=CaseStatus.CLOSED.value),
                return_records=(record_document(returnReference="RMA-1"),),
            )
        ),
        requirements=RELEASED_REQUIREMENTS,
    )

    assert projection.settlement is not None
    assert projection.settlement.status is SettlementStatus.NOT_INTEGRATED
    assert projection.status is ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT
    assert projection.status is not ReturnCaseStatus.COMPLETED
    assert projection.isTerminal is True


# ---------------------------------------------------------------------------
# A refused claim is refused, not settled.
#
# `_record_support_outcome` writes `CaseStatus.CLOSED` for a rejection and
# `_close_business_complete` writes the same value for a return that finished,
# so the projection had nothing to tell them apart with and read every closed
# case as `COMPLETED_EXTERNAL_SETTLEMENT`. A refused warranty claim was
# therefore terminal *and* still awaiting the verification the refusal had
# answered, and the terminal half asserted a credit settled outside the
# platform. The `support_outcome` fact is what closes it.
# ---------------------------------------------------------------------------


def refused_claim_documents(
    route: str = PolicyRoute.WARRANTY.value,
    *,
    reason: str | None = "Serial number outside the warranty term.",
    outcome: str = SupportOutcome.REJECTED.value,
) -> CaseAggregateDocuments:
    """A warranty case Support looked at and declined. No RMA, because none was issued."""
    facts = [
        fact_document("policy_route", route),
        fact_document(SUPPORT_OUTCOME_FACT, outcome),
    ]
    if reason is not None:
        facts.append(fact_document(SUPPORT_OUTCOME_REASON_FACT, reason))
    return CaseAggregateDocuments(
        case=case_document(status=CaseStatus.CLOSED.value, channelBWorkItemId="WI-1"),
        facts=latest(*facts),
        support_work_item={"_id": "WI-1", "queue": "WARRANTY_SUPPORT", "status": "RESOLVED"},
    )


@pytest.mark.parametrize("route", [PolicyRoute.WARRANTY.value, PolicyRoute.DELIVERY_CLAIM.value])
def test_a_refused_claim_is_terminal_and_awaits_nothing(route: str) -> None:
    """The two halves of the incoherence, asserted together because they were one bug.

    Terminal *and* waiting is not a state a case can be in. Before the outcome
    was recorded, a refused claim was exactly that: `isTerminal: true` beside
    `awaiting: ['WARRANTY_VERIFICATION']`.
    """
    projection = project_case(
        assemble_case_projection_state(refused_claim_documents(route)),
        requirements=RELEASED_REQUIREMENTS,
    )

    assert projection.status is ReturnCaseStatus.POLICY_REJECTED
    assert projection.awaiting == ()
    assert projection.isTerminal is True
    assert projection.businessComplete is False
    assert projection.stage is CopilotStage.COMPLETED


def test_a_refused_claim_is_not_a_settled_one() -> None:
    """The distinction an operator has to be able to draw, stated as an A/B.

    Both cases are `CLOSED` in Mongo and both carry the same
    `NOT_INTEGRATED` settlement. What separates them is the answer Support
    gave, and the projected status is where that separation lands.
    """
    refused = project_case(
        assemble_case_projection_state(refused_claim_documents()),
        requirements=RELEASED_REQUIREMENTS,
    )
    settled_elsewhere = project_case(
        assemble_case_projection_state(
            CaseAggregateDocuments(
                case=case_document(status=CaseStatus.CLOSED.value),
                return_records=(record_document(returnReference="RMA-1"),),
            )
        ),
        requirements=RELEASED_REQUIREMENTS,
    )

    assert refused.status is ReturnCaseStatus.POLICY_REJECTED
    assert settled_elsewhere.status is ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT
    assert refused.status is not settled_elsewhere.status
    # And neither claims a settlement the platform could produce.
    for projection in (refused, settled_elsewhere):
        assert projection.settlement is not None
        assert projection.settlement.status is SettlementStatus.NOT_INTEGRATED


def test_a_refused_claim_carries_why_and_when_without_claiming_who() -> None:
    """The record behind the status, and the one thing it does not pretend to know.

    `SupportResponseNotice` carries no actor, so the fact's provenance names the
    component that recorded the answer -- never the person who reached it. A
    projection that filled in a verifier would be the invented tracking number
    in another costume.
    """
    state = assemble_case_projection_state(refused_claim_documents())
    assert state.facts is not None
    by_name = {fact.factName: fact for fact in state.facts}

    outcome = by_name[SUPPORT_OUTCOME_FACT]
    assert outcome.value == SupportOutcome.REJECTED.value
    assert outcome.recordedAt is not None
    assert by_name[SUPPORT_OUTCOME_REASON_FACT].value == "Serial number outside the warranty term."
    # The work item Support answered on is still projected beside it.
    assert state.support is not None
    assert state.support.queue == "WARRANTY_SUPPORT"


def test_a_support_refusal_stays_distinguishable_from_an_evaluator_rejection() -> None:
    """They share a projected status; they do not share a projection.

    That is the test `CANCELLED` and `EXPIRED` had to fail before they were
    split into two members -- nothing else told those two apart. Here the route,
    the decision, the work item and the outcome fact all do.
    """
    refused = assemble_case_projection_state(refused_claim_documents())
    evaluator_rejected = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(status=CaseStatus.POLICY_REJECTED.value),
            facts=policy_facts(decision=EligibilityDecision.REJECT.value),
        )
    )

    assert refused.status is evaluator_rejected.status is ReturnCaseStatus.POLICY_REJECTED

    assert refused.policyEvaluation is not None
    assert refused.policyEvaluation.route is PolicyRoute.WARRANTY
    assert refused.policyEvaluation.originalDecision is None
    assert refused.support is not None

    assert evaluator_rejected.policyEvaluation is not None
    assert evaluator_rejected.policyEvaluation.route is PolicyRoute.STANDARD_RETURN
    assert evaluator_rejected.policyEvaluation.originalDecision is EligibilityDecision.REJECT
    assert evaluator_rejected.support is None


def test_a_case_support_has_not_answered_reads_as_no_outcome_rather_than_a_refusal() -> None:
    assert project_support_outcome({}) is None
    assert project_support_outcome(latest(fact_document("policy_route", "WARRANTY"))) is None
    assert project_support_outcome(latest(fact_document(SUPPORT_OUTCOME_FACT, ""))) is None


def test_an_unreadable_support_outcome_fails_the_read_rather_than_completing_the_case() -> None:
    """Defaulting it to `None` would report a refusal as a completion, every poll.

    The same stance `policy_route` takes, for a sharper reason: this value
    decides whether the platform announces a settled credit.
    """
    with pytest.raises(ValueError, match="not a SupportOutcome"):
        assemble_case_projection_state(refused_claim_documents(outcome="DECLINED"))


def test_warehouse_receiving_is_unreachable_from_persistence_today() -> None:
    """Phase 9, task 3: the answer is *no producer*, and it is asserted rather than assumed.

    `_is_receiving` has exactly two ways in and persistence can supply neither.

    * `warehouse.has_receipt` reads `receivedAt`, `warehouseStatus`,
      `receivedQuantity` and `inspectionStatus`. No case-keyed writer exists for
      any of them; the warehouse block projects placement, and placement runs
      before the goods arrive.
    * a shipment in `DELIVERED` or `RECEIVED`. `ShipmentProjection.
      shipmentStatus` has no producer, and the only case-level fulfilment
      producer -- `return_shipment_state.py`, fact `fulfillment_status` -- draws
      from `FulfillmentTrackingStatus`, whose three members are
      `NOT_APPLICABLE`, `AWAITING_HANDOFF` and `IN_TRANSIT`. Nothing in the
      platform can say a return arrived.

    So the stage stays unreachable, and this test is the record of why. It fails
    the day a receiving producer lands, which is the day to wire it -- not
    before, and never by lighting the pane from something else.
    """
    assert {status.value for status in FulfillmentTrackingStatus}.isdisjoint(
        {ShipmentStatus.DELIVERED.value, ShipmentStatus.RECEIVED.value}
    )

    richest = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=case_document(status=CaseStatus.RMA_RECEIVED.value),
            facts=policy_facts(
                extra=(
                    fact_document("return_method", "PREPAID_PARCEL"),
                    fact_document("fulfillment_status", "IN_TRANSIT"),
                    fact_document("bay_warehouse_reference", "WH-ATL-01"),
                    fact_document("bay_reference", "BAY-3"),
                    fact_document("bay_reason", "RECOMMENDED"),
                ),
            ),
            return_records=(
                record_document(
                    returnReference="RMA-1",
                    trackingReference="1Z-1",
                    labelReference="LBL-1",
                ),
            ),
        )
    )

    assert richest.warehouse is not None
    assert richest.warehouse.has_receipt is False
    assert all(shipment.shipmentStatus is None for shipment in richest.all_shipments())
    projection = project_case(richest, requirements=RELEASED_REQUIREMENTS)
    assert projection.stage is not CopilotStage.WAREHOUSE_RECEIVING


def test_support_is_absent_when_no_work_item_was_loaded() -> None:
    assert project_support(None) is None
    assert (
        project_support(
            {
                "_id": "WI-1",
                "threadId": "TH-1",
                "queue": "RETURNS_SUPPORT",
                "status": "OPEN",
                "subject": "RMA for ORD-77",
                "createdAt": datetime(2026, 8, 15, 11, 40),
                "completedAt": None,
            }
        )
        is not None
    )


# ---------------------------------------------------------------------------
# 5. Backfill.
# ---------------------------------------------------------------------------


def test_a_current_case_needs_no_backfill() -> None:
    plan = plan_case_backfill(case_document(), workflow_terminated=False)

    assert plan.is_noop
    assert plan.actions == ()
    assert plan.status is None
    assert plan.expected_version == 3


def test_a_document_with_no_version_is_initialized_deterministically() -> None:
    document = case_document()
    del document["version"]

    plan = plan_case_backfill(document, workflow_terminated=False)

    assert plan.actions == (CaseBackfillAction.INITIALIZE_REVISION,)
    assert plan.expected_version == 0
    # And the read path already agrees, so nothing jumps when the migration runs.
    state = assemble_case_projection_state(CaseAggregateDocuments(case=document))
    assert state.revision == 0


def test_an_orphan_is_marked_for_recovery_and_a_second_run_plans_nothing() -> None:
    orphan = case_document(status=CaseStatus.AWAITING_SUPPORT.value)

    first = plan_case_backfill(orphan, workflow_terminated=True)
    assert first.actions == (CaseBackfillAction.MARK_RECOVERY_REQUIRED,)
    assert first.status is CaseStatus.RECOVERY_REQUIRED

    recovered = {**orphan, "status": CaseStatus.RECOVERY_REQUIRED.value}
    second = plan_case_backfill(recovered, workflow_terminated=True)
    assert second.is_noop


def test_a_terminal_case_is_never_reopened_by_the_backfill() -> None:
    """Only the workflow writes CLOSED, CANCELLED or POLICY_REJECTED."""
    for status in (CaseStatus.CLOSED, CaseStatus.CANCELLED, CaseStatus.POLICY_REJECTED):
        plan = plan_case_backfill(case_document(status=status.value), workflow_terminated=True)
        assert plan.is_noop, status


def test_an_orphaned_case_projects_as_recovery_required() -> None:
    """Non-terminal, awaiting recovery, and never business-complete."""
    orphan = case_document(status=CaseStatus.RECOVERY_REQUIRED.value)
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case=orphan,
            return_records=(
                record_document(
                    "RR-1",
                    returnReference="RMA-1",
                    trackingReference="1Z-AAA",
                    labelReference="LBL-A",
                ),
            ),
            facts=policy_facts(),
        )
    )

    projection = project_case(state, requirements=RELEASED_REQUIREMENTS)

    assert projection.status is ReturnCaseStatus.RECOVERY_REQUIRED
    assert projection.isTerminal is False
    assert AwaitingDimension.RECOVERY in projection.awaiting
    assert projection.businessComplete is False


def test_the_backfill_refuses_a_status_it_cannot_read() -> None:
    with pytest.raises(UnmappedCaseStatusError):
        plan_case_backfill(case_document(status="SOMETHING_ELSE"), workflow_terminated=True)


# ---------------------------------------------------------------------------
# The repository method, against a double for the collections it reads.
# ---------------------------------------------------------------------------


class _FakeCursor:
    """`find(...).sort(...).limit(...)` and an async iteration. Nothing more."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, key: str, direction: int = 1) -> _FakeCursor:
        self._documents.sort(key=lambda document: str(document.get(key)), reverse=direction < 0)
        return self

    def limit(self, count: int) -> _FakeCursor:
        self._documents = self._documents[:count]
        return self

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for document in self._documents:
            yield document


class _FakeCollection:
    """Equality filters, `$exists`, `$set` and `$inc`. The slice this test needs."""

    def __init__(
        self, database: _FakeDatabase, documents: Iterable[Mapping[str, Any]] = ()
    ) -> None:
        self.database = database
        self.documents: list[dict[str, Any]] = [dict(document) for document in documents]

    @staticmethod
    def _matches(document: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
        for key, expected in query.items():
            if isinstance(expected, Mapping) and "$exists" in expected:
                if (key in document) is not bool(expected["$exists"]):
                    return False
                continue
            if document.get(key) != expected:
                return False
        return True

    def find(self, query: Mapping[str, Any]) -> _FakeCursor:
        return _FakeCursor([dict(d) for d in self.documents if self._matches(d, query)])

    async def find_one(
        self, query: Mapping[str, Any], projection: Mapping[str, Any] | None = None, **_: Any
    ) -> dict[str, Any] | None:
        del projection
        for document in self.documents:
            if self._matches(document, query):
                return dict(document)
        return None

    async def insert_one(self, document: MutableMapping[str, Any]) -> None:
        self.documents.append(dict(document))

    def _apply(self, document: dict[str, Any], update: Mapping[str, Any]) -> None:
        for key, value in update.get("$set", {}).items():
            document[key] = value
        for key, value in update.get("$inc", {}).items():
            document[key] = int(document.get(key, 0)) + int(value)

    async def update_one(self, query: Mapping[str, Any], update: Mapping[str, Any]) -> None:
        for document in self.documents:
            if self._matches(document, query):
                self._apply(document, update)
                return

    async def find_one_and_update(
        self, query: Mapping[str, Any], update: Mapping[str, Any], **_: Any
    ) -> dict[str, Any] | None:
        for document in self.documents:
            if self._matches(document, query):
                self._apply(document, update)
                return dict(document)
        return None


class _FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection(self))


def build_repository(
    *,
    cases: Iterable[Mapping[str, Any]] = (),
    facts: Iterable[Mapping[str, Any]] = (),
    records: Iterable[Mapping[str, Any]] = (),
    items: Iterable[Mapping[str, Any]] = (),
    work_items: Iterable[Mapping[str, Any]] = (),
) -> CaseRepository:
    database = _FakeDatabase()
    repository = CaseRepository()
    repository.cases = _FakeCollection(database, cases)  # type: ignore[assignment]
    repository.case_facts = _FakeCollection(database, facts)  # type: ignore[assignment]
    repository.return_records = _FakeCollection(database, records)  # type: ignore[assignment]
    repository.return_items = _FakeCollection(database, items)  # type: ignore[assignment]
    database._collections["support_work_items"] = _FakeCollection(database, work_items)
    return repository


@pytest.mark.asyncio
async def test_the_repository_assembles_the_projection_from_all_four_collections() -> None:
    repository = build_repository(
        cases=(case_document(channelBWorkItemId="WI-1", confirmedOrderReference="ORD-77"),),
        facts=(
            fact_document("policy_route", PolicyRoute.STANDARD_RETURN.value),
            fact_document("policy_decision", EligibilityDecision.APPROVE.value),
            fact_document("customer_id", "CUST-9"),
        ),
        records=(
            record_document(
                REAL_RECORD_ID,
                returnReference="RMA-4E372",
                labelReference="LBL-4E372",
            ),
        ),
        items=(item_document("ITEM-1", returnRecordId=REAL_RECORD_ID),),
        work_items=(
            {
                "_id": "WI-1",
                "caseId": "CASE-1",
                "threadId": "TH-1",
                "queue": "RETURNS_SUPPORT",
                "status": "OPEN",
                "subject": "RMA for ORD-77",
                "createdAt": datetime(2026, 8, 15, 11, 40),
                "completedAt": None,
            },
        ),
    )

    state = await repository.load_case_projection_state("CASE-1")

    assert state is not None
    assert state.confirmedOrder is not None
    assert state.customer is not None and state.customer.customerReference == "CUST-9"
    assert state.policyEvaluation is not None
    assert state.support is not None and state.support.queue == "RETURNS_SUPPORT"
    assert state.records()[0].shipments is None
    assert state.records()[0].approvedItems is not None
    assert state.selectedItems is None

    projection = project_case(state, requirements=RELEASED_REQUIREMENTS)
    assert projection.businessComplete is False
    assert AwaitingDimension.RETURN_METHOD in projection.awaiting


@pytest.mark.asyncio
async def test_a_missing_case_projects_as_nothing_rather_than_raising() -> None:
    repository = build_repository()

    assert await repository.load_case_projection_state("CASE-MISSING") is None


@pytest.mark.asyncio
async def test_the_repository_backfill_recovers_an_orphan_once() -> None:
    repository = build_repository(
        cases=(case_document(status=CaseStatus.AWAITING_SUPPORT.value, version=4),)
    )

    first = await repository.backfill_case("CASE-1", workflow_terminated=True)
    second = await repository.backfill_case("CASE-1", workflow_terminated=True)

    assert first.actions == (CaseBackfillAction.MARK_RECOVERY_REQUIRED,)
    assert second.is_noop

    stored = await repository.get_case("CASE-1")
    assert stored is not None
    assert stored["status"] == CaseStatus.RECOVERY_REQUIRED.value
    # One write, one revision. The second run must not have bumped it again.
    assert stored["version"] == 5

    state = await repository.load_case_projection_state("CASE-1")
    assert state is not None
    recovered = project_case(state, requirements=RELEASED_REQUIREMENTS)
    assert recovered.status is ReturnCaseStatus.RECOVERY_REQUIRED


@pytest.mark.asyncio
async def test_the_repository_backfill_initializes_a_missing_revision_once() -> None:
    document = case_document()
    del document["version"]
    repository = build_repository(cases=(document,))

    first = await repository.backfill_case("CASE-1", workflow_terminated=False)
    stored_after_first = await repository.get_case("CASE-1")
    second = await repository.backfill_case("CASE-1", workflow_terminated=False)
    stored_after_second = await repository.get_case("CASE-1")

    assert first.actions == (CaseBackfillAction.INITIALIZE_REVISION,)
    assert second.is_noop
    assert stored_after_first == stored_after_second
    assert stored_after_second is not None
    assert stored_after_second["version"] == 0


@pytest.mark.asyncio
async def test_a_versionless_orphan_is_initialized_and_recovered_in_one_run() -> None:
    """Both writes, in an order that leaves `update_case` an expectation it can meet."""
    document = case_document(status=CaseStatus.AWAITING_SUPPORT.value)
    del document["version"]
    repository = build_repository(cases=(document,))

    plan = await repository.backfill_case("CASE-1", workflow_terminated=True)

    assert plan.actions == (
        CaseBackfillAction.INITIALIZE_REVISION,
        CaseBackfillAction.MARK_RECOVERY_REQUIRED,
    )
    stored = await repository.get_case("CASE-1")
    assert stored is not None
    assert stored["status"] == CaseStatus.RECOVERY_REQUIRED.value
    assert stored["version"] == 1
    assert (await repository.backfill_case("CASE-1", workflow_terminated=True)).is_noop


@pytest.mark.asyncio
async def test_backfilling_a_set_of_cases_touches_only_the_orphans() -> None:
    repository = build_repository(
        cases=(
            case_document(caseId="CASE-1", status=CaseStatus.AWAITING_SUPPORT.value),
            case_document(caseId="CASE-2", status=CaseStatus.AWAITING_SUPPORT.value),
        )
    )

    plans = await repository.backfill_cases(("CASE-1", "CASE-2"), terminated_case_ids={"CASE-1"})

    assert plans[0].actions == (CaseBackfillAction.MARK_RECOVERY_REQUIRED,)
    assert plans[1].is_noop
    healthy = await repository.get_case("CASE-2")
    assert healthy is not None
    assert healthy["status"] == CaseStatus.AWAITING_SUPPORT.value


@pytest.mark.asyncio
async def test_backfilling_a_case_that_does_not_exist_raises() -> None:
    repository = build_repository()

    with pytest.raises(KeyError):
        await repository.backfill_case("CASE-MISSING", workflow_terminated=True)
