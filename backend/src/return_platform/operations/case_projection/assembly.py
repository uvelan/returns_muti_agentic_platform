"""One case's persisted documents -> one `CaseProjectionState`. Pure, and absence-preserving.

The read half of plan sect. 6.3. `case_projection/__init__.py` says what
assembles a `CaseProjectionState` from Mongo is the repository's problem; this
module is the part of that problem which is a *function* rather than IO, so it
can be tested against document literals with no database in the room.
`CaseRepository.load_case_projection_state` does the four reads and calls
`assemble_case_projection_state`; nothing here opens a connection.

**Absence is data, and this module is where the distinction is either kept or
lost.** Two rules, applied without exception:

* A block is `None` when there is nothing to say. It is never constructed with
  every field null -- `shipment: {...all nulls}` asserts "there is a package and
  we know nothing about it", which is a different and much worse statement than
  "there is no package". The audit's fabricated `TRK-98421049281` is what
  happens downstream when those two are indistinguishable.
* A blank string is not a value. `returnReference: ""` would satisfy "an RMA
  exists" and complete a case that has none, so every string goes through
  `_text`, which reads blank as absent.

**One legacy single-value field is projected into a plural shape** (plan
sect. 6.7): `ReturnRecordView.labelReference` becomes one active
`ReturnArtifactProjection`, `None` when the field is null -- never a
one-element tuple holding a null-filled object.

A record with a label and no tracking -- a real shape in the dev database,
`RMA-OPS01-CD4364` -- therefore projects **one artifact and zero shipments**.
That artifact lands on `returnRecords[].artifacts[]` with `shipmentId: null`,
which is the contract saying exactly what persistence holds: this RMA has this
label and no package is known yet. It used to have nowhere to go, because
artifacts were reachable only through a shipment; minting a package to carry it
would have meant inventing a shipment id and a status the platform does not
have, so the contract was changed instead of the data. The case still reports
both `LABEL` and `TRACKING` outstanding, because the `LABEL` requirement is that
every package is papered and there is no package to paper.

**Packages are genuinely plural and come from two producers.** `project_shipments`
unions the tracking reference Support stated on the record with the
`shipments[]` entries `CaseRepository.record_case_shipment` writes from APPLIED
carrier updates, keyed on the tracking reference so that one parcel named by
both is one parcel. Two parcels on one RMA -- a split return -- are therefore
representable without inventing anything, and a carrier event now reaches the
read model instead of stopping at `dbo.return_tracking`.

**What has no persistence today, and therefore projects as `None`:** `pickup`
(pickup requests are session-keyed, never case-keyed); `ShipmentProjection`'s
`shipmentStatus`, `carrier`, `serviceLevel` and `estimatedDeliveryAt`, each with
its missing producer named on `project_shipments`; and
`PolicyEvaluationProjection`'s `rateBasisPoints` and `rateSource`, whose values
exist on `PolicyOutcome` and are simply not recorded to the fact log yet
(`_RESTOCKING_RATE_FACT`). Each is an absence rather than a default, so the
stage and completion rules read them as unknown and neither completes nor
advances on them.

**`settlement` is the one block that is stated rather than omitted.** There is
no settlement producer anywhere in the platform -- nothing issues a credit memo,
computes a settled amount or records a settlement date -- and every assembled
case therefore carries `SettlementProjection(status=NOT_INTEGRATED)`. That is
not the "all fields null" shape forbidden above: `status` is required and holds
a real answer. Absence here would say "not computed", inviting a reader to wait
for a number that is never coming; `NOT_INTEGRATED` says the integration does
not exist, which is the true and more useful statement, and it is what
`project_case_status` reads to send a *completed* `CLOSED` case to
`COMPLETED_EXTERNAL_SETTLEMENT` rather than to `COMPLETED`. It is deliberately
**not** `NOT_STARTED`, which would assert a producer that has not run.

**How a case closed is read before whether it settled.** `ReturnCaseWorkflow`
writes one `CLOSED` for two opposite endings -- the return finished, and Support
refused it -- so `project_support_outcome` reads the `support_outcome` fact and
hands it to `project_case_status` beside the settlement. Without it a refused
warranty claim projected as `COMPLETED_EXTERNAL_SETTLEMENT`, announcing a credit
settled elsewhere for a return nobody authorized, while `awaiting` still named
the verification that refusal had answered.

**Warehouse receipt has no case-keyed producer, and this module does not invent
one.** `project_warehouse` fills `facilityId`, `bayId` and `bayReason` from the
bay facts `ReturnCaseWorkflow` writes, and leaves the seven receipt fields
`None`; the reasons are enumerated on that function. The consequence for the
stage precedence is stated there too, and it is that `WAREHOUSE_RECEIVING`
remains unreachable from Mongo alone.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from return_platform.operations.case_projection.contract import (
    ApprovedItemProjection,
    CaseFactProjection,
    CaseProjectionState,
    ConfirmedOrderProjection,
    CustomerProjection,
    PickupProjection,
    PolicyEvaluationProjection,
    PolicyOverrideProjection,
    ReturnArtifactProjection,
    ReturnRecordProjection,
    SelectedItemProjection,
    SettlementProjection,
    ShipmentProjection,
    SupportProjection,
    WarehouseProjection,
)
from return_platform.operations.case_projection.status_mapping import project_case_status
from return_platform.operations.case_projection.vocabulary import (
    ReturnArtifactType,
    SettlementStatus,
    SupportOutcome,
)
from return_platform.operations.models import normalize_utc_datetime
from return_platform.policy import (
    EligibilityDecision,
    FeeAmountSource,
    PolicyCondition,
    PolicyReasonCode,
    PolicyRoute,
    PolicyRule,
)

__all__ = [
    "RETURN_METHOD_FACT_NAMES",
    "RETURN_RECORD_SHIPMENTS_FIELD",
    "SUPPORT_OUTCOME_FACT",
    "SUPPORT_OUTCOME_REASON_FACT",
    "SUPPORT_WORK_ITEMS_COLLECTION",
    "CaseAggregateDocuments",
    "assemble_case_projection_state",
    "project_confirmed_order",
    "project_customer",
    "project_facts",
    "project_policy_evaluation",
    "project_return_artifacts",
    "project_return_record",
    "project_return_records",
    "project_selected_items",
    "project_shipments",
    "project_support",
    "project_support_outcome",
    "project_warehouse",
]

#: The collection `operations/return_support/service.py` owns. Named here
#: because the case projection reads it and a second spelling of a collection
#: name is a read that silently finds nothing.
SUPPORT_WORK_ITEMS_COLLECTION: Final = "support_work_items"

#: The array on a return record document that holds one entry per parcel.
#:
#: Named here for the reason the collection above is: `CaseRepository.
#: record_case_shipment` writes it and `project_shipments` reads it, and a second
#: spelling of the field is a read that silently finds nothing -- which is
#: exactly the failure this field was added to close.
#:
#: The record's legacy `trackingReference` column is **not** replaced by it.
#: Support states a tracking number on an RMA without any carrier ever having
#: filed a scan, and `persist_case_return_records` deliberately writes no
#: `dbo.return_tracking` row for that statement, because it would have to invent
#: the `tracking_type` and `event_at` such a row requires (see
#: `008_return_record_carrier.sql`). So the two are different statements about
#: possibly the same parcel, and `project_shipments` takes their union keyed on
#: the tracking reference rather than letting either win.
RETURN_RECORD_SHIPMENTS_FIELD: Final = "shipments"

#: Where a record's return method comes from, in order of authority.
#:
#: **Neither exists in the case aggregate today.** `ReturnRecordView` has no
#: `returnMethod` column and no writer records one as a case fact -- the method
#: lives on the legacy `ReturnSessionView.approvedReturnMethod` and in the agent
#: DTOs, neither of which the case path reads. So every record currently
#: projects `returnMethod: None`, the completion profile stays unresolved,
#: `awaiting` reports `RETURN_METHOD`, and `businessComplete` can never become
#: true. That is the truthful answer, and the missing writer is specified in
#: `docs/RETURN_METHOD_PERSISTENCE_SPEC.md`: a per-record column plus a
#: per-record `return_method` fact, written by `record_support_outcome`.
#:
#: These names are the two places the assembler finds the method the moment
#: something writes it. Record-level first, and this fallback second: it is
#: correct only for a single-record case, because `latest_case_facts` is a
#: latest-per-*name* projection and two RMAs writing two methods would leave
#: only the newer. That is why the specification puts the authority on the
#: record and not here.
RETURN_METHOD_FACT_NAMES: Final[tuple[str, ...]] = (
    "approved_return_method",
    "return_method",
)

#: The observed facts that identify the customer. `customer_id` and
#: `customer_name` are the field names `smart_question.fields` already declares
#: and `RepositoryCaseStore._append_observed` already writes, not new ones.
_CUSTOMER_REFERENCE_FACT: Final = "customer_id"
_CUSTOMER_NAME_FACT: Final = "customer_name"

#: Written by `RepositoryCaseStore.confirm` on the turn that creates the case.
#: Its timestamp is the only record of *when* the associate confirmed.
_CONFIRMED_ORDER_FACT: Final = "confirmed_order_reference"

#: What Support answered, and why. Written by
#: `ReturnCaseActivities.record_support_outcome` -- which is handed `rejected`
#: and `reason` on every notice and, until this existed, discarded both.
#:
#: Named here and imported by the writer rather than spelled twice, for the
#: reason `SUPPORT_WORK_ITEMS_COLLECTION` is: this is a read keyed on a string,
#: and a second spelling of it is a read that silently finds nothing. That
#: failure would be invisible and expensive here -- a refused case whose
#: outcome fact the projection could not find reads as a *completed* one, which
#: is exactly the defect the fact was added to close.
#:
#: The reason is a separate fact rather than a field of the first because the
#: fact log is one value per name: folding them together would put a free-text
#: sentence where a vocabulary belongs and leave `SupportOutcome` unparseable.
SUPPORT_OUTCOME_FACT: Final = "support_outcome"
SUPPORT_OUTCOME_REASON_FACT: Final = "support_outcome_reason"

#: Bay facts, written by `ReturnCaseWorkflowActivities._record_bay_facts` from
#: the `BayResultNotice` that `CaseBayPlacement.recommend` produced. The
#: contract's `WarehouseProjection` docstring names these as its source: there
#: is no separate bay service to read.
#:
#: Three of the seven the workflow writes are read here. The other four --
#: `bay_return_location`, `bay_confidence_millionths`, `bay_evidence_reference`
#: and `bay_capacity_evidence` -- are placement *provenance* rather than
#: warehouse state, and they stay on `facts[]`, which serves the whole
#: latest-per-name projection. `bay_return_location` in particular is
#: `warehouse/bay` composed from the two fields already projected here
#: (`return_location_of`), and the authoritative return location -- what Support
#: actually instructed -- is `returnRecords[].returnLocation`, which the two are
#: explicitly allowed to differ from. A second copy of it on the warehouse block
#: would be a second place for a reader to look and a writer to disagree with.
_BAY_FACILITY_FACT: Final = "bay_warehouse_reference"
_BAY_REFERENCE_FACT: Final = "bay_reference"
_BAY_REASON_FACT: Final = "bay_reason"

#: Where the restocking rate reaches the projection from.
#:
#: `FeeDetermination.rate_basis_points` and `.rate_source` are on
#: `PolicyOutcome`, produced by `policy.evaluator._seller_restocking_fee` from
#: `restocking_fee.seller_schedule` in the active release, and
#: `ReturnCaseWorkflowActivities._record_policy_outcome` appends both under the
#: names below beside `policy_restocking_fee_applies` and
#: `policy_restocking_fee_waived`.
#:
#: **Recorded as evaluated, and read from the log rather than from the release.**
#: The rate belongs to the release the case was decided under. Resolving
#: `restocking_fee.seller_schedule` here instead would report today's rate for a
#: case evaluated under a release that had another one -- the provenance failure
#: `policy_version` exists to make visible. A policy with no `seller_schedule`
#: produces no rate, appends neither fact, and projects `None` for both.
_RESTOCKING_RATE_FACT: Final = "policy_restocking_fee_rate_basis_points"
_RESTOCKING_RATE_SOURCE_FACT: Final = "policy_restocking_fee_rate_source"

# ----------------------------------------------------------------------------
# Coercion. Every value out of Mongo passes through one of these.
# ----------------------------------------------------------------------------


def _text(value: object) -> str | None:
    """A non-blank string, or nothing.

    Blank reads as absent because `Reference` forbids it and, more to the point,
    because an empty `returnReference` would satisfy "an RMA exists".
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return str(value)
    return None


def _moment(value: object) -> datetime | None:
    """An aware UTC datetime, or nothing.

    Mongo hands back naive datetimes and the fact log holds ISO strings; both
    have to arrive at the contract's `AwareDatetime`. An unparseable value is
    absent rather than substituted -- a timestamp nobody can read is not a
    timestamp, and `datetime.now()` here would date an event to the read.
    """
    if isinstance(value, datetime):
        return normalize_utc_datetime(value)
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return normalize_utc_datetime(parsed)


def _count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _documents(value: object) -> tuple[Mapping[str, Any], ...]:
    """An embedded array of subdocuments, or nothing.

    Absent, null, a scalar and an array of scalars all read as no entries. A
    document shaped unlike anything a writer here produces is skipped rather
    than coerced: an entry the projection cannot read is an entry it knows
    nothing about, and a `{}` forced into a package would assert a parcel with
    no tracking number -- the one shape this module forbids everywhere else.
    """
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(entry for entry in value if isinstance(entry, Mapping))


def _scalar(value: object) -> str | int | float | bool | None:
    """A fact value the contract can carry.

    `CaseFactProjection.value` is deliberately scalar while the fact log is
    deliberately heterogeneous -- `confirmed_order_lines` holds a list, and
    `_append_observed` writes whatever a conversation produced. A non-scalar is
    rendered as deterministic JSON rather than dropped: dropping it would remove
    a fact the log holds from a projection that claims to serve the log, and
    that is the same class of silent hole this contract was written to close.
    """
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, datetime):
        return normalize_utc_datetime(value).isoformat()
    return json.dumps(value, sort_keys=True, default=str)


def _basis_points(value: object) -> int | None:
    """An in-range basis-point rate, or nothing.

    Bounded here as well as on the contract, and deliberately: a fact holding
    `150000` is a corrupt rate rather than a 1500% fee, and letting it reach the
    model would fail the whole case read on a value the projection can simply
    decline to believe. Accepts the string form too, because the fact log stores
    whatever the writer passed and `append_case_fact` does not coerce.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        rate = value
    else:
        text = _text(value)
        if text is None:
            return None
        try:
            rate = int(text)
        except ValueError:
            return None
    return rate if 0 <= rate <= 10_000 else None


def _members[EnumT: StrEnum](value: object, member: type[EnumT]) -> tuple[EnumT, ...] | None:
    """A comma-joined provenance list, read back as enum members.

    Unrecognised entries are skipped rather than raised on. These lists are
    provenance -- the reason codes and rules an evaluation cited -- and a code
    retired in a later release must not make every case that carries it
    unreadable. The route and the decision are handled the other way round,
    because the meaning of the block depends on them.
    """
    text = _text(value)
    if text is None:
        return None
    lookup = {item.value: item for item in member}
    parsed = [
        lookup[candidate]
        for candidate in (entry.strip() for entry in text.split(","))
        if candidate in lookup
    ]
    return tuple(parsed) or None


# ----------------------------------------------------------------------------
# The documents one case is assembled from.
# ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseAggregateDocuments:
    """Everything the repository read, before anything was made of it.

    A record of what was loaded, so the assembler is a function of its argument
    and a test can state a case as five literals. `facts` is the
    **latest-per-name** projection (`CaseRepository.latest_case_facts`), not the
    whole log: plan sect. 6.3 serves `facts[]` from exactly that projection,
    which is what deletes the console's client-side `latestFacts` duplicate.
    """

    case: Mapping[str, Any]
    facts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    return_records: Sequence[Mapping[str, Any]] = ()
    return_items: Sequence[Mapping[str, Any]] = ()
    #: The Support work item `channelBWorkItemId` points at, when the case has
    #: one and it was loaded. `None` covers both "no work item" and "not
    #: loaded", and the projection says nothing about Support either way.
    support_work_item: Mapping[str, Any] | None = None


def _fact(facts: Mapping[str, Mapping[str, Any]], name: str) -> object:
    document = facts.get(name)
    return None if document is None else document.get("value")


def _fact_text(facts: Mapping[str, Mapping[str, Any]], name: str) -> str | None:
    return _text(_fact(facts, name))


# ----------------------------------------------------------------------------
# Blocks.
# ----------------------------------------------------------------------------


def project_facts(
    facts: Mapping[str, Mapping[str, Any]],
) -> tuple[CaseFactProjection, ...] | None:
    """The latest value of every fact, with the provenance that decides trust.

    Sorted by name so two polls of an unchanged case serialize identically; a
    projection whose ordering wandered would read as a change on every read.
    """
    projected: list[CaseFactProjection] = []
    for name in sorted(facts):
        document = facts[name]
        fact_id = _text(document.get("factId"))
        fact_name = _text(document.get("factName")) or _text(name)
        if fact_id is None or fact_name is None:
            continue
        projected.append(
            CaseFactProjection(
                factId=fact_id,
                factName=fact_name,
                value=_scalar(document.get("value")),
                agentId=_text(document.get("agentId")),
                channel=_text(document.get("channel")),
                sourceSystem=_text(document.get("sourceSystem")),
                acquisitionMethod=_text(document.get("acquisitionMethod")),
                observedAt=_moment(document.get("observedAt")),
                recordedAt=_moment(document.get("recordedAt")),
                supersedesFactId=_text(document.get("supersedesFactId")),
            )
        )
    return tuple(projected) or None


def project_customer(
    case: Mapping[str, Any], facts: Mapping[str, Mapping[str, Any]]
) -> CustomerProjection | None:
    """Who the return is for, from the case's branch and the discovery facts.

    `None` unless at least one field has a value. A `customer` block whose every
    field is null says the platform resolved a customer it knows nothing about.
    """
    customer_reference = _fact_text(facts, _CUSTOMER_REFERENCE_FACT)
    display_name = _fact_text(facts, _CUSTOMER_NAME_FACT)
    branch_reference = _text(case.get("branchId"))
    if customer_reference is None and display_name is None and branch_reference is None:
        return None
    return CustomerProjection(
        customerReference=customer_reference,
        # No account resolution reaches the case aggregate; `None` says so.
        accountReference=None,
        displayName=display_name,
        branchReference=branch_reference,
    )


def project_confirmed_order(
    case: Mapping[str, Any], facts: Mapping[str, Mapping[str, Any]]
) -> ConfirmedOrderProjection | None:
    """The order the associate confirmed. Absent until they did.

    Keyed on `confirmedOrderReference`, which `create_case` only carries for a
    confirmation. Its absence is what distinguishes discovery from confirmation,
    so a candidate still being looked at can never appear here.
    """
    order_reference = _text(case.get("confirmedOrderReference"))
    if order_reference is None:
        return None
    confirmation = facts.get(_CONFIRMED_ORDER_FACT)
    confirmed_at = None
    if confirmation is not None:
        confirmed_at = _moment(confirmation.get("observedAt")) or _moment(
            confirmation.get("recordedAt")
        )
    return ConfirmedOrderProjection(
        orderReference=order_reference,
        confirmationKey=_text(case.get("confirmationKey")),
        confirmedAt=confirmed_at,
    )


def project_selected_items(
    return_items: Sequence[Mapping[str, Any]],
) -> tuple[SelectedItemProjection, ...] | None:
    """The lines the associate named that no RMA covers yet.

    Assigned items are not repeated here; they are `approvedItems` on their
    record. `api/cases.py` already keeps the two apart for the same reason --
    folding an unassigned line into the first record would put it somewhere
    plausible, and the pane would render the guess as fact.
    """
    projected: list[SelectedItemProjection] = []
    for item in return_items:
        if _text(item.get("returnRecordId")) is not None:
            continue
        item_id = _text(item.get("returnItemId"))
        line = _text(item.get("orderLineId")) or _text(item.get("orderLineReference"))
        if item_id is None or line is None:
            continue
        projected.append(
            SelectedItemProjection(
                returnItemId=item_id,
                orderLineReference=line,
                productReference=_text(item.get("productReference")),
                quantity=_count(item.get("quantity")),
                reason=_text(item.get("reason")),
                condition=_text(item.get("condition")),
                packageReference=_text(item.get("packageReference")),
            )
        )
    return tuple(projected) or None


def project_return_artifacts(
    record: Mapping[str, Any],
) -> tuple[ReturnArtifactProjection, ...] | None:
    """`labelReference` -> one active shipping label, or nothing.

    The label reference *is* the artifact's identity -- persistence holds no
    other -- so it is the `artifactId` rather than a minted one. `active=True`
    and `supersededBy=None` because the legacy field holds exactly one label and
    a superseded one was overwritten rather than recorded; there is no version
    history to project, and claiming one would be worse than admitting none.

    The result belongs on `ReturnRecordProjection.artifacts`, which is the one
    home for it. `shipmentId` is the package this belongs to and is `None`
    whenever no single package can carry it -- a label with no parcel beside it,
    and equally a label on an RMA with two -- so the label is projected,
    attributed to nothing, rather than dropped or given a shipment it was not
    printed for. `_shipment_id` states why. `createdAt` is `None` because the
    record's `updatedAt` is when the *record* last changed, which is not when
    the label was issued.
    """
    label = _text(record.get("labelReference"))
    if label is None:
        return None
    return (
        ReturnArtifactProjection(
            artifactId=label,
            artifactType=ReturnArtifactType.SHIPPING_LABEL,
            shipmentId=_shipment_id(record),
            active=True,
            supersededBy=None,
        ),
    )


def _shipment_id(record: Mapping[str, Any]) -> str | None:
    """The package a document on this record can honestly be attributed to.

    The sole package's id when the RMA has exactly one, and `None` otherwise --
    including when it has two.

    `dbo.return_record` holds one `label_reference` and the projection holds one
    active `SHIPPING_LABEL`, so an RMA that turns out to have two parcels offers
    no way to say which parcel that label papers. Stamping it on the first is
    the `labels[0]` reading the audit found; stamping it on both would claim a
    document that was never printed twice. `None` is the third answer and the
    only true one: the label is on the RMA, and no package is named. The
    consequence is that `LABEL` stays outstanding for such a record, which is
    what `_every_package_papered` already says it should be -- one label printed
    for two parcels leaves the second parcel with nothing on it.
    """
    shipments = project_shipments(record)
    if shipments is None or len(shipments) != 1:
        return None
    return shipments[0].shipmentId


def project_shipments(record: Mapping[str, Any]) -> tuple[ShipmentProjection, ...] | None:
    """Every parcel this RMA has, from both places one can be stated.

    `None` when there are none. Never a tuple holding a shipment with a null
    tracking number -- that is the shape that let a screen show a parcel nobody
    had tendered.

    **Two producers, unioned on the tracking reference.**

    ```text
    trackingReference   what Support stated when the RMA was issued. One slot,
                        because `dbo.return_record` has one column, and it is a
                        statement made before any carrier has filed anything.
    shipments[]         one entry per parcel, written by
                        `CaseRepository.record_case_shipment` from an APPLIED
                        `dbo.return_tracking` update -- the authoritative,
                        already-RMA-scoped, already-plural return shipment store
                        (`006_return_shipment_state.sql`).
    ```

    Neither subsumes the other, so neither wins. Support can name a tracking
    number no carrier has scanned, and a carrier can scan a parcel Support never
    named -- which is the whole of D1: the carrier event reached
    `dbo.return_tracking`, resolved its case and appended its facts, and the
    read model had nowhere to put the parcel, so `returnRecords[].shipments`
    stayed empty and `awaiting` stayed `['LABEL', 'TRACKING']` for a return that
    was demonstrably moving. An entry and the legacy column naming the same
    tracking reference are **one** parcel, and the entry is the more specific
    statement about it, so it supplies the timestamps and the observed carrier.

    **`shipmentId` is the tracking reference**, and it is not minted. The
    tracking number *is* the parcel's identity -- `UQ_return_tracking_reference`
    makes it one row per number -- exactly as the label reference is the
    artifact's identity in `project_return_artifacts`, and persistence offers no
    other handle on a parcel. It used to be the record id, which was serviceable
    only while an RMA could hold one parcel: with two, the record id names
    neither, and any scheme that gave the *first* parcel the record id would
    change that parcel's identity the moment a second one arrived.

    **Five fields have a producer. Three do not, and are `None`.**

    ```text
    shipmentId       the parcel's tracking reference
    trackingNumber   the same value, as the number rather than as the handle
    carrier          the carrier code the feed filed for this parcel, else
                     `ReturnRecordView.carrier` -- Support's answer for this
                     RMA, carried by `RETURN_RECORD_MERGED_FIELDS`'s
                     `("carrier", ...)` row from `ReturnOutcomeRecord.carrier`
                     through `SupportReturnRecord.carrier`. Never the
                     session-scoped `SupportActionRequest.carrier`, which
                     belongs to a different return: reading it onto a case would
                     attribute one return's carrier to another return's package,
                     which is the shape of the defect that put
                     `session.orderSource` behind "Carrier & Service".
    createdAt        when this parcel was first recorded; the record's own
                     `createdAt` for the parcel Support stated.
    updatedAt        when this parcel's recorded identity last changed; the
                     record's own `updatedAt` for the parcel Support stated.

    serviceLevel     None -- no case-keyed producer. The only service level in
                     the platform is `PickupRequest.serviceLevel`
                     (`operations/physical/service.py`), which is the service a
                     freight *collection* was booked at, keyed by pickup request
                     and session. It is not the service a parcel is moving
                     under, and `pickup_requests` is the same session-keyed
                     collection that leaves `pickup` absent below.
    estimatedDeliveryAt
                     None -- no producer anywhere. Nothing in this platform
                     computes or receives a return-leg delivery estimate.
                     `workflows/fulfillment_tracking.py` reads the graph's
                     `shipmentInfo`, which describes the *outbound* order and
                     which that module records as carrying no carrier at this
                     grain either; an outbound estimate is not this package's,
                     and `shippingPathExpectation` -- the return-method enum the
                     audit found rendered as "Est. Delivery" -- is not a date.
    shipmentStatus   None -- still no producer this may read. A carrier update
                     does carry a status, and it is deliberately not mapped
                     here: `ShipmentUpdateRequest.shipmentStatus` is documented
                     as un-enumerable because "the carrier's status vocabulary
                     is the carrier's", while `ShipmentStatus` is a closed five,
                     so any mapping would either drop the statuses it did not
                     recognise or guess at them. The platform's own reading of
                     that status already reaches the case honestly, as the
                     `fulfillment_status` fact `ReturnShipmentStateService`
                     writes beside the parcel.
    ```

    The three are left to their declared `None` rather than written out as
    arguments, so that adding a producer is an edit here and not a search for
    which of seven arguments was the honest one.

    No artifacts hang off this. They are the record's, and
    `project_return_artifacts` stamps a `shipmentId` on them when exactly one
    package can carry them, which is what attributes a label to a package
    without giving the label two places to live.
    """
    record_carrier = _text(record.get("carrier"))
    parcels: dict[str, ShipmentProjection] = {}

    stated = _text(record.get("trackingReference"))
    if stated is not None:
        parcels[stated] = ShipmentProjection(
            shipmentId=stated,
            trackingNumber=stated,
            carrier=record_carrier,
            createdAt=_moment(record.get("createdAt")),
            updatedAt=_moment(record.get("updatedAt")),
        )

    for entry in _documents(record.get(RETURN_RECORD_SHIPMENTS_FIELD)):
        tracking = _text(entry.get("trackingReference"))
        if tracking is None:
            # An entry with no tracking reference is an entry naming no parcel.
            # There is nothing to project it as, and a package with a null
            # tracking number is the one shipment shape this module forbids.
            continue
        parcels[tracking] = ShipmentProjection(
            shipmentId=tracking,
            trackingNumber=tracking,
            # The carrier that filed the scan, when one did. Otherwise Support's
            # answer, which is a statement about this RMA and therefore about
            # every parcel under it -- unlike a case-level carrier, which would
            # be a statement about a different RMA's parcels too.
            carrier=_text(entry.get("carrier")) or record_carrier,
            createdAt=_moment(entry.get("createdAt")),
            updatedAt=_moment(entry.get("updatedAt")),
        )

    return tuple(parcels.values()) or None


def _approved_items(
    record_id: str, return_items: Sequence[Mapping[str, Any]]
) -> tuple[ApprovedItemProjection, ...] | None:
    projected: list[ApprovedItemProjection] = []
    for item in return_items:
        if _text(item.get("returnRecordId")) != record_id:
            continue
        item_id = _text(item.get("returnItemId"))
        if item_id is None:
            continue
        projected.append(
            ApprovedItemProjection(
                returnItemId=item_id,
                orderLineReference=_text(item.get("orderLineId"))
                or _text(item.get("orderLineReference")),
                productReference=_text(item.get("productReference")),
                quantityApproved=_count(item.get("quantity")),
            )
        )
    return tuple(projected) or None


def project_return_record(
    record: Mapping[str, Any],
    return_items: Sequence[Mapping[str, Any]] = (),
    *,
    fallback_method: str | None = None,
) -> ReturnRecordProjection | None:
    """One RMA, its approved lines, its packages and its documents.

    `fallback_method` is the case-level return-method fact, used only when the
    record itself carries no method. Record first: a case-level value applied
    over a record that disagreed would be the cross-attribution the multi-RMA
    shape exists to prevent.
    """
    record_id = _text(record.get("returnRecordId"))
    if record_id is None:
        return None
    return ReturnRecordProjection(
        returnRecordId=record_id,
        returnReference=_text(record.get("returnReference")),
        status=_text(record.get("status")),
        returnMethod=_text(record.get("returnMethod")) or fallback_method,
        returnLocation=_text(record.get("returnLocation")),
        approvedItems=_approved_items(record_id, return_items),
        shipments=project_shipments(record),
        artifacts=project_return_artifacts(record),
    )


def project_return_records(
    return_records: Sequence[Mapping[str, Any]],
    return_items: Sequence[Mapping[str, Any]] = (),
    *,
    fallback_method: str | None = None,
) -> tuple[ReturnRecordProjection, ...] | None:
    projected = [
        record
        for record in (
            project_return_record(document, return_items, fallback_method=fallback_method)
            for document in return_records
        )
        if record is not None
    ]
    return tuple(projected) or None


def project_support(work_item: Mapping[str, Any] | None) -> SupportProjection | None:
    """The open work item, if one was loaded.

    Distinguished by `queue`, never by a type field -- `SupportWorkItemView` has
    no type, and route context travels as the configured queue.
    """
    if work_item is None:
        return None
    work_item_id = _text(work_item.get("_id")) or _text(work_item.get("id"))
    if work_item_id is None:
        return None
    return SupportProjection(
        workItemId=work_item_id,
        threadId=_text(work_item.get("threadId")),
        queue=_text(work_item.get("queue")),
        status=_text(work_item.get("status")),
        subject=_text(work_item.get("subject")),
        priority=_text(work_item.get("priority")),
        assignedTo=_text(work_item.get("assignedTo")),
        slaDueAt=_moment(work_item.get("slaDueAt")),
        openedAt=_moment(work_item.get("createdAt")),
        resolvedAt=_moment(work_item.get("completedAt")),
    )


def project_support_outcome(
    facts: Mapping[str, Mapping[str, Any]],
) -> SupportOutcome | None:
    """What Support answered, or `None` because it has not answered.

    `None` covers three situations that are one thing to a reader: Support has
    not replied, the case never went to Support at all, and the reply predates
    the writer. None of them is a refusal, and treating them as one would end
    working cases.

    **An unreadable value raises rather than reading as `None`**, which is the
    same stance `project_policy_evaluation` takes on `policy_route` and for a
    sharper version of the same reason. `None` here does not merely lose
    detail: `project_case_status` reads it, so a `CLOSED` case whose outcome
    fact held a value this enum does not recognise would be projected as
    *completed with an external settlement* -- the platform announcing a credit
    for a return somebody refused. Failing the read says the projection cannot
    answer; defaulting it would have the projection answer wrongly, on every
    poll, in the direction that costs money.

    The instant and the writer are not returned. They are already on the fact
    itself and reach a reader through `facts[]` as `recordedAt`, `agentId`,
    `channel` and `sourceSystem`, and a second copy of a timestamp is a second
    thing to disagree with the first.
    """
    value = _fact_text(facts, SUPPORT_OUTCOME_FACT)
    if value is None:
        return None
    try:
        return SupportOutcome(value)
    except ValueError as error:
        raise ValueError(
            f"case fact {SUPPORT_OUTCOME_FACT} holds {value!r}, which is not a SupportOutcome: "
            "a closed case whose Support answer cannot be read must not be reported as "
            "completed, because a refusal read as a completion asserts a settlement"
        ) from error


def project_warehouse(
    facts: Mapping[str, Mapping[str, Any]],
) -> WarehouseProjection | None:
    """Bay placement, from the facts `ReturnCaseWorkflow` already writes.

    **Three fields have a producer. Seven do not, and are `None`.**

    ```text
    facilityId       bay_warehouse_reference fact  (ReturnCaseWorkflowActivities
                                                    ._record_bay_facts)
    bayId            bay_reference fact            (same writer)
    bayReason        bay_reason fact               (same writer)

    facilityName     None -- no producer. The bay facts carry a warehouse
                     *reference*; nothing on the case path resolves it to a
                     display name, and `WarehouseObservation` returns the id it
                     was asked about.
    receivedAt       None -- no producer.
    receivedQuantity None -- no producer.
    inspectionStatus None -- no producer.
    condition        None -- no producer. `case_return_items.condition` is the
                     condition the associate stated at selection, not a
                     condition anybody inspected on arrival, and reading it here
                     would relabel a customer's claim as a warehouse finding.
    disposition      None -- no producer. `case_return_items` has no disposition
                     column and no writer sets one. (`return_support.py`'s
                     `disposition="RECORDED" | "DUPLICATE"` is an idempotency
                     outcome for a support event, not what happens to goods.)
    qaStatus         None -- no producer.
    warehouseStatus  None -- no case-keyed producer. `ReturnSessionView.
                     warehouseStatus` exists and moves to `STAGED`, but it is
                     written by `WarehousePlacementService.assign` through
                     `update_return(session_id, ...)` against the session
                     document, and handling units are keyed
                     `f"{session_id}:HU:{sequence}"`. A Copilot case has no
                     session, so there is nothing to read; joining a session's
                     receipt to a case would be a guess about which goods.
    ```

    Every one of those seven is a real receiving concept with no writer on the
    case path. They are reported absent rather than modelled into existence,
    because the audit's `Bay 14-B` and `Tier 2 Technical Inspection` are what
    filling them from something plausible looks like.

    **Consequently the block is placement, never receipt.** `has_receipt` reads
    only the four fields a receiving event could write, all of which are `None`
    here, so a bay recommendation does not advance the stage to
    `WAREHOUSE_RECEIVING` -- which is right, because a recommended bay is not
    goods booked in. `CaseBayPlacement` runs pre-arrival by design
    (`PRE_ARRIVAL_STATUS = "AWAITING_RECEIPT"`), so the recommendation typically
    predates the goods by days.

    **`bayReason` alone is enough for the block to exist**, and that is the
    point of projecting it. Placement is advisory and best-effort: when it is
    unconfigured, refused before arrival, or looking at a warehouse the graph
    does not hold, it writes `bay_reason` and nothing else. Returning `None`
    there would throw away the only statement the platform has about why this
    case has no bay, and leave a reader unable to tell "not attempted" from
    "attempted and found nothing". A `bayReason` with no `bayId` is an
    explanation, **not an error** -- a case with no bay is the normal state of
    most cases for most of their lives.
    """
    facility = _fact_text(facts, _BAY_FACILITY_FACT)
    bay = _fact_text(facts, _BAY_REFERENCE_FACT)
    reason = _fact_text(facts, _BAY_REASON_FACT)
    if facility is None and bay is None and reason is None:
        return None
    return WarehouseProjection(
        facilityId=facility,
        # No producer resolves a warehouse reference to a name on the case path.
        facilityName=None,
        bayId=bay,
        bayReason=reason,
        # The seven below are left to their declared `None` rather than written
        # out, so that adding a producer is an edit here and not a search for
        # which of ten arguments was the honest one.
    )


def _override(
    facts: Mapping[str, Mapping[str, Any]],
) -> PolicyOverrideProjection | None:
    """A supervisor's departure from the evaluator's answer, if the log holds a whole one.

    All four of decision, reason code, actor and timestamp or none of them. A
    partial override cannot be projected -- `actor` and `overriddenAt` are what
    make it audit rather than an assertion -- and half an override read as a
    whole one would approve a case in the name of nobody.
    """
    decision = _fact_text(facts, "policy_override_decision")
    reason_code = _fact_text(facts, "policy_override_reason_code")
    actor = _fact_text(facts, "policy_override_actor")
    overridden_at = _moment(_fact(facts, "policy_override_at"))
    if decision is None or reason_code is None or actor is None or overridden_at is None:
        return None
    try:
        parsed = EligibilityDecision(decision)
    except ValueError:
        return None
    return PolicyOverrideProjection(
        overrideDecision=parsed,
        reasonCode=reason_code,
        reason=_fact_text(facts, "policy_override_reason"),
        actor=actor,
        overriddenAt=overridden_at,
    )


def _restocking_rate(
    facts: Mapping[str, Mapping[str, Any]],
) -> tuple[int | None, FeeAmountSource | None]:
    """The restocking rate and the authority that set it, or neither.

    **Both or neither, never one.** A rate with no named source is exactly the
    shape `FeeDetermination` refuses and `PolicyEvaluationProjection` refuses
    after it: a percentage nobody can attribute is indistinguishable from an
    invented one, and `SELLER_CONFIGURATION` is the whole of what stops a
    seller's rate reading as published Ferguson policy on the screen.

    A half-written pair is dropped rather than raised on. The rate is one
    annotation on an evaluation, and failing the entire case read over it would
    take the decision, the reason codes and the whole return down with it --
    which is the trade `_override` already makes for the same reason. The route
    and the decision are the two things read the other way round, because the
    meaning of the block depends on them.

    The producer is `_record_policy_outcome`, which appends both facts from the
    `FeeDetermination` the evaluation returned. See `_RESTOCKING_RATE_FACT` for
    why the value is read from the log and never from the live release.
    """
    rate = _basis_points(_fact(facts, _RESTOCKING_RATE_FACT))
    if rate is None:
        return None, None
    source_value = _fact_text(facts, _RESTOCKING_RATE_SOURCE_FACT)
    if source_value is None:
        return None, None
    try:
        source = FeeAmountSource(source_value)
    except ValueError:
        return None, None
    return rate, source


def project_policy_evaluation(
    facts: Mapping[str, Mapping[str, Any]],
) -> PolicyEvaluationProjection | None:
    """What the evaluator decided, from the facts it recorded (plan sect. 3A.8).

    Keyed on `policy_route`: no route, no evaluation. An unreadable route raises
    rather than reading as "not evaluated", because the whole block's meaning
    hangs off it and a case silently reported as unevaluated waits on a policy
    decision it already has.

    `effectiveDecision` is **derived here, never read from
    `policy_effective_decision`.** The contract requires it to equal the
    override's decision when one stands and the original otherwise, and deriving
    it is what makes that true by construction: a log holding an effective
    decision whose override cannot be projected would otherwise be rejected
    outright, failing the whole case read over a half-written audit trail.

    A non-standard route carries no decision and no override. Support verifies
    warranty and delivery claims, and an override on one would approve the claim
    the verification exists to test -- so anything the log holds for those is
    dropped rather than projected, which is also the only reading the contract's
    own validator accepts.

    **The restocking rate is projected, and no currency figure is.** The rate is
    read from the two facts `_restocking_rate` names, in basis points and with
    its authority beside it; nothing here multiplies it by anything, because the
    price it would be multiplied by is on the order line and no order line
    reaches this function. The rate comes from the fact the evaluation recorded
    and never from the live release: reading the *current* release's
    `seller_schedule` here would report today's rate for a case evaluated under
    a release that had another one, which is the provenance failure
    `policy_version` exists to make visible. A release with no `seller_schedule`
    leaves both facts unwritten and both fields `None`, which is the honest
    absence and not a value to fill in from configuration at read time.
    """
    route_value = _fact_text(facts, "policy_route")
    if route_value is None:
        return None
    try:
        route = PolicyRoute(route_value)
    except ValueError as error:
        raise ValueError(
            f"case fact policy_route holds {route_value!r}, which is not a PolicyRoute: "
            "the policy block cannot be read and a case must not be reported unevaluated "
            "because of it"
        ) from error

    original: EligibilityDecision | None = None
    override: PolicyOverrideProjection | None = None
    if route is PolicyRoute.STANDARD_RETURN:
        decision = _fact_text(facts, "policy_decision")
        if decision is None:
            # A standard return with no decision cannot satisfy the contract's
            # own invariant, and inventing `REVIEW_REQUIRED` would put a
            # supervisor in front of a case nothing asked them to look at.
            return None
        try:
            original = EligibilityDecision(decision)
        except ValueError as error:
            raise ValueError(
                f"case fact policy_decision holds {decision!r}, which is not an EligibilityDecision"
            ) from error
        override = _override(facts)

    effective = override.overrideDecision if override is not None else original
    rate_basis_points, rate_source = _restocking_rate(facts)
    return PolicyEvaluationProjection(
        route=route,
        originalDecision=original,
        effectiveDecision=effective,
        override=override,
        reasonCodes=_members(_fact(facts, "policy_reason_codes"), PolicyReasonCode),
        conditions=_members(_fact(facts, "policy_conditions"), PolicyCondition),
        appliedRules=_members(_fact(facts, "policy_applied_rules"), PolicyRule),
        policyId=_fact_text(facts, "policy_id"),
        policyVersion=_fact_text(facts, "policy_version"),
        evaluatedAt=_moment(_fact(facts, "policy_evaluated_at")),
        rateBasisPoints=rate_basis_points,
        rateSource=rate_source,
    )


# ----------------------------------------------------------------------------
# The whole case.
# ----------------------------------------------------------------------------


def _return_method_fact(facts: Mapping[str, Mapping[str, Any]]) -> str | None:
    for name in RETURN_METHOD_FACT_NAMES:
        method = _fact_text(facts, name)
        if method is not None:
            return method
    return None


def assemble_case_projection_state(documents: CaseAggregateDocuments) -> CaseProjectionState:
    """One case, exactly as persistence has it. No derivation, no defaults.

    The four derived values are `project_case`'s job and are deliberately not
    computed here: a state that could see its own stage would let a future rule
    read one, and plan sect. 6.6 puts stage logic in exactly one function.

    `revision` reads a missing `version` as `0` -- the same deterministic
    initialisation `plan_case_backfill` writes, so a case the backfill has not
    reached and one it has report the same number rather than jumping when the
    migration runs.
    """
    case = documents.case
    facts = documents.facts

    case_id = _text(case.get("caseId"))
    tenant_id = _text(case.get("tenantId"))
    principal_id = _text(case.get("principalId"))
    if case_id is None or tenant_id is None or principal_id is None:
        identity = {key: case.get(key) for key in ("caseId", "tenantId", "principalId")}
        raise ValueError(
            f"a case document without caseId, tenantId and principalId is not a case: {identity}"
        )

    updated_at = _moment(case.get("updatedAt")) or _moment(case.get("createdAt"))
    if updated_at is None:
        raise ValueError(
            f"case {case_id} carries no readable updatedAt or createdAt; the projection "
            "will not stamp one, because a timestamp taken at read time dates every event "
            "to the moment somebody looked"
        )

    # No settlement producer exists anywhere in the platform, and the contract
    # says so out loud rather than by omission. `NOT_INTEGRATED` is a positive
    # statement -- "this platform does not settle returns" -- where `None` would
    # say "not computed" and leave a reader waiting for a credit memo that is
    # never coming. It is never `NOT_STARTED`: that would assert a producer that
    # has not run yet.
    #
    # This is the whole of what makes `COMPLETED_EXTERNAL_SETTLEMENT` deliberate
    # rather than incidental. `project_case_status` reads it below and sends
    # every `CLOSED` case there, so a completed-return count is never misread as
    # a settled-return count. Settlement enters nothing else: it is not an
    # `AwaitingDimension`, `resolve_completion` never reads it, and
    # `derive_copilot_stage` treats `NOT_INTEGRATED` as no settlement at all.
    settlement = SettlementProjection(status=SettlementStatus.NOT_INTEGRATED)
    # Still absent, and for the ordinary reason: `pickup_requests` is keyed by
    # session and nothing case-keyed reaches this assembler. Unlike settlement
    # there is a producer -- it is simply attached to the wrong aggregate -- so
    # "not computed for this case" is the true statement.
    pickup: PickupProjection | None = None

    revision = _count(case.get("version"))

    return CaseProjectionState(
        caseId=case_id,
        tenantId=tenant_id,
        principalId=principal_id,
        conversationId=_text(case.get("channelAConversationId")),
        status=project_case_status(
            case.get("status"),
            settlement=settlement,
            # How the case closed, for the one persisted status that two
            # opposite endings share. Without it a refused claim and a
            # fulfilled return are the same `CLOSED` document.
            support_outcome=project_support_outcome(facts),
        ),
        revision=0 if revision is None else revision,
        updatedAt=updated_at,
        customer=project_customer(case, facts),
        confirmedOrder=project_confirmed_order(case, facts),
        selectedItems=project_selected_items(documents.return_items),
        facts=project_facts(facts),
        policyEvaluation=project_policy_evaluation(facts),
        support=project_support(documents.support_work_item),
        returnRecords=project_return_records(
            documents.return_records,
            documents.return_items,
            fallback_method=_return_method_fact(facts),
        ),
        pickup=pickup,
        warehouse=project_warehouse(facts),
        settlement=settlement,
    )
