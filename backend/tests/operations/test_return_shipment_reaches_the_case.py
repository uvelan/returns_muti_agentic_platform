"""SHIP-01: a shipment reading reaches the case, and says what it was read from.

The write half is proven against real SQL Server in
`test_return_shipment_state_real_infra.py`, and the whole loop against real SQL,
Mongo and Neo4j in `test_return_shipment_graph_sync_real_infra.py`. What this
module owns is the decision layer between them: which outcomes propagate, what a
graph outage does, and that the fulfilment state the case is told is the same one
the legacy session path would have concluded.

That last point is the reason `fulfillment_status_for_shipment` was extracted
rather than restated. A second copy of "OBSERVED means IN_TRANSIT" is how one
screen ends up calling a return collected while the other calls it waiting, with
nothing to say which is right.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.return_shipment_state import (
    FULFILLMENT_EVIDENCE_FACT,
    FULFILLMENT_STATUS_FACT,
    ReturnShipmentStateService,
)
from return_platform.operations.sql_business_state import (
    SHIPMENT_UPDATE_APPLIED,
    SHIPMENT_UPDATE_DUPLICATE,
    SHIPMENT_UPDATE_STALE,
    ShipmentUpdate,
    ShipmentUpdateOutcome,
)
from return_platform.workflows.fulfillment_tracking import (
    ShipmentEvidence,
    ShipmentObservation,
)
from return_platform.workflows.stage_results import FulfillmentTrackingStatus

CASE_ID = "case-7f3a"
RMA = "RMA-7F3A"
TRACKING = "TRK-7F3A"
GENERATION = "gen-1"
AT = datetime(2026, 8, 14, 14, 0, tzinfo=UTC)


def _update(status: str = "IN_TRANSIT") -> ShipmentUpdate:
    return ShipmentUpdate(
        return_reference=RMA,
        tracking_reference=TRACKING,
        shipment_status=status,
        status_at=AT,
        carrier_code="UPS",
    )


#: `None` is a meaningful answer from `read_return_record_by_reference` -- an RMA
#: no case owns -- so "not supplied" needs its own value or the no-case test
#: silently gets the default record back and passes for the wrong reason.
_DEFAULT_RECORD: dict[str, Any] = {
    "return_record_id": "rec-1",
    "case_id": CASE_ID,
    "return_reference": RMA,
}


class _BusinessState:
    """The authoritative store, answering whatever the test needs it to."""

    def __init__(
        self,
        *,
        outcome: str = SHIPMENT_UPDATE_APPLIED,
        graph_generation_id: str | None = GENERATION,
        record: dict[str, Any] | None = _DEFAULT_RECORD,
    ) -> None:
        self._outcome = outcome
        self._graph_generation_id = graph_generation_id
        self._record = record
        self.updates: list[ShipmentUpdate] = []

    async def record_shipment_update(self, update: ShipmentUpdate) -> ShipmentUpdateOutcome:
        self.updates.append(update)
        return ShipmentUpdateOutcome(
            outcome=self._outcome,
            return_reference=update.return_reference,
            tracking_reference=update.tracking_reference,
            current_status=update.shipment_status,
            current_status_at=update.status_at,
            row_version=1,
            graph_generation_id=(
                self._graph_generation_id if self._outcome == SHIPMENT_UPDATE_APPLIED else None
            ),
        )

    async def read_return_record_by_reference(self, return_reference: str) -> dict[str, Any] | None:
        del return_reference
        return self._record


class _Repository:
    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []

    async def append_case_fact(self, **fields: Any) -> dict[str, Any]:
        self.facts.append(dict(fields))
        return dict(fields)


class _Observations:
    def __init__(self, observation: ShipmentObservation | Exception) -> None:
        self._observation = observation
        self.calls: list[str] = []

    async def observe(self, tracking_reference: str) -> ShipmentObservation:
        self.calls.append(tracking_reference)
        if isinstance(self._observation, Exception):
            raise self._observation
        return self._observation


def _observed(status: str = "intransit") -> ShipmentObservation:
    return ShipmentObservation(
        tracking_reference=TRACKING,
        evidence=ShipmentEvidence.OBSERVED,
        graph_generation_id=GENERATION,
        current_status=status,
        shipment_id="SHP-1",
        sync_request_id="sync-1",
    )


def _absent() -> ShipmentObservation:
    return ShipmentObservation(
        tracking_reference=TRACKING,
        evidence=ShipmentEvidence.ABSENT,
        graph_generation_id=GENERATION,
        sync_request_id="sync-1",
    )


def _service(
    business_state: _BusinessState,
    repository: _Repository,
    observations: _Observations | None = None,
) -> ReturnShipmentStateService:
    return ReturnShipmentStateService(
        business_state=business_state,
        repository=repository,
        observations=observations,
    )


def _named(repository: _Repository, fact_name: str) -> list[dict[str, Any]]:
    return [fact for fact in repository.facts if fact["fact_name"] == fact_name]


@pytest.mark.asyncio
async def test_an_observed_shipment_tells_the_case_the_return_is_in_transit() -> None:
    """The closure, at the decision layer: the graph moved, so the case knows.

    The fact lands on `case_facts`, which is what `RepositoryCaseStore.case_facts`
    projects into the agent's turn context -- the same route
    `record_support_outcome` uses to make an RMA appear in the associate's
    original conversation. A status that stopped at the store or the graph would
    never reach the person who asked where their return is.
    """
    business_state, repository = _BusinessState(), _Repository()
    observations = _Observations(_observed())

    outcome, reading = await _service(business_state, repository, observations).record_update(
        _update()
    )

    assert outcome.outcome == SHIPMENT_UPDATE_APPLIED
    assert observations.calls == [TRACKING]
    assert reading is not None
    assert reading.case_id == CASE_ID
    assert reading.status is FulfillmentTrackingStatus.IN_TRANSIT
    assert reading.evidence is ShipmentEvidence.OBSERVED
    assert reading.graph_generation_id == GENERATION

    status_facts = _named(repository, FULFILLMENT_STATUS_FACT)
    assert [fact["value"] for fact in status_facts] == ["IN_TRANSIT"]
    assert status_facts[0]["case_id"] == CASE_ID
    assert status_facts[0]["channel"] is FactChannel.SYSTEM
    assert status_facts[0]["acquisition_method"] is FactAcquisition.DERIVED


@pytest.mark.asyncio
async def test_the_evidence_the_status_was_concluded_from_is_recorded_too() -> None:
    """Two facts, because the status alone cannot carry the reason.

    `AWAITING_HANDOFF` reads the same whether nobody collected the parcel or the
    platform could not look. The evidence line is what keeps those apart, and it
    is the same string the session path writes onto its stage result.
    """
    business_state, repository = _BusinessState(), _Repository()

    await _service(business_state, repository, _Observations(_observed("delivered"))).record_update(
        _update("DELIVERED")
    )

    evidence = _named(repository, FULFILLMENT_EVIDENCE_FACT)
    assert [fact["value"] for fact in evidence] == [f"SHIPMENT_OBSERVED:{GENERATION}:delivered"]
    assert evidence[0]["acquisition_method"] is FactAcquisition.OBSERVED


@pytest.mark.asyncio
async def test_a_parcel_the_graph_has_never_seen_is_awaiting_handoff_not_in_transit() -> None:
    """A label printed and left on the counter.

    The platform minting a tracking reference is not evidence that anything
    moved. This is the inference W2.6 removed, and recording it as a case fact is
    where it would come back: an associate told `IN_TRANSIT` because an RMA
    exists is being told something nobody observed.
    """
    business_state, repository = _BusinessState(), _Repository()

    _, reading = await _service(business_state, repository, _Observations(_absent())).record_update(
        _update()
    )

    assert reading is not None
    assert reading.status is FulfillmentTrackingStatus.AWAITING_HANDOFF
    assert _named(repository, FULFILLMENT_STATUS_FACT)[0]["value"] == "AWAITING_HANDOFF"
    assert _named(repository, FULFILLMENT_EVIDENCE_FACT)[0]["value"] == (
        f"SHIPMENT_ABSENT:{GENERATION}"
    )


@pytest.mark.asyncio
async def test_a_graph_outage_never_fails_the_update_and_never_looks_like_data() -> None:
    """Fulfilment is best-effort, exactly as it is on the session path.

    The authoritative row is already committed by the time the reading is
    attempted, so raising here would leave the store ahead of everything that
    reads it with nothing recorded to say why. The reading comes back
    `UNAVAILABLE` and the evidence line names the failure, so an operator can
    tell an outage from a return still on the counter -- which is the whole
    reason `ShipmentEvidence` has three members rather than two.
    """
    business_state, repository = _BusinessState(), _Repository()
    observations = _Observations(TimeoutError("neo4j unreachable"))

    outcome, reading = await _service(business_state, repository, observations).record_update(
        _update()
    )

    assert outcome.outcome == SHIPMENT_UPDATE_APPLIED
    assert reading is not None
    assert reading.evidence is ShipmentEvidence.UNAVAILABLE
    assert reading.status is FulfillmentTrackingStatus.AWAITING_HANDOFF
    assert reading.graph_generation_id is None, "an unresolved lookup claimed a generation"
    assert _named(repository, FULFILLMENT_EVIDENCE_FACT)[0]["value"] == (
        "SHIPMENT_UNAVAILABLE:TIMEOUTERROR"
    )


@pytest.mark.asyncio
async def test_a_process_with_no_observation_port_records_that_it_did_not_look() -> None:
    """`NOT_ATTEMPTED` is a fourth reading, and it is not `ABSENT`.

    A process wired without the graph never looked. Recording `SHIPMENT_ABSENT`
    would claim a lookup that was never made, which is the same silent downgrade
    the evidence vocabulary exists to prevent.
    """
    business_state, repository = _BusinessState(), _Repository()

    _, reading = await _service(business_state, repository, None).record_update(_update())

    assert reading is not None
    assert reading.evidence is ShipmentEvidence.UNAVAILABLE
    assert _named(repository, FULFILLMENT_EVIDENCE_FACT)[0]["value"] == (
        "SHIPMENT_UNAVAILABLE:NOT_ATTEMPTED"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [SHIPMENT_UPDATE_DUPLICATE, SHIPMENT_UPDATE_STALE])
async def test_an_update_that_changed_nothing_tells_the_case_nothing(outcome: str) -> None:
    """The same gate the graph sync uses, one layer up.

    A duplicate submission of a status the associate has already been shown must
    not append a second fact, and a rejected stale update must not append one at
    all -- a case log in which a refused regressive event looks exactly like an
    accepted one is a log nobody can audit.
    """
    business_state = _BusinessState(outcome=outcome)
    repository = _Repository()
    observations = _Observations(_observed())

    result, reading = await _service(business_state, repository, observations).record_update(
        _update()
    )

    assert result.outcome == outcome
    assert result.applied is False
    assert reading is None
    assert repository.facts == []
    assert observations.calls == [], "a rejected update still read the graph"


@pytest.mark.asyncio
async def test_a_shipment_for_an_rma_no_case_owns_is_read_but_not_recorded() -> None:
    """`dbo.return_tracking` is keyed on the RMA and needs no `return_record` row.

    So a shipment can legitimately be recorded for an RMA the case store has
    never seen. Reported rather than raised: the reading is still the truth, and
    a caller can tell "nobody was told" from "the reading failed".
    """
    business_state = _BusinessState(record=None)
    repository = _Repository()

    _, reading = await _service(
        business_state, repository, _Observations(_observed())
    ).record_update(_update())

    assert reading is not None
    assert reading.case_id is None
    assert reading.status is FulfillmentTrackingStatus.IN_TRANSIT
    assert repository.facts == []


@pytest.mark.asyncio
async def test_the_same_reading_twice_rewrites_one_fact_rather_than_appending_two() -> None:
    """`append_case_fact` is insert-only and `latest_case_facts` projects the newest.

    So an unkeyed append would grow the log without adding information. The id is
    derived from the RMA, the parcel and the reading, which is the same
    convention `record_support_outcome` uses for an RMA fact.
    """
    business_state, repository = _BusinessState(), _Repository()
    service = _service(business_state, repository, _Observations(_observed()))

    await service.record_update(_update())
    await service.record_update(_update())

    status_ids = {fact["fact_id"] for fact in _named(repository, FULFILLMENT_STATUS_FACT)}
    evidence_ids = {fact["fact_id"] for fact in _named(repository, FULFILLMENT_EVIDENCE_FACT)}
    assert len(status_ids) == 1
    assert len(evidence_ids) == 1


@pytest.mark.asyncio
async def test_the_canonical_reading_is_reachable_without_submitting_an_event() -> None:
    """The case screen and the associate's turn both want the current reading.

    Neither of them is submitting a carrier event, so the read has to stand on
    its own rather than only existing as a side effect of a write.
    """
    business_state, repository = _BusinessState(), _Repository()

    reading = await _service(
        business_state, repository, _Observations(_observed())
    ).observe_shipment(return_reference=RMA, tracking_reference=TRACKING)

    assert business_state.updates == [], "a read submitted an update"
    assert reading.status is FulfillmentTrackingStatus.IN_TRANSIT
    assert _named(repository, FULFILLMENT_STATUS_FACT)[0]["value"] == "IN_TRANSIT"
