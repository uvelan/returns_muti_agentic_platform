"""Deterministic fulfillment construction from return evidence and one observed shipment.

The agent named "fulfillment tracking" never read a tracking source. `IN_TRANSIT`
was concluded from `tracking_reference is not None` -- that is, from the platform
having written a number into its own store, never from anything having been seen
to move. An RMA whose label was printed and then left on the counter reported the
same state as one the carrier had collected.

A tracking number is now necessary but not sufficient: `IN_TRANSIT` requires a
shipment observed against that number in the graph. Absent one -- no shipment
record, or no way to look -- the honest state is `AWAITING_HANDOFF`, and which of
the three it was lands in `evidence_references` so a reader can tell "we looked
and there is nothing" from "we could not look".

Still a pure function. The observation is passed in because reading the graph is
IO and this is called from a Temporal-adjacent path; the caller does the looking
-- `operations/orchestrator.py` on the legacy session path, and
`operations/return_shipment_state.py` on the canonical case path.

Two callers, one rule. The session path needs the whole stage result because it
binds the deterministic evidence chain (eligibility -> return request ->
fulfillment); the case path has no eligibility stage and must not fabricate one,
so it takes `fulfillment_status_for_shipment` and `shipment_evidence_reference`
directly. Both therefore reach `IN_TRANSIT` by the same test, which is the point
of extracting them.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.stage_results import (
    FulfillmentTrackingActivityResult,
    FulfillmentTrackingStatus,
    ReturnRequestOutcome,
    StageContextBinding,
    bind_stage_activity_result,
    return_request_result_from_binding,
)

__all__ = [
    "ShipmentEvidence",
    "ShipmentObservation",
    "ShipmentObservationPort",
    "build_fulfillment_tracking_result",
    "fulfillment_status_for_shipment",
    "shipment_evidence_reference",
]


class ShipmentEvidence(StrEnum):
    """Why the fulfillment state is what it is.

    Three outcomes, not two, because "the graph holds no shipment for this
    number" and "the platform could not reach the graph" carry different
    obligations: the first is a return genuinely not yet handed over, the second
    is an outage somebody has to fix. Collapsing them is how the previous
    implementation's single inference became invisible.
    """

    OBSERVED = "OBSERVED"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ShipmentObservation:
    """What the graph knew about one tracking reference.

    `graph_generation_id` is the generation the read was leased against, and is
    recorded rather than merely used: a fulfillment state derived from a
    generation that has since been retired is still a defensible reading of the
    evidence available at the time, and without the id nobody can establish that
    afterwards. It is not a claim that the rows were filtered to that
    generation -- `Neo4jKnowledgeGateway.execute` does not fence reads.

    No carrier: the verified `shipmentInfo` contract has none at this grain, and
    a field nothing can ever populate reads to a caller as "the carrier is
    unknown for this shipment" rather than as "this platform does not carry a
    carrier".
    """

    tracking_reference: str
    evidence: ShipmentEvidence
    graph_generation_id: str
    current_status: str | None = None
    shipment_id: str | None = None
    #: Set when a targeted sync ran for this tracking reference before the read.
    sync_request_id: str | None = None
    #: Why no sync ran, or why it failed. The read still happens -- a scheduled
    #: sync may already have brought the shipment in -- so this is not fatal.
    sync_skipped_reason: str | None = None
    #: Why the graph could not be read at all. Only set with UNAVAILABLE.
    unavailable_reason: str | None = None


class ShipmentObservationPort(Protocol):
    """What fulfillment needs from the graph, and nothing more.

    A structural protocol so `operations` neither imports `dynamic_knowledge`
    nor learns what a generation lease is -- the adapter lives in
    `dynamic_knowledge/integration/shipment_observations.py`.
    """

    async def observe(self, tracking_reference: str) -> ShipmentObservation: ...


def shipment_evidence_reference(observation: ShipmentObservation | None) -> str:
    """One audit string per possible reading of the shipment evidence.

    Reference strings are validated as identifiers (no whitespace, no control
    characters), so every component here is a code rather than prose.

    Public because the canonical case flow records the same reading as a case
    fact (`operations/return_shipment_state.py`). Two encodings of "what the
    graph said about this parcel" is how a legacy stage result and a case fact
    start disagreeing about the same shipment.
    """
    if observation is None:
        return "SHIPMENT_UNAVAILABLE:NOT_ATTEMPTED"
    if observation.evidence is ShipmentEvidence.OBSERVED:
        return (
            f"SHIPMENT_OBSERVED:{observation.graph_generation_id}:"
            f"{observation.current_status or 'STATUS_UNKNOWN'}"
        )
    if observation.evidence is ShipmentEvidence.ABSENT:
        return f"SHIPMENT_ABSENT:{observation.graph_generation_id}"
    return f"SHIPMENT_UNAVAILABLE:{observation.unavailable_reason or 'UNKNOWN'}"


def fulfillment_status_for_shipment(
    observation: ShipmentObservation | None,
) -> FulfillmentTrackingStatus:
    """The one rule that turns a shipment reading into a fulfillment state.

    `IN_TRANSIT` requires a shipment *observed* against the tracking number.
    Neither "the graph holds no such shipment" nor "the graph could not be read"
    is evidence that anything moved, so both stay `AWAITING_HANDOFF` -- which of
    the three it was survives on the evidence reference, not on the status.

    Extracted so the legacy session stage result and the canonical case flow
    apply the same rule rather than two copies of it: a divergence here would
    show up as one screen calling a return collected while the other calls it
    waiting, with no way to tell which is wrong.
    """
    return (
        FulfillmentTrackingStatus.IN_TRANSIT
        if observation is not None and observation.evidence is ShipmentEvidence.OBSERVED
        else FulfillmentTrackingStatus.AWAITING_HANDOFF
    )


def build_fulfillment_tracking_result(
    *,
    return_request: ContextSnapshot,
    fulfillment_reference: str | None,
    tracking_reference: str | None,
    configuration_version: str,
    observed_at: datetime,
    shipment: ShipmentObservation | None = None,
) -> FulfillmentTrackingActivityResult:
    """Map authoritative return evidence and one shipment reading to a tracking state."""
    request_result = return_request_result_from_binding(
        StageContextBinding(
            completed_stage=WorkflowStage.RETURN_REQUEST,
            schema_version=return_request.schema_version,
            payload_json=return_request.payload_json,
            payload_digest=return_request.payload_digest,
        )
    )
    evidence_references = (
        *request_result.evidence_references,
        f"CONTEXT_SHA256:{return_request.payload_digest}",
    )
    if request_result.outcome is ReturnRequestOutcome.CREATED:
        if fulfillment_reference is None:
            raise ValueError("Created returns require a fulfillment reference.")
        if tracking_reference is None:
            # Nothing to look up. Recording an evidence line here would claim a
            # lookup that never made sense to attempt.
            status = FulfillmentTrackingStatus.AWAITING_HANDOFF
        else:
            if shipment is not None and shipment.tracking_reference != tracking_reference:
                raise ValueError("Shipment observation does not match the tracking reference.")
            status = fulfillment_status_for_shipment(shipment)
            evidence_references = (
                *evidence_references,
                shipment_evidence_reference(shipment),
            )
    else:
        if fulfillment_reference is not None or tracking_reference is not None:
            raise ValueError("Inactive returns cannot carry fulfillment references.")
        status = FulfillmentTrackingStatus.NOT_APPLICABLE
    result = FulfillmentTrackingActivityResult(
        schema_version="fulfillment-tracking-v1",
        return_request_outcome=request_result.outcome,
        status=status,
        request_reference=request_result.request_reference,
        return_reference=request_result.return_reference,
        fulfillment_reference=fulfillment_reference,
        tracking_reference=tracking_reference,
        return_request_context_digest=return_request.payload_digest,
        evidence_references=evidence_references,
        configuration_version=configuration_version,
        observed_at=observed_at,
    )
    bind_stage_activity_result(WorkflowStage.FULFILLMENT_TRACKING, result)
    return result
