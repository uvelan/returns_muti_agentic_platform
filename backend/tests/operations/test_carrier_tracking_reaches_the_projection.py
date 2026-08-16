"""D1 and D4: a carrier's tracking number reaches the case, and two parcels fit.

WHAT WAS BROKEN (D1)
--------------------
`POST /api/return-shipments/{ref}/updates` answered `APPLIED`, wrote
`dbo.return_tracking`, synchronized the graph, resolved the owning case and
appended `fulfillment_status` and `shipment_evidence` as case facts. Every one of
those steps worked. And the tracking reference still never appeared on
`returnRecords[].shipments`, so `awaiting` stayed `['LABEL', 'TRACKING']` for a
return the carrier had demonstrably collected.

The reason is that facts reach `facts[]` and nothing else. `shipments[]` is
assembled from the case's *return records*, `project_shipments` read one
`trackingReference` column off each, and nothing joined `dbo.return_tracking` into
the aggregate at all. Only a second Support `return-outcome` repeating the
tracking number closed it -- so a Copilot polling for tracking after a carrier
update polled forever, which is the exact failure mode this programme exists to
remove: the platform holds the answer and the read model cannot see it.

WHAT WAS BROKEN (D4)
--------------------
`project_shipments` returned at most one `ShipmentProjection`, out of one column.
A split return going back in two parcels was unrepresentable -- not
mis-projected, *inexpressible*. `dbo.return_tracking` has been RMA-scoped and
plural since `006_return_shipment_state.sql`; the read model had one slot.

Both are closed the same way, and the way matters. **Nothing is invented.** No
synthetic shipment is minted to carry a label, no parcel is conjured from a
tracking number Support typed, and the carrier's own status string is not mapped
onto `ShipmentStatus` -- the update route documents that vocabulary as the
carrier's, `ShipmentStatus` is a closed five, and a mapping would either drop the
statuses it did not recognise or guess at them. Where the platform cannot say, it
says nothing: an RMA with two parcels and one label attributes that label to
neither, and `LABEL` stays outstanding.

WHY THIS MODULE DRIVES THE REAL REPOSITORY METHOD
-------------------------------------------------
The defect was a *gap between two stores*, so a test that asserted on
`project_shipments` alone would pass on the day nothing wrote the field it reads
-- which is precisely the state the platform was found in. So the chain here is
the shipped one: `ReturnShipmentStateService.record_update` ->
`CaseRepository.record_case_shipment` (the real method, its real update
documents) -> `assemble_case_projection_state` -> `project_case`. Only the
collections are doubles, and they implement exactly the operator set the writer
uses; the atomicity those doubles cannot prove is settled against a real replica
set in `test_case_revision_atomicity_real_infra.py`, and the SQL half against
real SQL Server in `test_return_shipment_state_real_infra.py`.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.operations.case_projection import (
    AwaitingDimension,
    ReturnArtifactType,
    project_case,
)
from return_platform.operations.case_projection.assembly import (
    CaseAggregateDocuments,
    assemble_case_projection_state,
    project_shipments,
)
from return_platform.operations.case_repository import CaseRepository
from return_platform.operations.models import CaseStatus
from return_platform.operations.return_shipment_state import ReturnShipmentStateService
from return_platform.operations.sql_business_state import (
    SHIPMENT_UPDATE_APPLIED,
    ShipmentUpdate,
    ShipmentUpdateOutcome,
)
from return_platform.workflows.fulfillment_tracking import ShipmentEvidence, ShipmentObservation
from tests.operations.test_case_projection_assembly import (
    RELEASED_REQUIREMENTS,
    case_document,
    fact_document,
    latest,
    record_document,
)

CASE_ID = "CASE-1"
RECORD_ID = "RR-1"
RMA = "RMA-D1-0001"
FIRST = "1Z-PARCEL-AAA"
SECOND = "1Z-PARCEL-BBB"
AT = datetime(2026, 8, 15, 13, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 15, 15, 0, tzinfo=UTC)


def approved_prepaid_parcel() -> dict[str, dict[str, Any]]:
    """The facts that make `PREPAID_PARCEL` the requirement set for the case.

    `PREPAID_PARCEL` requires `RMA`, `LABEL` and `TRACKING`, which is what makes
    `awaiting` say something about a package at all -- a `CUSTOMER_KEEP` return
    would report nothing outstanding whether or not this defect were fixed.
    """
    return latest(
        fact_document("policy_route", "STANDARD_RETURN"),
        fact_document("policy_decision", "APPROVE"),
        fact_document("policy_effective_decision", "APPROVE"),
        fact_document("approved_return_method", "PREPAID_PARCEL"),
    )


# ---------------------------------------------------------------------------
# The stores.
#
# `_Records` implements exactly the operators `record_case_shipment` issues and
# nothing else, so an operator the writer starts using without this double
# learning it is a `NotImplementedError` rather than a silently ignored clause.
# ---------------------------------------------------------------------------


def _element_matches(element: Mapping[str, Any], conditions: Mapping[str, Any]) -> bool:
    for field, condition in conditions.items():
        actual = element.get(field)
        if isinstance(condition, dict):
            for operator, operand in condition.items():
                if operator != "$ne":  # pragma: no cover - an operator the double has not met
                    raise NotImplementedError(operator)
                if actual == operand:
                    return False
        elif actual != condition:
            return False
    return True


class _Records:
    """The `return_records` collection, with array-update semantics."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = [dict(document) for document in documents]

    def _select(self, query: Mapping[str, Any]) -> tuple[dict[str, Any], int] | None:
        """The first matching document and the index the positional `$` names."""
        for document in self.documents:
            position = -1
            matched = True
            for key, condition in query.items():
                if key == "shipments" and isinstance(condition, dict) and "$elemMatch" in condition:
                    entries = document.get("shipments") or []
                    position = next(
                        (
                            index
                            for index, entry in enumerate(entries)
                            if _element_matches(entry, condition["$elemMatch"])
                        ),
                        -1,
                    )
                    matched = position >= 0
                elif key == "shipments.trackingReference":
                    # Mongo's array semantics: `$ne` on a dotted array path
                    # matches a document in which *no* element equals the
                    # operand -- including one that carries no array at all.
                    operand = condition["$ne"]
                    entries = document.get("shipments") or []
                    matched = all(entry.get("trackingReference") != operand for entry in entries)
                else:
                    matched = document.get(key) == condition
                if not matched:
                    break
            if matched:
                return document, position
        return None

    async def update_one(
        self, query: Mapping[str, Any], update: Mapping[str, Any], session: Any = None
    ) -> Any:
        del session
        selected = self._select(query)
        if selected is None:
            return type("_Result", (), {"matched_count": 0})()
        document, position = selected
        for field, value in update.get("$set", {}).items():
            head, _, tail = field.partition(".$.")
            if tail:
                document[head][position][tail] = value
            else:
                document[field] = value
        for field, value in update.get("$inc", {}).items():
            document[field] = document.get(field, 0) + value
        for field, value in update.get("$push", {}).items():
            document.setdefault(field, []).append(dict(value))
        return type("_Result", (), {"matched_count": 1})()

    async def find_one(
        self, query: Mapping[str, Any], projection: Any = None, session: Any = None
    ) -> dict[str, Any] | None:
        del projection, session
        selected = self._select(query)
        return None if selected is None else copy.deepcopy(selected[0])


class _Facts:
    """The `case_facts` collection. Insert-only, exactly as production is."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def insert_one(self, document: Mapping[str, Any], session: Any = None) -> None:
        del session
        self.documents.append(dict(document))


class _Cases:
    """The `cases` collection, for the revision bump and nothing else."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document

    async def update_one(
        self, query: Mapping[str, Any], update: Mapping[str, Any], session: Any = None
    ) -> Any:
        del session
        if self.document.get("caseId") != query.get("caseId"):
            return type("_Result", (), {"matched_count": 0})()
        for field, value in update.get("$inc", {}).items():
            self.document[field] = self.document.get(field, 0) + value
        self.document.update(update.get("$set", {}))
        return type("_Result", (), {"matched_count": 1})()


class _Client:
    def start_session(self) -> Any:
        @asynccontextmanager
        async def _session() -> AsyncIterator[Any]:
            yield _Transactional()

        return _session()


class _Transactional:
    async def with_transaction(self, callback: Any) -> Any:
        return await callback(object())


class _BusinessState:
    """`dbo.return_tracking` and `dbo.return_record`, as the SQL reads answer them."""

    def __init__(self, *, record: dict[str, Any] | None = None) -> None:
        self._record = (
            {"return_record_id": RECORD_ID, "case_id": CASE_ID, "return_reference": RMA}
            if record is None
            else record
        )
        self.rows: list[dict[str, Any]] = []

    async def record_shipment_update(self, update: ShipmentUpdate) -> ShipmentUpdateOutcome:
        self.rows = [
            row for row in self.rows if row["tracking_reference"] != update.tracking_reference
        ] + [
            {
                "return_reference": update.return_reference,
                "tracking_reference": update.tracking_reference,
                "carrier_code": update.carrier_code,
                "tracking_status": update.shipment_status,
                "event_at": update.status_at,
            }
        ]
        return ShipmentUpdateOutcome(
            outcome=SHIPMENT_UPDATE_APPLIED,
            return_reference=update.return_reference,
            tracking_reference=update.tracking_reference,
            current_status=update.shipment_status,
            current_status_at=update.status_at,
            row_version=1,
            graph_generation_id="gen-1",
        )

    async def read_return_record_by_reference(self, return_reference: str) -> dict[str, Any] | None:
        del return_reference
        return self._record

    async def read_shipment_state(self, return_reference: str) -> list[dict[str, Any]]:
        del return_reference
        return sorted(self.rows, key=lambda row: row["event_at"], reverse=True)


class _Observations:
    async def observe(self, tracking_reference: str) -> ShipmentObservation:
        return ShipmentObservation(
            tracking_reference=tracking_reference,
            evidence=ShipmentEvidence.OBSERVED,
            graph_generation_id="gen-1",
            current_status="intransit",
        )


class _Harness:
    """The case store, the return store, and the service that joins them."""

    def __init__(self, *, record: dict[str, Any]) -> None:
        self.case = case_document(
            caseId=CASE_ID, status=CaseStatus.POLICY_APPROVED.value, version=3
        )
        self.records = _Records([record])
        self.cases = _Cases(self.case)
        self.facts = _Facts()
        self.repository = CaseRepository()
        self.repository.cases = self.cases  # type: ignore[assignment]
        self.repository.case_facts = self.facts  # type: ignore[assignment]
        self.repository.return_records = self.records  # type: ignore[assignment]
        self.repository._client = _Client()  # type: ignore[assignment]
        self.business_state = _BusinessState()
        self.service = ReturnShipmentStateService(
            business_state=self.business_state,
            repository=self.repository,
            observations=_Observations(),
        )

    async def carrier_filed(
        self, tracking_reference: str, *, carrier: str | None = "UPS", at: datetime = AT
    ) -> None:
        await self.service.record_update(
            ShipmentUpdate(
                return_reference=RMA,
                tracking_reference=tracking_reference,
                shipment_status="IN_TRANSIT",
                status_at=at,
                tracking_type="PPL",
                carrier_code=carrier,
            )
        )

    @property
    def record(self) -> dict[str, Any]:
        return self.records.documents[0]

    def projected(self) -> Any:
        """The case exactly as `GET /api/cases/{id}` would serve it."""
        state = assemble_case_projection_state(
            CaseAggregateDocuments(
                case=self.case,
                facts=approved_prepaid_parcel(),
                return_records=tuple(copy.deepcopy(self.records.documents)),
            )
        )
        return project_case(state, requirements=RELEASED_REQUIREMENTS)


def _harness(**overrides: Any) -> _Harness:
    return _Harness(
        record=record_document(
            RECORD_ID, caseId=CASE_ID, returnReference=RMA, status="AUTHORIZED", **overrides
        )
    )


# ---------------------------------------------------------------------------
# 1. D1: the carrier's tracking number reaches the projection.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_applied_carrier_update_puts_the_parcel_on_the_projected_record() -> None:
    """The closure. One carrier event, and the package is on the case.

    Nothing here submits a Support outcome. The tracking number's only route
    into the aggregate is the carrier update itself, which is what the defect
    denied it.
    """
    harness = _harness(labelReference="LBL-1")

    await harness.carrier_filed(FIRST)

    projected = harness.projected()
    (record,) = projected.records()
    assert record.shipments is not None
    (parcel,) = record.shipments
    assert parcel.trackingNumber == FIRST
    assert parcel.carrier == "UPS"
    # The parcel's identity is the tracking number: the only handle persistence
    # holds on a package, and the one that stays this package's when a second
    # arrives.
    assert parcel.shipmentId == FIRST


@pytest.mark.asyncio
async def test_the_polling_copilot_stops_waiting_for_tracking() -> None:
    """The consequence, stated as the client sees it.

    `awaiting` is the whole reason the previous test matters: a Copilot polling
    `GET /api/cases/{id}` waits on this list, and before the fix it waited on a
    `TRACKING` that a carrier event could never satisfy.
    """
    harness = _harness(labelReference="LBL-1")
    assert AwaitingDimension.TRACKING in harness.projected().awaiting

    await harness.carrier_filed(FIRST)

    projected = harness.projected()
    assert AwaitingDimension.TRACKING not in projected.awaiting
    # And `LABEL` with it: the label was already on the RMA, and it now has the
    # one package it can honestly be attributed to.
    assert AwaitingDimension.LABEL not in projected.awaiting
    assert projected.awaiting == ()
    assert projected.businessComplete is True


@pytest.mark.asyncio
async def test_a_carrier_update_alone_does_not_complete_a_return_that_has_no_label() -> None:
    """The parcel is visible; the missing document still is missing.

    Guards the fix against overreach. `TRACKING` is satisfied because a package
    exists and carries a number; `LABEL` is not, because the requirement is that
    every package is papered and this one has nothing on it.
    """
    harness = _harness()

    await harness.carrier_filed(FIRST)

    projected = harness.projected()
    assert AwaitingDimension.TRACKING not in projected.awaiting
    assert AwaitingDimension.LABEL in projected.awaiting
    assert projected.businessComplete is False


@pytest.mark.asyncio
async def test_the_case_revision_moves_with_the_parcel() -> None:
    """Plan sect. 6.5. A package appearing changes the projection, so it bumps.

    A client that could not see the package in the revision could not tell a
    fresh projection from a stale one, and would keep serving the case it
    already had. That the bump shares the child write's transaction -- the half
    a double cannot prove, because it cannot roll anything back -- is asserted
    at the wiring level in `test_case_revision_atomicity.py` and against a real
    replica set in its `_real_infra` sibling.
    """
    harness = _harness()
    before_case = int(harness.case["version"])
    stamped = harness.case["updatedAt"]

    await harness.carrier_filed(FIRST)

    assert int(harness.case["version"]) > before_case
    assert harness.case["updatedAt"] != stamped
    assert int(harness.record["version"]) == 1


@pytest.mark.asyncio
async def test_replaying_the_same_carrier_event_records_no_second_parcel() -> None:
    """Idempotent, and a replay writes nothing at all.

    Asserted on the *record's* revision rather than the case's, because the
    facts are a separate writer with a separate idempotency mechanism -- a
    unique-indexed `factId` whose duplicate aborts the transaction -- and this
    is a statement about the parcel writer alone.

    It matters because `observe_shipment` is also the canonical read, reachable
    without any carrier event at all: a read that appended a parcel, or that
    invalidated every client's cache, would be worse than the defect it closes.
    """
    harness = _harness()
    await harness.carrier_filed(FIRST)
    after_first = int(harness.record["version"])

    await harness.carrier_filed(FIRST)
    await harness.service.observe_shipment(return_reference=RMA, tracking_reference=FIRST)

    assert [entry["trackingReference"] for entry in harness.record["shipments"]] == [FIRST]
    assert int(harness.record["version"]) == after_first


@pytest.mark.asyncio
async def test_a_later_scan_with_no_carrier_code_does_not_blank_the_one_recorded() -> None:
    """The merge rule `RETURN_RECORD_MERGED_FIELDS` states, applied to a parcel.

    A feed that files a scan without a carrier code has said nothing about the
    carrier. Writing that silence over one already recorded would delete it,
    which is the failure `008_return_record_carrier.sql` refused to leave in
    place for the RMA's carrier.
    """
    harness = _harness()
    await harness.carrier_filed(FIRST, carrier="UPS")

    await harness.carrier_filed(FIRST, carrier=None, at=LATER)

    (parcel,) = harness.projected().records()[0].shipments
    assert parcel.carrier == "UPS"


@pytest.mark.asyncio
async def test_a_corrected_carrier_reaches_the_parcel_it_belongs_to() -> None:
    """A carrier the feed *does* state replaces the one stored, and bumps."""
    harness = _harness()
    await harness.carrier_filed(FIRST, carrier="UPS")
    after_first = int(harness.record["version"])

    await harness.carrier_filed(FIRST, carrier="FEDEX", at=LATER)

    (parcel,) = harness.projected().records()[0].shipments
    assert parcel.carrier == "FEDEX"
    assert int(harness.record["version"]) > after_first


@pytest.mark.asyncio
async def test_no_parcel_is_recorded_for_an_rma_the_case_store_does_not_own() -> None:
    """`dbo.return_tracking` needs no `dbo.return_record` row, and this is that state.

    A real state rather than an error -- the service already reports it -- and
    the read model must not acquire a package on the strength of it.
    """
    harness = _harness()
    harness.business_state._record = None

    await harness.carrier_filed(FIRST)

    assert "shipments" not in harness.record
    assert harness.projected().records()[0].shipments is None


# ---------------------------------------------------------------------------
# 2. D4: two parcels on one RMA.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_split_return_projects_two_parcels_each_with_its_own_identity() -> None:
    """The shape that was inexpressible. Two parcels, one RMA, nothing invented.

    Both are real: each entered as its own carrier event against its own
    tracking number, and `dbo.return_tracking` has held them as separate rows
    since `006_return_shipment_state.sql`. What was missing was anywhere for the
    second one to go.
    """
    harness = _harness()

    await harness.carrier_filed(FIRST, carrier="UPS")
    await harness.carrier_filed(SECOND, carrier="FEDEX", at=LATER)

    (record,) = harness.projected().records()
    assert record.shipments is not None
    assert [parcel.trackingNumber for parcel in record.shipments] == [FIRST, SECOND]
    assert [parcel.carrier for parcel in record.shipments] == ["UPS", "FEDEX"]
    assert record.shipments[0].shipmentId != record.shipments[1].shipmentId


@pytest.mark.asyncio
async def test_one_label_on_two_parcels_papers_neither_and_leaves_label_outstanding() -> None:
    """The D24 standard, applied here: the contract says so rather than guessing.

    `dbo.return_record` holds one label reference, and an RMA with two parcels
    offers no way to say which parcel it papers. Stamping it on the first is the
    audit's `labels[0]` reading; stamping it on both claims a document that was
    never printed twice. Attributing it to neither is the third answer and the
    only true one -- and `LABEL` stays outstanding, which is exactly what
    `_every_package_papered` already says it should: one label printed for two
    parcels leaves the second parcel with nothing on it.
    """
    harness = _harness(labelReference="LBL-1")

    await harness.carrier_filed(FIRST)
    await harness.carrier_filed(SECOND, at=LATER)

    projected = harness.projected()
    (record,) = projected.records()
    (label,) = record.artifacts or ()
    assert label.artifactId == "LBL-1"
    # The label is served -- an operator has it in their hand -- and attributed
    # to no package.
    assert label.shipmentId is None
    assert record.active_artifacts(ReturnArtifactType.SHIPPING_LABEL) == (label,)
    for parcel in record.shipments or ():
        assert (
            record.active_artifacts_for_shipment(
                ReturnArtifactType.SHIPPING_LABEL, parcel.shipmentId
            )
            == ()
        )
    assert AwaitingDimension.LABEL in projected.awaiting
    assert AwaitingDimension.TRACKING not in projected.awaiting
    assert projected.businessComplete is False


# ---------------------------------------------------------------------------
# 3. The union of the two producers, and what neither of them may invent.
# ---------------------------------------------------------------------------


def test_support_and_the_carrier_naming_one_number_are_one_parcel() -> None:
    """Two statements about the same package, not two packages.

    Support states a tracking number on the RMA before any carrier has scanned
    it, and `persist_case_return_records` deliberately writes no
    `dbo.return_tracking` row for that statement. So the same parcel can be
    named twice, and keying the union on the tracking reference is what stops it
    projecting as a split return that never happened.
    """
    shipments = project_shipments(
        record_document(
            RECORD_ID,
            trackingReference=FIRST,
            carrier="UPS",
            shipments=[{"trackingReference": FIRST, "carrier": "FEDEX"}],
        )
    )

    assert shipments is not None
    assert len(shipments) == 1
    # The carrier that filed the scan outranks the carrier Support predicted:
    # one is an observation of this parcel, the other an expectation of it.
    assert shipments[0].carrier == "FEDEX"


def test_a_parcel_the_carrier_filed_without_a_code_falls_back_to_the_rmas_carrier() -> None:
    """Support's answer is about this RMA, so it is about every parcel under it.

    Unlike a case-level carrier, which would be a statement about a *different*
    RMA's parcels -- the cross-attribution `008_return_record_carrier.sql`
    refused.
    """
    shipments = project_shipments(
        record_document(
            RECORD_ID, carrier="UPS", shipments=[{"trackingReference": SECOND, "carrier": None}]
        )
    )

    assert shipments is not None
    assert shipments[0].carrier == "UPS"


def test_an_entry_that_names_no_parcel_projects_no_parcel() -> None:
    """A package with a null tracking number is the one shipment shape forbidden.

    It is what let a screen show a parcel nobody had tendered, and an entry that
    somehow carried no reference must produce nothing rather than a shipment of
    nulls.
    """
    assert (
        project_shipments(
            record_document(RECORD_ID, shipments=[{"trackingReference": "  ", "carrier": "UPS"}])
        )
        is None
    )
    assert project_shipments(record_document(RECORD_ID, shipments=[])) is None
    assert project_shipments(record_document(RECORD_ID, shipments="1Z-NOT-AN-ARRAY")) is None


@pytest.mark.asyncio
async def test_the_carriers_own_status_vocabulary_is_never_mapped_onto_the_contract() -> None:
    """`shipmentStatus` still has no producer, and that is the honest answer.

    `ShipmentUpdateRequest.shipmentStatus` is deliberately un-enumerated because
    the carrier's status vocabulary is the carrier's; `ShipmentStatus` is a
    closed five. Any mapping between them would drop the statuses it did not
    recognise or guess at them, so the platform's *reading* of the status
    travels as the `fulfillment_status` fact instead and the package field stays
    absent.
    """
    harness = _harness()

    await harness.carrier_filed(FIRST)

    (parcel,) = harness.projected().records()[0].shipments
    assert parcel.shipmentStatus is None
    assert parcel.serviceLevel is None
    assert parcel.estimatedDeliveryAt is None
    # The status did reach the case, as the platform's *reading* of it, under
    # provenance a reader can inspect. Both facts, beside the parcel and not
    # instead of it.
    assert {str(document["factName"]) for document in harness.facts.documents} == {
        "fulfillment_status",
        "shipment_evidence",
    }
