"""Production controls for branch staging, pickup coordination, and artifact metadata."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.operations.repository import OperationalRepository


class PhysicalOperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BranchStagingRequest(PhysicalOperationModel):
    handlingUnitId: str = Field(min_length=3, max_length=128)
    branchId: str = Field(min_length=1, max_length=128)
    stagingLocation: str = Field(min_length=1, max_length=256)
    returnNumberTagApplied: bool
    manufacturerBoxDirectlyMarked: bool
    inventoryAddedToBranch: bool
    expectedHandlingUnitVersion: int = Field(ge=0)


class PickupAction(StrEnum):
    AUTHORIZE = "AUTHORIZE"
    REQUEST_BOOKING = "REQUEST_BOOKING"
    CONFIRM_BOOKING = "CONFIRM_BOOKING"
    SCHEDULE = "SCHEDULE"
    CONFIRM_READINESS = "CONFIRM_READINESS"
    RECORD_CARRIER_ARRIVAL = "RECORD_CARRIER_ARRIVAL"
    RECORD_PICKUP = "RECORD_PICKUP"
    RECORD_FAILURE = "RECORD_FAILURE"
    CANCEL = "CANCEL"


class PickupActionRequest(PhysicalOperationModel):
    action: PickupAction
    expectedVersion: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=1_000)
    carrier: str | None = Field(default=None, max_length=128)
    serviceLevel: str | None = Field(default=None, max_length=128)
    equipmentRequirements: list[str] = Field(default_factory=list, max_length=20)
    scheduledWindowStart: datetime | None = None
    scheduledWindowEnd: datetime | None = None
    bolReference: str | None = Field(default=None, max_length=128)
    bookingConfirmationReference: str | None = Field(default=None, max_length=256)
    pickupConfirmationReference: str | None = Field(default=None, max_length=256)
    failureCode: str | None = Field(default=None, max_length=128)


class DocumentArtifactRegistration(PhysicalOperationModel):
    artifactId: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=8, max_length=128)
    artifactType: str = Field(min_length=2, max_length=64)
    storageProvider: str = Field(min_length=2, max_length=64)
    storageKey: str = Field(min_length=3, max_length=1_024)
    contentType: str = Field(min_length=3, max_length=128)
    sizeBytes: int = Field(ge=0, le=100_000_000)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    classification: str = Field(default="RETURN_EVIDENCE", min_length=3, max_length=64)


class BranchStagingService:
    def __init__(
        self,
        repository: OperationalRepository,
        configuration: ReturnPlatformConfiguration,
    ) -> None:
        self._repository = repository
        self._configuration = configuration

    async def confirm(
        self,
        session_id: str,
        request: BranchStagingRequest,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        session = await self._repository.get_return(session_id)
        if session is None:
            raise KeyError(session_id)
        if session.productPresence != "PRESENT_AT_BRANCH":
            raise ValueError(
                "Branch staging is allowed only when the product is present at branch."
            )
        if session.returnReference is None:
            raise ValueError(
                "An authoritative return/RMA number is required before branch staging."
            )
        policy = self._configuration.return_policy.branch_staging
        if policy.require_return_number_tag and not request.returnNumberTagApplied:
            raise ValueError("A removable return-number tag is required.")
        if not policy.allow_manufacturer_box_marking and request.manufacturerBoxDirectlyMarked:
            raise ValueError("The manufacturer box must not be marked directly.")
        if not policy.allow_branch_inventory_addition and request.inventoryAddedToBranch:
            raise ValueError("Returned goods must not be added to branch inventory.")
        handling = await self._repository.get_handling_unit(request.handlingUnitId)
        if handling is None or handling.get("sessionId") != session_id:
            raise KeyError(request.handlingUnitId)
        updated_handling = await self._repository.update_handling_unit(
            request.handlingUnitId,
            {
                "physicalStatus": "STAGED_AT_BRANCH",
                "stagingLocation": request.stagingLocation,
                "branchId": request.branchId,
            },
            expected_version=request.expectedHandlingUnitVersion,
        )
        record = await self._repository.upsert_branch_staging_record(
            session_id=session_id,
            handling_unit_id=request.handlingUnitId,
            branch_id=request.branchId,
            staging_location=request.stagingLocation,
            return_number_tag_applied=request.returnNumberTagApplied,
            manufacturer_box_directly_marked=request.manufacturerBoxDirectlyMarked,
            inventory_added_to_branch=request.inventoryAddedToBranch,
            actor_id=actor_id,
        )
        await self._repository.append_event(
            session_id,
            event_type="BRANCH_HANDLING_UNIT_STAGED",
            actor_type="USER",
            actor_id=actor_id,
            payload={
                "handlingUnitId": request.handlingUnitId,
                "stagingLocation": request.stagingLocation,
                "branchId": request.branchId,
            },
            deduplication_key=f"branch-staging:{request.handlingUnitId}",
        )
        return {
            "stagingRecord": {key: value for key, value in record.items() if key != "_id"},
            "handlingUnit": {key: value for key, value in updated_handling.items() if key != "_id"},
        }


class PickupCoordinationService:
    _allowed: ClassVar[dict[str, frozenset[PickupAction]]] = {
        "ASSESSMENT_COMPLETE": frozenset({PickupAction.AUTHORIZE, PickupAction.CANCEL}),
        "AUTHORIZED": frozenset(
            {PickupAction.REQUEST_BOOKING, PickupAction.SCHEDULE, PickupAction.CANCEL}
        ),
        "BOOKING_REQUESTED": frozenset(
            {PickupAction.CONFIRM_BOOKING, PickupAction.RECORD_FAILURE, PickupAction.CANCEL}
        ),
        "SCHEDULED": frozenset(
            {PickupAction.CONFIRM_READINESS, PickupAction.RECORD_FAILURE, PickupAction.CANCEL}
        ),
        "CUSTOMER_READY": frozenset(
            {PickupAction.RECORD_CARRIER_ARRIVAL, PickupAction.RECORD_FAILURE, PickupAction.CANCEL}
        ),
        "CARRIER_ARRIVED": frozenset(
            {PickupAction.RECORD_PICKUP, PickupAction.RECORD_FAILURE, PickupAction.CANCEL}
        ),
        "FAILED": frozenset(
            {PickupAction.REQUEST_BOOKING, PickupAction.SCHEDULE, PickupAction.CANCEL}
        ),
        "PICKED_UP": frozenset(),
        "CANCELLED": frozenset(),
    }

    def __init__(
        self,
        repository: OperationalRepository,
        configuration: ReturnPlatformConfiguration,
    ) -> None:
        self._repository = repository
        self._configuration = configuration

    async def apply(
        self,
        session_id: str,
        request: PickupActionRequest,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        session = await self._repository.get_return(session_id)
        if session is None:
            raise KeyError(session_id)
        if not (session.productPresence or "").startswith("OFFSITE_"):
            raise ValueError("Pickup coordination is allowed only for offsite products.")
        current = await self._repository.get_pickup_request(session_id)
        if current is None:
            raise KeyError(session_id)
        current_status = str(current.get("status", "ASSESSMENT_COMPLETE"))
        allowed = self._allowed.get(current_status, frozenset())
        if request.action not in allowed:
            raise ValueError(
                f"Pickup action {request.action.value} is not valid from {current_status}."
            )
        now = datetime.now(UTC)
        updates: dict[str, Any] = {
            "lastAction": request.action.value,
            "lastActionReason": request.reason,
            "lastActionBy": actor_id,
        }
        event_type: str
        if request.action is PickupAction.AUTHORIZE:
            if not request.carrier or not request.serviceLevel:
                raise ValueError("Carrier and service level are required for authorization.")
            updates.update(
                {
                    "status": "AUTHORIZED",
                    "carrier": request.carrier,
                    "serviceLevel": request.serviceLevel,
                    "equipmentRequirements": request.equipmentRequirements,
                    "authorizedBy": actor_id,
                    "authorizedAt": now,
                }
            )
            event_type = "PICKUP_AUTHORIZED"
        elif request.action is PickupAction.REQUEST_BOOKING:
            if request.scheduledWindowStart is None or request.scheduledWindowEnd is None:
                raise ValueError("A requested pickup window is required.")
            if request.scheduledWindowEnd <= request.scheduledWindowStart:
                raise ValueError("Pickup window end must be after start.")
            booking_payload = {
                "sessionId": session_id,
                "pickupRequestId": str(current.get("pickupRequestId")),
                "carrier": request.carrier or current.get("carrier"),
                "serviceLevel": request.serviceLevel or current.get("serviceLevel"),
                "equipmentRequirements": (
                    request.equipmentRequirements or list(current.get("equipmentRequirements", []))
                ),
                "requestedWindowStart": request.scheduledWindowStart.isoformat(),
                "requestedWindowEnd": request.scheduledWindowEnd.isoformat(),
                "bolReference": request.bolReference,
                "requestedBy": actor_id,
            }
            if not booking_payload["carrier"] or not booking_payload["serviceLevel"]:
                raise ValueError("Carrier and service level are required for booking.")
            command = await self._repository.enqueue_integration_command(
                topic=self._configuration.integrations.carrier_booking.topic,
                aggregate_type="PICKUP_REQUEST",
                aggregate_id=session_id,
                idempotency_key=(
                    f"carrier-booking:{current.get('pickupRequestId')}:{request.expectedVersion}"
                ),
                payload=booking_payload,
            )
            updates.update(
                {
                    "status": "BOOKING_REQUESTED",
                    "requestedWindowStart": request.scheduledWindowStart,
                    "requestedWindowEnd": request.scheduledWindowEnd,
                    "bolReference": request.bolReference,
                    "bookingCommandId": str(command["_id"]),
                    "bookingRequestedBy": actor_id,
                    "bookingRequestedAt": now,
                    "failureCode": None,
                }
            )
            event_type = "CARRIER_BOOKING_REQUESTED"
        elif request.action in {PickupAction.CONFIRM_BOOKING, PickupAction.SCHEDULE}:
            if not request.bookingConfirmationReference:
                raise ValueError("Authoritative carrier booking evidence is required.")
            start = request.scheduledWindowStart or current.get("requestedWindowStart")
            end = request.scheduledWindowEnd or current.get("requestedWindowEnd")
            if start is None or end is None:
                raise ValueError("A complete confirmed pickup window is required.")
            if end <= start:
                raise ValueError("Pickup window end must be after start.")
            updates.update(
                {
                    "status": "SCHEDULED",
                    "scheduledWindowStart": start,
                    "scheduledWindowEnd": end,
                    "bolReference": request.bolReference or current.get("bolReference"),
                    "bookingConfirmationReference": (request.bookingConfirmationReference),
                    "scheduledBy": actor_id,
                    "scheduledAt": now,
                    "failureCode": None,
                }
            )
            event_type = "PICKUP_APPOINTMENT_CONFIRMED"
        elif request.action is PickupAction.CONFIRM_READINESS:
            updates.update({"status": "CUSTOMER_READY", "customerReadyAt": now})
            event_type = "PICKUP_SITE_READY"
        elif request.action is PickupAction.RECORD_CARRIER_ARRIVAL:
            updates.update({"status": "CARRIER_ARRIVED", "carrierArrivedAt": now})
            event_type = "CARRIER_ARRIVED_AT_PICKUP_SITE"
        elif request.action is PickupAction.RECORD_PICKUP:
            if not request.pickupConfirmationReference:
                raise ValueError("Authoritative pickup evidence is required.")
            updates.update(
                {
                    "status": "PICKED_UP",
                    "pickupConfirmationReference": request.pickupConfirmationReference,
                    "pickedUpAt": now,
                }
            )
            event_type = "OFFSITE_PICKUP_CONFIRMED"
        elif request.action is PickupAction.RECORD_FAILURE:
            updates.update(
                {
                    "status": "FAILED",
                    "failureCode": request.failureCode or "PICKUP_FAILED",
                    "failedAt": now,
                }
            )
            event_type = "PICKUP_FAILED"
        else:
            updates.update({"status": "CANCELLED", "cancelledAt": now})
            event_type = "PICKUP_CANCELLED"
        updated = await self._repository.update_pickup_request(
            session_id,
            updates,
            expected_version=request.expectedVersion,
        )
        await self._repository.append_event(
            session_id,
            event_type=event_type,
            actor_type="USER",
            actor_id=actor_id,
            payload={
                "pickupRequestId": updated.get("pickupRequestId"),
                "status": updated.get("status"),
                "carrier": updated.get("carrier"),
                "bolReference": updated.get("bolReference"),
                "evidenceReference": request.pickupConfirmationReference,
            },
            deduplication_key=(
                f"pickup-action:{updated.get('pickupRequestId')}:{request.action.value}:"
                f"{request.expectedVersion}"
            ),
        )
        return {key: value for key, value in updated.items() if key != "_id"}
