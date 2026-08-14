"""One RMA-scoped shipment update, end to end (SHIP-01, T-15, contract C4).

The write half landed first: `SQLBusinessStateRepository.record_shipment_update`
is idempotent, timestamp-aware and stale-safe, and it now synchronizes an APPLIED
update into the active graph generation. This module closes the other two clauses
of C4 -- **fulfilment-readable**, and reaching the person who asked.

The chain, in order, and each link is somewhere you can stand and look at it:

    update -> dbo.return_tracking -> targeted graph sync -> fulfilment read
           -> case fact -> the associate's next turn

The last link is the one that is easy to miss and is the whole point. A shipment
status sitting in SQL, or even in the graph, is invisible to the person who asked
"has my return been collected?". The agent's turn context is built from the case's
fact projection (`RepositoryCaseStore.case_facts` -> `GraphDependencies`), which
is the same mechanism `record_support_outcome` uses to make an RMA appear in the
associate's *original* conversation. Writing the fulfilment reading there is what
makes a carrier event answer a question somebody already asked -- no new chat, no
client-side join, no poll.

**Fulfilment is best-effort here, exactly as it is on the session path.** A graph
outage must not fail a shipment update whose authoritative row is already
committed; the reading comes back `UNAVAILABLE`, the fact says so, and an operator
can tell that from a return genuinely still on the counter. That distinction is
`ShipmentEvidence`'s reason for existing, and this is the second place in the
platform that honours it.

**The status rule is not restated here.** `fulfillment_status_for_shipment` and
`shipment_evidence_reference` come from `workflows/fulfillment_tracking.py`, so
the case path and the legacy session path reach `IN_TRANSIT` by the same test. The
session path additionally binds the deterministic stage chain (eligibility ->
return request -> fulfillment); the case flow has no eligibility stage and will
not fabricate one, so it takes the rule and not the whole stage result.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.sql_business_state import (
    ShipmentUpdate,
    ShipmentUpdateOutcome,
)
from return_platform.workflows.fulfillment_tracking import (
    ShipmentEvidence,
    ShipmentObservation,
    ShipmentObservationPort,
    fulfillment_status_for_shipment,
    shipment_evidence_reference,
)
from return_platform.workflows.stage_results import FulfillmentTrackingStatus

__all__ = [
    "FULFILLMENT_EVIDENCE_FACT",
    "FULFILLMENT_STATUS_FACT",
    "UNRESOLVED_GENERATION",
    "CaseShipmentReading",
    "ReturnShipmentStateService",
]

logger = logging.getLogger("return_platform.operations.return_shipment_state")

#: The two facts a shipment reading contributes to a case. Named constants
#: because the associate-facing read and the tests both have to agree with the
#: writer, and a fact name that drifts silently stops answering.
FULFILLMENT_STATUS_FACT = "fulfillment_status"
FULFILLMENT_EVIDENCE_FACT = "shipment_evidence"

#: What a generation id reads as when the lookup never reached one. The same
#: sentinel `operations/orchestrator.py` writes on the session path -- two
#: spellings of "we did not get that far" would make the audit trail ambiguous
#: for no gain.
UNRESOLVED_GENERATION = "UNRESOLVED"


class ShipmentStatePort(Protocol):
    """The slice of `SQLBusinessStateRepository` this service uses.

    Structural so the service can be exercised without a connection pool, and so
    that what it is allowed to do to the authoritative store is visible in one
    place rather than implied by a concrete class with thirty methods.
    """

    async def record_shipment_update(self, update: ShipmentUpdate) -> ShipmentUpdateOutcome: ...

    async def read_return_record_by_reference(
        self, return_reference: str
    ) -> dict[str, Any] | None: ...


class CaseFactPort(Protocol):
    """The one write this service makes to the case, and nothing more."""

    async def append_case_fact(
        self,
        *,
        fact_id: str,
        case_id: str,
        fact_name: str,
        value: Any,
        agent_id: str,
        channel: FactChannel,
        acquisition_method: FactAcquisition,
        turn_id: str | None = ...,
        source_system: str | None = ...,
        source_path: str | None = ...,
        observed_at: datetime | None = ...,
        supersedes_fact_id: str | None = ...,
        correlation_id: str | None = ...,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CaseShipmentReading:
    """What the graph said about one RMA's parcel, and where it was recorded.

    `case_id` is `None` when no case owns the RMA. That is a real state rather
    than an error -- `dbo.return_tracking` is keyed on `return_reference` and does
    not require a `dbo.return_record` row, so a shipment can legitimately be
    recorded for an RMA the case store has never seen. Reporting it lets a caller
    tell "nobody was told" from "the reading failed".
    """

    return_reference: str
    tracking_reference: str
    case_id: str | None
    status: FulfillmentTrackingStatus
    evidence: ShipmentEvidence
    evidence_reference: str
    graph_generation_id: str | None = None
    current_status: str | None = None


class ReturnShipmentStateService:
    """The production entry point for a return shipment update.

    Owns the ordering -- store, then graph, then fulfilment reading, then the
    case -- because every one of those steps is only meaningful after the one
    before it committed. `record_shipment_update` does the first two; this does
    the last two and refuses to do them for an update that changed nothing.
    """

    def __init__(
        self,
        *,
        business_state: ShipmentStatePort,
        repository: CaseFactPort,
        observations: ShipmentObservationPort | None = None,
    ) -> None:
        self._business_state = business_state
        self._repository = repository
        # Optional for the same reason the sync port is: a process may record
        # shipment updates without being able to read the graph, and refusing the
        # update over that would take the authoritative store down for a
        # reporting concern. The reading then records NOT_ATTEMPTED, which is a
        # distinct evidence line rather than a silent AWAITING_HANDOFF.
        self._observations = observations

    async def record_update(
        self, update: ShipmentUpdate
    ) -> tuple[ShipmentUpdateOutcome, CaseShipmentReading | None]:
        """Apply one update and, if it changed the truth, tell the case about it.

        Returns the outcome unchanged alongside the reading, so a caller can
        still see APPLIED / DUPLICATE / STALE exactly as the store decided it.
        The reading is `None` for the two outcomes that changed nothing: a
        duplicate submission of a status the associate has already been told
        about must not append a second fact, and a rejected stale update must not
        append one at all.
        """
        outcome = await self._business_state.record_shipment_update(update)
        if not outcome.applied:
            return outcome, None
        reading = await self.observe_shipment(
            return_reference=update.return_reference,
            tracking_reference=update.tracking_reference,
        )
        return outcome, reading

    async def observe_shipment(
        self, *, return_reference: str, tracking_reference: str
    ) -> CaseShipmentReading:
        """Read this parcel's truth from the graph and record it on its case.

        Separated from `record_update` so the canonical read is reachable on its
        own: the case detail screen and the associate's turn both want the
        current reading, and neither of them is submitting a carrier event.
        """
        observation = await self._observe(tracking_reference)
        record = await self._business_state.read_return_record_by_reference(return_reference)
        case_id = str(record["case_id"]) if record and record.get("case_id") else None
        reading = CaseShipmentReading(
            return_reference=return_reference,
            tracking_reference=tracking_reference,
            case_id=case_id,
            status=fulfillment_status_for_shipment(observation),
            evidence=observation.evidence if observation else ShipmentEvidence.UNAVAILABLE,
            evidence_reference=shipment_evidence_reference(observation),
            # `UNRESOLVED` is not a generation, so it is not reported as one.
            graph_generation_id=(
                observation.graph_generation_id
                if observation is not None
                and observation.graph_generation_id != UNRESOLVED_GENERATION
                else None
            ),
            current_status=observation.current_status if observation else None,
        )
        if case_id is not None:
            await self._record_on_case(case_id, reading)
        else:
            logger.info(
                "return_shipment_reading_has_no_case",
                extra={
                    "return_reference": return_reference,
                    "tracking_reference": tracking_reference,
                },
            )
        return reading

    async def _observe(self, tracking_reference: str) -> ShipmentObservation | None:
        """Look the shipment up. Never fail the update over it.

        Fulfilment is best-effort by declared policy, which is why an outage
        becomes `UNAVAILABLE` rather than an exception: the alternative is a
        graph outage failing a shipment update whose authoritative row is already
        committed, leaving the store ahead of everything that reads it with
        nothing recorded to say why.
        """
        if self._observations is None:
            return None
        try:
            return await self._observations.observe(tracking_reference)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - best_effort by declared policy
            logger.warning(
                "return_shipment_observation_failed",
                extra={
                    "tracking_reference": tracking_reference,
                    "error_code": type(error).__name__,
                },
                exc_info=True,
            )
            return ShipmentObservation(
                tracking_reference=tracking_reference,
                evidence=ShipmentEvidence.UNAVAILABLE,
                # The same sentinel `operations/orchestrator.py` uses on this
                # path: resolution is what failed, or what the failure happened
                # underneath, and claiming a generation would put an id on an
                # audit trail nothing was read at.
                graph_generation_id=UNRESOLVED_GENERATION,
                unavailable_reason=type(error).__name__.upper(),
            )

    async def _record_on_case(self, case_id: str, reading: CaseShipmentReading) -> None:
        """Two facts: the state, and what it was concluded from.

        Both, not one. The status alone cannot distinguish a return nobody has
        collected from one the platform could not look up, and an associate told
        `AWAITING_HANDOFF` during a graph outage would be told something the
        platform does not actually know.

        `fact_id` is derived from the RMA, the parcel and the evidence rather
        than minted, so a retry of the same reading rewrites the same fact
        instead of appending a second one -- `append_case_fact` is insert-only
        and `latest_case_facts` projects the newest, so an unkeyed append would
        grow the log without adding information.
        """
        key = f"{reading.return_reference}-{reading.tracking_reference}"
        await self._repository.append_case_fact(
            fact_id=f"fulfillment-status-{key}-{reading.status.value}",
            case_id=case_id,
            fact_name=FULFILLMENT_STATUS_FACT,
            value=reading.status.value,
            agent_id="fulfillment-tracking-agent",
            channel=FactChannel.SYSTEM,
            # DERIVED, not OBSERVED: the shipment was observed, but the
            # fulfilment state is this platform's reading of it.
            acquisition_method=FactAcquisition.DERIVED,
            source_system="RETURN_SHIPMENT",
            source_path="RETURN_SHIPMENT_GRAPH_READ",
        )
        await self._repository.append_case_fact(
            fact_id=f"shipment-evidence-{key}-{reading.evidence_reference}",
            case_id=case_id,
            fact_name=FULFILLMENT_EVIDENCE_FACT,
            value=reading.evidence_reference,
            agent_id="fulfillment-tracking-agent",
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.OBSERVED,
            source_system="RETURN_SHIPMENT",
            source_path="RETURN_SHIPMENT_GRAPH_READ",
        )
        logger.info(
            "return_shipment_reading_recorded",
            extra={
                "case_id": case_id,
                "return_reference": reading.return_reference,
                "tracking_reference": reading.tracking_reference,
                "fulfillment_status": reading.status.value,
                "shipment_evidence": reading.evidence.value,
                "graph_generation_id": reading.graph_generation_id,
            },
        )
