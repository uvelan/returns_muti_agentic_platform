"""The HTTP entry point for an RMA-scoped return shipment update (SHIP-01, C4).

Everything below this route already existed and was proven against real SQL
Server, Mongo and Neo4j. `ReturnShipmentStateService.record_update` runs the
whole chain --

    SQL write -> APPLIED-only targeted graph sync under an RMA-scoped
    generation lease -> fulfilment read -> two case facts -> the associate's
    next turn

-- and nothing here reimplements any of it. What was missing was a way in: a
carrier event could reach the platform only through a Python caller, and there
was none in production, so contract C4's chain ran for tests and for nobody
else.

**The semantics are the service's, not this module's.** RMA scope, idempotency,
and the `APPLIED | DUPLICATE | STALE` verdict are settled inside the UPDATE's
WHERE clause under `UPDLOCK, HOLDLOCK` -- not by a read-then-write this route
could lose a race on, and not by anything a second concurrent request could
reorder. This route's only jobs are to name the RMA, validate the payload
against what the store will accept, hand the update over, and report the
verdict unchanged.

**Both graph ports are supplied, and their absence is not silent.** Without the
sync port an APPLIED update never reaches the graph; without the observation
port the fulfilment reading records `SHIPMENT_UNAVAILABLE:NOT_ATTEMPTED`, which
is honest but is not the closure. Both are built from one `TargetedGraphAccess`
so the write reservation and the read lease are counted against one generation
document, and both are cached on app state -- including the failure to build
them, for the reason `api/warehouse_placement.py` gives: retrying a Neo4j
connection on every carrier event turns a best-effort reading into a per-request
timeout.

**A graph outage does not fail the update, and a graph *sync* failure does not
hide it.** The reading is best-effort by declared policy and degrades to
`UNAVAILABLE`. The sync is not: `record_shipment_update` raises when it cannot
project an accepted update, because a shipment the platform has accepted and the
graph has never heard of reads as `AWAITING_HANDOFF` to every agent. That
becomes a 502 naming the authoritative row as already committed -- resubmitting
the identical update is safe and answers DUPLICATE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from return_platform.dynamic_knowledge.integration.shipment_observations import (
    GraphShipmentObservations,
)
from return_platform.dynamic_knowledge.integration.shipment_state_sync import (
    GraphShipmentStateSync,
    ShipmentStateSyncFailed,
)
from return_platform.dynamic_knowledge.integration.targeted_sync import (
    build_targeted_graph_access,
)
from return_platform.operations.repository import resolve_operational_repository
from return_platform.operations.return_shipment_state import (
    CaseShipmentReading,
    ReturnShipmentStateService,
)
from return_platform.operations.sql_business_state import (
    TRACKING_TYPES,
    ShipmentGraphSyncPort,
    ShipmentUpdate,
    ShipmentUpdateOutcome,
    SQLBusinessStateRepository,
)
from return_platform.resources import RuntimeResources
from return_platform.security import capabilities
from return_platform.security.authorization import require_capability
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.fulfillment_tracking import ShipmentObservationPort

router = APIRouter(prefix="/api/return-shipments", tags=["Return Shipments"])

logger = logging.getLogger("return_platform.api.return_shipments")

#: Where the built graph ports are cached on app state. Building them opens a
#: release store, a lease store and a sync store, so they are assembled once per
#: process rather than per carrier event -- and a failed build is remembered too.
_GRAPH_PORTS_ATTRIBUTE = "return_shipment_graph_ports"


@dataclass(frozen=True, slots=True)
class _GraphPorts:
    """The write half and the read half of one `TargetedGraphAccess`.

    Held together because they must come from the same access: the sync's write
    reservation and the reading's read lease are only counted against one
    generation document and one drain if they share the lease store.
    """

    sync: ShipmentGraphSyncPort | None
    observations: ShipmentObservationPort | None


class ReturnShipmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShipmentUpdateRequest(ReturnShipmentModel):
    """One carrier observation of one return parcel.

    Every length below is `dbo.return_tracking`'s own column width, so a payload
    this model accepts is a payload the store can hold. Refusing an over-long
    tracking number here is a 422 naming the field; letting it through is a
    truncation or a driver error a caller cannot act on.
    """

    trackingReference: str = Field(min_length=1, max_length=128)
    #: `VARCHAR(32)`. Not enumerated: the carrier's status vocabulary is the
    #: carrier's, and a platform-side allowlist would silently reject a status a
    #: carrier legitimately started emitting.
    shipmentStatus: str = Field(min_length=1, max_length=32)
    #: The carrier's status timestamp, and the ordering authority for the whole
    #: contract -- APPLIED vs STALE is decided against this and nothing else.
    statusAt: datetime
    #: `CK_return_tracking_type`'s vocabulary, and required rather than
    #: defaulted. A shipment's ship-via is a property of that shipment; defaulting
    #: it would file a BOL freight movement as a parcel, and nothing downstream
    #: could tell that had happened.
    trackingType: str
    carrierCode: str | None = Field(default=None, max_length=32)
    shipmentDetails: str | None = Field(default=None, max_length=1_000)

    @field_validator("statusAt")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        """Reject an unzoned status timestamp rather than assuming one.

        This field decides whether an update advances the stored truth or is
        rejected as stale. Reading a naive timestamp as UTC would make a
        submitter in a different zone silently overtake -- or silently lose to --
        an event it has no relationship to.
        """
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("statusAt must carry a timezone offset")
        return value

    @field_validator("trackingType")
    @classmethod
    def known_tracking_type(cls, value: str) -> str:
        if value not in TRACKING_TYPES:
            raise ValueError(f"trackingType must be one of {sorted(TRACKING_TYPES)}")
        return value


class ShipmentReadingView(ReturnShipmentModel):
    """What the graph said about the parcel, and who was told."""

    #: `None` when no case owns the RMA. A real state, not an error --
    #: `dbo.return_tracking` is keyed on the RMA and needs no `return_record` row
    #: -- and reporting it lets a caller tell "nobody was told" from "the reading
    #: failed".
    caseId: str | None
    fulfillmentStatus: str
    evidence: str
    #: The same evidence string the case fact carries, so what the associate is
    #: shown and what the submitter is told cannot disagree.
    evidenceReference: str
    graphGenerationId: str | None
    observedStatus: str | None


class ShipmentUpdateResult(ReturnShipmentModel):
    #: `APPLIED`, `DUPLICATE` or `STALE`, exactly as the store decided it.
    outcome: str
    returnReference: str
    trackingReference: str
    currentStatus: str
    currentStatusAt: datetime
    rowVersion: int
    graphGenerationId: str | None
    #: Present only for an APPLIED update. A duplicate submission of a status the
    #: associate has already been shown appends no second fact, and a rejected
    #: stale update appends none at all, so neither produces a reading.
    reading: ShipmentReadingView | None


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


async def _graph_ports(request: Request, resources: RuntimeResources) -> _GraphPorts:
    """The graph write and read ports for this process, built once.

    A process that cannot reach the graph still records shipment updates: the
    authoritative row is the point, and refusing the write over a reporting
    concern would take the store down for an outage it does not depend on. The
    cost is visible rather than silent -- the reading comes back
    `SHIPMENT_UNAVAILABLE:NOT_ATTEMPTED`, which is a distinct evidence line from
    both "the parcel is not in the graph" and "the lookup failed".
    """
    cached = getattr(request.app.state, _GRAPH_PORTS_ATTRIBUTE, None)
    if isinstance(cached, _GraphPorts):
        return cached
    ports = _GraphPorts(sync=None, observations=None)
    if resources.mongo is not None and resources.neo4j is not None:
        try:
            access = await build_targeted_graph_access(
                settings=resources.settings,
                platform_mongo=resources.mongo,
                source_mongo=resources.source_mongo or resources.mongo,
                neo4j_driver=resources.neo4j,
                owner_role="return-shipment-state",
            )
            ports = _GraphPorts(
                sync=GraphShipmentStateSync.from_access(access),
                observations=GraphShipmentObservations.from_access(access),
            )
        except Exception:  # noqa: BLE001 - see docstring; the authoritative write stands
            logger.exception("return_shipment_graph_ports_unavailable")
    setattr(request.app.state, _GRAPH_PORTS_ATTRIBUTE, ports)
    return ports


async def _service(request: Request) -> ReturnShipmentStateService:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SHIPMENT_DEPENDENCIES_UNAVAILABLE",
                "message": "Return shipment dependencies are unavailable.",
            },
        )
    ports = await _graph_ports(request, resources)
    return ReturnShipmentStateService(
        business_state=SQLBusinessStateRepository(
            resources.settings,
            shipment_graph_sync=ports.sync,
        ),
        # Raises 503 of its own when the platform store is down. Resolved through
        # the shared helper rather than constructed here so this route's case
        # write is the same write every other case surface makes.
        repository=resolve_operational_repository(request),
        observations=ports.observations,
    )


def _reading_view(reading: CaseShipmentReading | None) -> ShipmentReadingView | None:
    if reading is None:
        return None
    return ShipmentReadingView(
        caseId=reading.case_id,
        fulfillmentStatus=reading.status.value,
        evidence=reading.evidence.value,
        evidenceReference=reading.evidence_reference,
        graphGenerationId=reading.graph_generation_id,
        observedStatus=reading.current_status,
    )


def _result(
    outcome: ShipmentUpdateOutcome, reading: CaseShipmentReading | None
) -> ShipmentUpdateResult:
    return ShipmentUpdateResult(
        outcome=outcome.outcome,
        returnReference=outcome.return_reference,
        trackingReference=outcome.tracking_reference,
        currentStatus=outcome.current_status,
        currentStatusAt=outcome.current_status_at,
        rowVersion=outcome.row_version,
        graphGenerationId=outcome.graph_generation_id,
        reading=_reading_view(reading),
    )


@router.post(
    "/{return_reference}/updates",
    response_model=APIResponse[ShipmentUpdateResult],
)
async def record_shipment_update(
    payload: ShipmentUpdateRequest,
    request: Request,
    return_reference: str = Path(min_length=1, max_length=64),
    # A carrier event is a logistics act, and `returns.logistics.act` is the
    # capability that already means exactly that -- the same grant behind
    # confirming a carrier booking and recording a physical handoff. The
    # capability rather than `require_logistics_roles` because
    # `require_capability` is the canonical dependency for a new surface, so the
    # console's gate and the server's are literally one string.
    _actor_id: str = Depends(require_capability(capabilities.RETURNS_LOGISTICS_ACT)),
) -> APIResponse[ShipmentUpdateResult]:
    """Record one carrier observation against one RMA.

    Idempotent, and safe to retry: the same observation submitted twice answers
    `DUPLICATE` and changes nothing, and an observation older than the stored one
    answers `STALE` and is rejected. Both are 200 -- they are correct outcomes of
    a well-formed request, not client errors, and a caller replaying a carrier
    feed must be able to tell "already knew that" from "your request was wrong".
    """
    service = await _service(request)
    update = ShipmentUpdate(
        return_reference=return_reference,
        tracking_reference=payload.trackingReference,
        shipment_status=payload.shipmentStatus,
        # `event_at` is `DATETIME2(3)`, which carries no zone. Converted here,
        # once, so the value compared against the stored one in the UPDATE's
        # WHERE is on the same clock -- an aware timestamp reaching the driver
        # would be compared against naive UTC rows by whatever the driver decided
        # to do with the offset.
        status_at=payload.statusAt.astimezone(UTC).replace(tzinfo=None),
        carrier_code=payload.carrierCode,
        tracking_type=payload.trackingType,
        shipment_details=payload.shipmentDetails,
    )
    try:
        outcome, reading = await service.record_update(update)
    except ShipmentStateSyncFailed as error:
        # The authoritative row committed and the projection did not. Reported
        # rather than swallowed: an accepted shipment the graph has never heard
        # of reads as AWAITING_HANDOFF to every agent, and a 200 here would make
        # that indistinguishable from a return still on the counter.
        logger.warning(
            "return_shipment_graph_sync_failed",
            extra={
                "return_reference": return_reference,
                "tracking_reference": payload.trackingReference,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "SHIPMENT_GRAPH_SYNC_FAILED",
                "message": (
                    "The shipment update was committed to the authoritative store and could "
                    "not be projected into the graph. Resubmitting the identical update is "
                    "safe and will answer DUPLICATE."
                ),
                "retryable": True,
            },
        ) from error
    return APIResponse(data=_result(outcome, reading), meta=_meta(request))
