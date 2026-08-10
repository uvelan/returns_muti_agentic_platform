"""Internal production Returns Support work queue and collaboration service."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, cast

from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.settings import Settings
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository
from return_platform.workflows.production_return_workflow import ProductionReturnEventType


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class SupportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SupportWorkItemStatus(StrEnum):
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    RETURN_CREATION_PENDING = "RETURN_CREATION_PENDING"
    RETURN_CREATED = "RETURN_CREATED"
    SHIPPING_INSTRUCTIONS_PENDING = "SHIPPING_INSTRUCTIONS_PENDING"
    READY_FOR_ASSOCIATE = "READY_FOR_ASSOCIATE"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class SupportMessageType(StrEnum):
    REQUEST = "REQUEST"
    COMMENT = "COMMENT"
    CLARIFICATION_REQUEST = "CLARIFICATION_REQUEST"
    CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
    SYSTEM = "SYSTEM"
    RETURN_CREATION = "RETURN_CREATION"
    SHIPPING_INSTRUCTIONS = "SHIPPING_INSTRUCTIONS"


class SupportWorkItemView(SupportModel):
    id: str
    sessionId: str
    threadId: str
    status: SupportWorkItemStatus
    priority: str
    queue: str
    subject: str
    requestSnapshotDigest: str
    assignedTo: str | None = None
    externalTicketReference: str | None = None
    returnVersion: str | None = None
    returnReference: str | None = None
    returnCreationCommandId: str | None = None
    returnCreationRequestedAt: datetime | None = None
    shippingInstructionReference: str | None = None
    customerResolutionStatus: str | None = None
    acknowledgedAt: datetime | None = None
    returnCreatedAt: datetime | None = None
    shippingInstructionsIssuedAt: datetime | None = None
    customerResolutionRecordedAt: datetime | None = None
    completedAt: datetime | None = None
    slaDueAt: datetime
    version: int = Field(ge=0)
    createdAt: datetime
    updatedAt: datetime


class SupportMessageView(SupportModel):
    id: str
    threadId: str
    sequence: int = Field(ge=1)
    senderRole: str
    senderId: str
    messageType: SupportMessageType
    messageText: str
    attachmentIds: tuple[str, ...] = ()
    businessPayload: dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime


class CreateSupportWorkItemRequest(SupportModel):
    sessionId: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=3, max_length=256)
    supportDraft: str = Field(min_length=10, max_length=16_000)
    requestSnapshotDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    priority: str = Field(default="NORMAL", pattern=r"^(LOW|NORMAL|HIGH|CRITICAL)$")
    idempotencyKey: str = Field(min_length=8, max_length=256)


class CreateSupportMessageRequest(SupportModel):
    messageType: SupportMessageType
    messageText: str = Field(min_length=1, max_length=16_000)
    attachmentIds: tuple[str, ...] = Field(default=(), max_length=100)
    businessPayload: dict[str, Any] = Field(default_factory=dict)
    expectedVersion: int = Field(ge=0)


class SupportAction(StrEnum):
    ASSIGN = "ASSIGN"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    START_WORK = "START_WORK"
    REQUEST_RETURN_CREATION = "REQUEST_RETURN_CREATION"
    RECORD_RETURN_CREATION = "RECORD_RETURN_CREATION"
    RECORD_SHIPPING_INSTRUCTIONS = "RECORD_SHIPPING_INSTRUCTIONS"
    RECORD_CUSTOMER_RESOLUTION = "RECORD_CUSTOMER_RESOLUTION"
    MARK_READY_FOR_ASSOCIATE = "MARK_READY_FOR_ASSOCIATE"
    COMPLETE = "COMPLETE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    ATTACH_EXTERNAL_TICKET = "ATTACH_EXTERNAL_TICKET"


class SupportActionRequest(SupportModel):
    action: SupportAction
    expectedVersion: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=2_000)
    assignee: str | None = Field(default=None, max_length=128)
    returnVersion: str | None = Field(default=None, pattern=r"^(V1|V2)$")
    returnReference: str | None = Field(default=None, max_length=128)
    shippingInstructionReference: str | None = Field(default=None, max_length=128)
    shippingInstructionType: str | None = Field(default=None, max_length=64)
    carrier: str | None = Field(default=None, max_length=64)
    trackingNumbers: tuple[str, ...] = Field(default=(), max_length=100)
    bolReference: str | None = Field(default=None, max_length=128)
    authoritativeReadbackDigest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    customerResolution: str | None = Field(default=None, min_length=2, max_length=128)
    externalTicketReference: str | None = Field(default=None, max_length=256)


#: Shipping instruction types whose issuance also tenders a BOL. Recorded here
#: rather than inline in the router: the set decides both what the caller is
#: authorized for and what is actually recorded, and those two must be the same
#: answer.
_BOL_TENDERING_INSTRUCTION_TYPES: Final = frozenset(
    {"LTL", "BOL", "BRANCH_LTL", "OFFSITE_LTL", "HEAVY_TRUCK_PICKUP"}
)

#: Support actions that record a production workflow event, and which one.
#: Actions absent from this map are support-queue bookkeeping and touch no
#: workflow state.
_PRODUCTION_EVENT_FOR_ACTION: Final[dict[SupportAction, ProductionReturnEventType]] = {
    SupportAction.ACKNOWLEDGE: ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
    SupportAction.RECORD_RETURN_CREATION: ProductionReturnEventType.OMC_RETURN_CREATED,
    SupportAction.RECORD_SHIPPING_INSTRUCTIONS: (
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED
    ),
    SupportAction.RECORD_CUSTOMER_RESOLUTION: (
        ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED
    ),
    SupportAction.CANCEL: ProductionReturnEventType.CANCELLED,
}


def production_events_for_support_action(
    request: SupportActionRequest,
) -> tuple[ProductionReturnEventType, ...]:
    """Every production workflow event this support action will record.

    In recording order, and derived from the request alone -- no service call,
    no database read -- so a caller can be authorized for the whole act before
    anything is mutated. `RECORD_SHIPPING_INSTRUCTIONS` with an LTL/BOL
    instruction type produces *two* events, which is the case that made
    per-event authorization inside the handler unsafe.
    """
    primary = _PRODUCTION_EVENT_FOR_ACTION.get(request.action)
    if primary is None:
        return ()
    if (
        request.action is SupportAction.RECORD_SHIPPING_INSTRUCTIONS
        and (request.shippingInstructionType or "").upper() in _BOL_TENDERING_INSTRUCTION_TYPES
    ):
        return (primary, ProductionReturnEventType.BOL_TENDERED)
    return (primary,)


class ReturnSupportService:
    """Platform-owned business queue; external ticketing is an optional mirror."""

    def __init__(
        self,
        *,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        configuration: ReturnPlatformConfiguration,
        operational_repository: OperationalRepository,
    ) -> None:
        self._client = client
        database = client[settings.mongo_database]
        self._work_items = database["support_work_items"]
        self._messages = database["support_messages"]
        self._outbox = database["integration_outbox"]
        self._config = configuration
        self._repository = operational_repository

    async def ensure_indexes(self) -> None:
        await self._work_items.create_index("sessionId", unique=True)
        await self._work_items.create_index("idempotencyKey", unique=True)
        await self._work_items.create_index(
            [("status", ASCENDING), ("priorityRank", ASCENDING), ("slaDueAt", ASCENDING)]
        )
        await self._work_items.create_index([("assignedTo", ASCENDING), ("status", ASCENDING)])
        await self._messages.create_index(
            [("threadId", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
        await self._messages.create_index([("threadId", ASCENDING), ("createdAt", ASCENDING)])
        await self._outbox.create_index("idempotencyKey", unique=True)
        await self._outbox.create_index([("status", ASCENDING), ("nextAttemptAt", ASCENDING)])
        await self._outbox.create_index([("createdAt", DESCENDING)])

    @staticmethod
    def _work_item_view(document: dict[str, Any]) -> SupportWorkItemView:
        payload = {
            key: value for key, value in document.items() if key in SupportWorkItemView.model_fields
        }
        payload["id"] = str(document["_id"])
        return SupportWorkItemView.model_validate(payload)

    @staticmethod
    def _message_view(document: dict[str, Any]) -> SupportMessageView:
        payload = {
            key: value for key, value in document.items() if key in SupportMessageView.model_fields
        }
        payload["id"] = str(document["_id"])
        return SupportMessageView.model_validate(payload)

    async def create_work_item(
        self,
        request: CreateSupportWorkItemRequest,
        *,
        actor_id: str,
        correlation_id: str,
    ) -> SupportWorkItemView:
        existing = await self._work_items.find_one({"idempotencyKey": request.idempotencyKey})
        if existing is not None:
            return self._work_item_view(cast(dict[str, Any], existing))
        now = _now()
        item_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        sla_minutes = self._config.workflow.sla_minutes["support_acknowledgement"]
        document: dict[str, Any] = {
            "_id": item_id,
            "sessionId": request.sessionId,
            "threadId": thread_id,
            "status": SupportWorkItemStatus.NEW.value,
            "priority": request.priority,
            "priorityRank": {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}[request.priority],
            "queue": "RETURNS_SUPPORT",
            "subject": request.subject,
            "requestSnapshotDigest": request.requestSnapshotDigest,
            "assignedTo": None,
            "externalTicketReference": None,
            "returnVersion": None,
            "returnReference": None,
            "returnCreationCommandId": None,
            "returnCreationRequestedAt": None,
            "shippingInstructionReference": None,
            "customerResolutionStatus": None,
            "acknowledgedAt": None,
            "returnCreatedAt": None,
            "shippingInstructionsIssuedAt": None,
            "customerResolutionRecordedAt": None,
            "completedAt": None,
            "slaDueAt": now + timedelta(minutes=sla_minutes),
            "lastMessageSequence": 1,
            "version": 0,
            "idempotencyKey": request.idempotencyKey,
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
        }
        message = {
            "_id": str(uuid.uuid4()),
            "threadId": thread_id,
            "sequence": 1,
            "senderRole": "ASSOCIATE",
            "senderId": actor_id,
            "messageType": SupportMessageType.REQUEST.value,
            "messageText": request.supportDraft,
            "attachmentIds": [],
            "businessPayload": {"requestSnapshotDigest": request.requestSnapshotDigest},
            "createdAt": now,
        }
        outbox: dict[str, Any] | None = None
        if self._config.support.external_mirror_enabled:
            outbox = {
                "_id": str(uuid.uuid4()),
                "topic": self._config.support.external_ticket_outbox_topic,
                "aggregateType": "SUPPORT_WORK_ITEM",
                "aggregateId": item_id,
                "idempotencyKey": f"external-ticket:{item_id}",
                "payload": {
                    "workItemId": item_id,
                    "sessionId": request.sessionId,
                    "subject": request.subject,
                    "requestSnapshotDigest": request.requestSnapshotDigest,
                },
                "status": "PENDING",
                "attemptCount": 0,
                "nextAttemptAt": now,
                "createdAt": now,
                "updatedAt": now,
            }

        async def transaction(mongo_session: Any) -> None:
            await self._work_items.insert_one(document, session=mongo_session)
            await self._messages.insert_one(message, session=mongo_session)
            if outbox is not None:
                await self._outbox.insert_one(outbox, session=mongo_session)
            await self._repository.update_return(
                request.sessionId,
                {
                    "supportWorkItemId": item_id,
                    "supportTicketReference": item_id,
                    "supportStatus": SupportWorkItemStatus.NEW.value,
                    "currentStage": "SUPPORT_REQUEST",
                    "status": "WAITING_SUPPORT",
                },
                session=mongo_session,
            )
            await self._repository.append_event(
                request.sessionId,
                event_type="SUPPORT_WORK_ITEM_CREATED",
                actor_type="USER",
                actor_id=actor_id,
                payload={"workItemId": item_id, "threadId": thread_id},
                deduplication_key=f"support-work-item:{item_id}",
                session=mongo_session,
            )

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(transaction)
        except DuplicateKeyError:
            existing = await self._work_items.find_one({"idempotencyKey": request.idempotencyKey})
            if existing is None:
                raise
            return self._work_item_view(cast(dict[str, Any], existing))

        return self._work_item_view(document)

    async def list_work_items(
        self, *, status: str | None = None, limit: int = 200
    ) -> list[SupportWorkItemView]:
        query: dict[str, Any] = {"status": status} if status else {}
        cursor = (
            self._work_items.find(query)
            .sort([("priorityRank", ASCENDING), ("slaDueAt", ASCENDING)])
            .limit(limit)
        )
        return [self._work_item_view(cast(dict[str, Any], item)) async for item in cursor]

    async def get_work_item(self, work_item_id: str) -> SupportWorkItemView | None:
        document = await self._work_items.find_one({"_id": work_item_id})
        return None if document is None else self._work_item_view(cast(dict[str, Any], document))

    async def list_messages(self, thread_id: str) -> list[SupportMessageView]:
        cursor = self._messages.find({"threadId": thread_id}).sort("sequence", ASCENDING)
        return [self._message_view(cast(dict[str, Any], item)) async for item in cursor]

    async def add_message(
        self,
        work_item_id: str,
        request: CreateSupportMessageRequest,
        *,
        actor_id: str,
        actor_role: str,
    ) -> tuple[SupportWorkItemView, SupportMessageView]:
        now = _now()
        updated = await self._work_items.find_one_and_update(
            {"_id": work_item_id, "version": request.expectedVersion},
            {"$inc": {"lastMessageSequence": 1, "version": 1}, "$set": {"updatedAt": now}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            if await self._work_items.find_one({"_id": work_item_id}) is None:
                raise KeyError(work_item_id)
            raise ConcurrencyConflictError(work_item_id)
        seq_raw = updated["lastMessageSequence"]
        if not isinstance(seq_raw, (int, float)):
            raise TypeError(f"Expected numeric lastMessageSequence, got {type(seq_raw).__name__}")
        sequence = int(seq_raw)
        message_document: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "threadId": str(updated["threadId"]),
            "sequence": sequence,
            "senderRole": actor_role,
            "senderId": actor_id,
            "messageType": request.messageType.value,
            "messageText": request.messageText,
            "attachmentIds": list(request.attachmentIds),
            "businessPayload": request.businessPayload,
            "createdAt": now,
        }
        await self._messages.insert_one(message_document)
        return self._work_item_view(cast(dict[str, Any], updated)), self._message_view(
            message_document
        )

    @staticmethod
    def _validate_action(status: SupportWorkItemStatus, action: SupportAction) -> None:
        allowed: dict[SupportWorkItemStatus, frozenset[SupportAction]] = {
            SupportWorkItemStatus.NEW: frozenset(
                {
                    SupportAction.ASSIGN,
                    SupportAction.ACKNOWLEDGE,
                    SupportAction.REQUEST_CLARIFICATION,
                    SupportAction.REJECT,
                    SupportAction.CANCEL,
                    SupportAction.ATTACH_EXTERNAL_TICKET,
                }
            ),
            SupportWorkItemStatus.ACKNOWLEDGED: frozenset(
                {
                    SupportAction.ASSIGN,
                    SupportAction.START_WORK,
                    SupportAction.REQUEST_CLARIFICATION,
                    SupportAction.REQUEST_RETURN_CREATION,
                    SupportAction.RECORD_RETURN_CREATION,
                    SupportAction.REJECT,
                    SupportAction.CANCEL,
                    SupportAction.ATTACH_EXTERNAL_TICKET,
                }
            ),
            SupportWorkItemStatus.CLARIFICATION_REQUIRED: frozenset(
                {
                    SupportAction.ASSIGN,
                    SupportAction.START_WORK,
                    SupportAction.REJECT,
                    SupportAction.CANCEL,
                    SupportAction.ATTACH_EXTERNAL_TICKET,
                }
            ),
            SupportWorkItemStatus.IN_PROGRESS: frozenset(
                {
                    SupportAction.ASSIGN,
                    SupportAction.REQUEST_CLARIFICATION,
                    SupportAction.REQUEST_RETURN_CREATION,
                    SupportAction.RECORD_RETURN_CREATION,
                    SupportAction.REJECT,
                    SupportAction.CANCEL,
                    SupportAction.ATTACH_EXTERNAL_TICKET,
                }
            ),
            SupportWorkItemStatus.RETURN_CREATION_PENDING: frozenset(
                {
                    SupportAction.ASSIGN,
                    SupportAction.RECORD_RETURN_CREATION,
                    SupportAction.REQUEST_CLARIFICATION,
                    SupportAction.CANCEL,
                    SupportAction.ATTACH_EXTERNAL_TICKET,
                }
            ),
            SupportWorkItemStatus.RETURN_CREATED: frozenset(
                {
                    SupportAction.RECORD_SHIPPING_INSTRUCTIONS,
                    SupportAction.RECORD_CUSTOMER_RESOLUTION,
                    SupportAction.MARK_READY_FOR_ASSOCIATE,
                    SupportAction.COMPLETE,
                    SupportAction.CANCEL,
                    SupportAction.ATTACH_EXTERNAL_TICKET,
                }
            ),
            SupportWorkItemStatus.SHIPPING_INSTRUCTIONS_PENDING: frozenset(
                {
                    SupportAction.RECORD_SHIPPING_INSTRUCTIONS,
                    SupportAction.RECORD_CUSTOMER_RESOLUTION,
                    SupportAction.CANCEL,
                    SupportAction.ATTACH_EXTERNAL_TICKET,
                }
            ),
            SupportWorkItemStatus.READY_FOR_ASSOCIATE: frozenset(
                {
                    SupportAction.RECORD_CUSTOMER_RESOLUTION,
                    SupportAction.COMPLETE,
                    SupportAction.CANCEL,
                    SupportAction.ATTACH_EXTERNAL_TICKET,
                }
            ),
            SupportWorkItemStatus.COMPLETED: frozenset({SupportAction.ATTACH_EXTERNAL_TICKET}),
            SupportWorkItemStatus.REJECTED: frozenset({SupportAction.ATTACH_EXTERNAL_TICKET}),
            SupportWorkItemStatus.CANCELLED: frozenset({SupportAction.ATTACH_EXTERNAL_TICKET}),
        }
        if action not in allowed[status]:
            raise ValueError(f"Action {action.value} is not valid from {status.value}")

    async def apply_action(
        self,
        work_item_id: str,
        request: SupportActionRequest,
        *,
        actor_id: str,
    ) -> SupportWorkItemView:
        current = await self._work_items.find_one({"_id": work_item_id})
        if current is None:
            raise KeyError(work_item_id)
        version_raw = current["version"]
        if not isinstance(version_raw, (int, float)):
            raise TypeError(f"Expected numeric version, got {type(version_raw).__name__}")
        if int(version_raw) != request.expectedVersion:
            raise ConcurrencyConflictError(work_item_id)
        now = _now()
        updates: dict[str, Any] = {"updatedAt": now, "lastActionReason": request.reason}
        status = SupportWorkItemStatus(str(current["status"]))
        action = request.action
        self._validate_action(status, action)
        if action is SupportAction.ASSIGN:
            if not request.assignee:
                raise ValueError("ASSIGN requires assignee")
            updates.update(
                {"assignedTo": request.assignee, "status": SupportWorkItemStatus.IN_PROGRESS.value}
            )
        elif action is SupportAction.ACKNOWLEDGE:
            updates.update(
                {"status": SupportWorkItemStatus.ACKNOWLEDGED.value, "acknowledgedAt": now}
            )
        elif action is SupportAction.REQUEST_CLARIFICATION:
            updates["status"] = SupportWorkItemStatus.CLARIFICATION_REQUIRED.value
        elif action is SupportAction.START_WORK:
            updates["status"] = SupportWorkItemStatus.IN_PROGRESS.value
        elif action is SupportAction.REQUEST_RETURN_CREATION:
            if not request.returnVersion:
                raise ValueError("REQUEST_RETURN_CREATION requires returnVersion")
            session_id = str(current["sessionId"])
            idempotency_key = f"omc-return-create:{session_id}"
            command_payload = {
                "sessionId": session_id,
                "supportWorkItemId": work_item_id,
                "returnVersion": request.returnVersion,
                "requestSnapshotDigest": str(current["requestSnapshotDigest"]),
                "requestedBy": actor_id,
            }
            request_digest = _digest(command_payload)
            command = await self._repository.record_omc_command(
                command_id=str(uuid.uuid4()),
                idempotency_key=idempotency_key,
                session_id=session_id,
                support_work_item_id=work_item_id,
                operation="CREATE_CUSTOMER_RETURN",
                request_digest=request_digest,
                request_payload=command_payload,
                actor_id=actor_id,
            )
            command_id = str(command["commandId"])
            await self._repository.enqueue_integration_command(
                topic=self._config.integrations.omc_return_create.topic,
                aggregate_type="RETURN_SESSION",
                aggregate_id=session_id,
                idempotency_key=idempotency_key,
                payload={"commandId": command_id, **command_payload},
            )
            updates.update(
                {
                    "status": SupportWorkItemStatus.RETURN_CREATION_PENDING.value,
                    "returnVersion": request.returnVersion,
                    "returnCreationCommandId": command_id,
                    "returnCreationRequestedAt": now,
                }
            )
        elif action is SupportAction.RECORD_RETURN_CREATION:
            if not request.returnVersion or not request.returnReference:
                raise ValueError(
                    "RECORD_RETURN_CREATION requires returnVersion and returnReference"
                )
            readback_payload = {
                "returnVersion": request.returnVersion,
                "returnReference": request.returnReference,
                "recordedBy": actor_id,
                "evidenceMode": "MANUAL_OR_INTEGRATION_READBACK",
            }
            readback_digest = request.authoritativeReadbackDigest or _digest(readback_payload)
            await self._repository.confirm_omc_command(
                session_id=str(current["sessionId"]),
                operation="CREATE_CUSTOMER_RETURN",
                authoritative_return_reference=request.returnReference,
                authoritative_version=request.returnVersion,
                readback_digest=readback_digest,
                response_payload=readback_payload,
            )
            updates.update(
                {
                    "status": SupportWorkItemStatus.RETURN_CREATED.value,
                    "returnVersion": request.returnVersion,
                    "returnReference": request.returnReference,
                    "returnCreatedAt": now,
                    "returnReadbackDigest": readback_digest,
                }
            )
        elif action is SupportAction.RECORD_SHIPPING_INSTRUCTIONS:
            if not request.shippingInstructionReference:
                raise ValueError("RECORD_SHIPPING_INSTRUCTIONS requires a reference")
            session_id = str(current["sessionId"])
            handling_units = await self._repository.list_handling_units(session_id)
            await self._repository.record_shipping_instruction(
                session_id=session_id,
                instruction_id=request.shippingInstructionReference,
                instruction_type=request.shippingInstructionType or "UNKNOWN",
                source_system="OMC_OR_SUPPORT_READBACK",
                issued_by=actor_id,
                handling_unit_ids=[str(item["handlingUnitId"]) for item in handling_units],
                carrier=request.carrier,
                tracking_numbers=list(request.trackingNumbers),
                bol_reference=request.bolReference,
                evidence_reference=request.shippingInstructionReference,
            )
            await self._repository.enqueue_integration_command(
                topic=self._config.integrations.customer_notification.topic,
                aggregate_type="RETURN_SESSION",
                aggregate_id=session_id,
                idempotency_key=(
                    f"customer-notification:shipping:{session_id}:"
                    f"{request.shippingInstructionReference}"
                ),
                payload={
                    "eventType": "SHIPPING_INSTRUCTIONS_ISSUED",
                    "sessionId": session_id,
                    "shippingInstructionReference": (request.shippingInstructionReference),
                },
            )
            updates.update(
                {
                    "status": SupportWorkItemStatus.READY_FOR_ASSOCIATE.value,
                    "shippingInstructionReference": request.shippingInstructionReference,
                    "shippingInstructionsIssuedAt": now,
                }
            )
        elif action is SupportAction.RECORD_CUSTOMER_RESOLUTION:
            if not request.customerResolution:
                raise ValueError("RECORD_CUSTOMER_RESOLUTION requires customerResolution")
            session_id = str(current["sessionId"])
            await self._repository.enqueue_integration_command(
                topic=self._config.integrations.customer_notification.topic,
                aggregate_type="RETURN_SESSION",
                aggregate_id=session_id,
                idempotency_key=(
                    f"customer-notification:resolution:{session_id}:{request.customerResolution}"
                ),
                payload={
                    "eventType": "CUSTOMER_RESOLUTION_RECORDED",
                    "sessionId": session_id,
                    "customerResolution": request.customerResolution,
                },
            )
            updates.update(
                {
                    "customerResolutionStatus": request.customerResolution,
                    "customerResolutionRecordedAt": now,
                }
            )
        elif action is SupportAction.MARK_READY_FOR_ASSOCIATE:
            updates["status"] = SupportWorkItemStatus.READY_FOR_ASSOCIATE.value
        elif action is SupportAction.COMPLETE:
            updates.update({"status": SupportWorkItemStatus.COMPLETED.value, "completedAt": now})
        elif action is SupportAction.REJECT:
            updates.update({"status": SupportWorkItemStatus.REJECTED.value, "completedAt": now})
        elif action is SupportAction.CANCEL:
            updates.update({"status": SupportWorkItemStatus.CANCELLED.value, "completedAt": now})
        elif action is SupportAction.ATTACH_EXTERNAL_TICKET:
            if not request.externalTicketReference:
                raise ValueError("ATTACH_EXTERNAL_TICKET requires externalTicketReference")
            updates["externalTicketReference"] = request.externalTicketReference
        else:  # pragma: no cover - enum exhaustiveness
            raise ValueError(f"Unsupported Support action {action.value}")
        updated = await self._work_items.find_one_and_update(
            {"_id": work_item_id, "version": request.expectedVersion, "status": status.value},
            {"$set": updates, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ConcurrencyConflictError(work_item_id)
        session_id = str(updated["sessionId"])
        return_updates: dict[str, Any] = {"supportStatus": str(updated["status"])}
        if updated.get("returnReference"):
            return_updates.update(
                {
                    "returnReference": updated["returnReference"],
                    "omcReturnVersion": updated.get("returnVersion"),
                    "currentStage": "RETURN_CREATION",
                }
            )
        if updated.get("shippingInstructionReference"):
            return_updates.update(
                {
                    "shippingInstructionReference": updated["shippingInstructionReference"],
                    "currentStage": "PHYSICAL_RETURN_SETUP",
                }
            )
        if updated.get("customerResolutionStatus"):
            return_updates["customerResolutionStatus"] = str(updated["customerResolutionStatus"])
        await self._repository.update_return(session_id, return_updates)
        await self._repository.append_event(
            session_id,
            event_type=f"SUPPORT_{action.value}",
            actor_type="USER",
            actor_id=actor_id,
            payload={
                "workItemId": work_item_id,
                "status": updated["status"],
                "reasonDigest": _digest(request.reason),
            },
        )
        return self._work_item_view(cast(dict[str, Any], updated))
