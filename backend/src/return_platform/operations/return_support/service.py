"""Internal production Returns Support work queue and collaboration service."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, cast

from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.settings import Settings
from return_platform.operations.integrations.outbox import INTEGRATION_OUTBOX_COLLECTION
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository
from return_platform.workflows.production_return_workflow import ProductionReturnEventType


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


#: The receiver-dedupe index, asserted by name (contracts.md sect. 7).
SUPPORT_MESSAGE_DELIVERY_INDEX: Final = "support_message_delivery_unique"


class SupportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SupportWorkItemStatus(StrEnum):
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    RETURN_CREATION_PENDING = "RETURN_CREATION_PENDING"
    #: Support has referred the question to someone outside the platform and is
    #: waiting on them: a manufacturer on a warranty or a special-order
    #: acceptance, a carrier on a delivery claim (plan sect. 7.6).
    #:
    #: **One new status total, parameterised by route rather than three.** The
    #: work item stays on its route's queue and the case stays `AWAITING_SUPPORT`
    #: -- an external party reviewing is not a terminal state and not a decision.
    #: The action that sets it belongs to the Support console work (3A.6); the
    #: vocabulary is declared here so that the three paths cannot each invent
    #: their own spelling of it.
    EXTERNAL_PARTY_REVIEW = "EXTERNAL_PARTY_REVIEW"
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
    # A work item belongs to a return *session* (the legacy shape) or to a
    # *case* (Channel B). Exactly one is set, and neither can be made required
    # without excluding the other -- a case is the thing sessions hang off, so
    # inventing a session id to satisfy the older field would be a lie the rest
    # of the system would then try to resolve.
    sessionId: str | None = None
    caseId: str | None = None
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


class CaseSupportThread(SupportModel):
    """The one Channel B thread a case gets, and whether this call opened it."""

    workItemId: str
    threadId: str
    created: bool


class SupportMessagePost(SupportModel):
    """What one delivery to the case thread came to.

    `absorbed` is the interesting field: it means the receiver already held a
    message under this `deliveryId`, so this send was a redelivery of one that
    already arrived. A caller treats it as success -- absorption *is* delivery
    (contracts.md sect. 7) -- and the review it belongs to still reaches `SENT`.
    """

    workItemId: str
    threadId: str
    messageId: str
    sequence: int
    deliveryId: str | None
    absorbed: bool


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
    *,
    bol_tendering_instruction_types: Collection[str],
) -> tuple[ProductionReturnEventType, ...]:
    """Every production workflow event this support action will record.

    In recording order, and derived from the request and the active policy alone
    -- no service call, no database read -- so a caller can be authorized for the
    whole act before anything is mutated. `RECORD_SHIPPING_INSTRUCTIONS` with a
    BOL-tendering instruction type produces *two* events, which is the case that
    made per-event authorization inside the handler unsafe.

    `bol_tendering_instruction_types` is `return_policy.bol_tendering_instruction_types`
    from the snapshot the process is serving, passed in rather than read here so
    this stays a pure function of its inputs and so the caller cannot compute the
    authorization set from one policy and the recorded set from another.

    Keyword-only and required: the previous hardcoded set answered "does not
    tender a BOL" for every instruction type it had not been told about, so an
    operator adding a freight return method through the Control Centre would have
    dropped a production event with nothing failing.
    """
    primary = _PRODUCTION_EVENT_FOR_ACTION.get(request.action)
    if primary is None:
        return ()
    tendering = {value.upper() for value in bol_tendering_instruction_types}
    if (
        request.action is SupportAction.RECORD_SHIPPING_INSTRUCTIONS
        and (request.shippingInstructionType or "").upper() in tendering
    ):
        return (primary, ProductionReturnEventType.BOL_TENDERED)
    return (primary,)


#: Queue ordering for each priority. One spelling, shared by every writer.
_PRIORITY_RANK: Final[dict[str, int]] = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}


def _work_item_document(
    *,
    item_id: str,
    thread_id: str,
    now: datetime,
    sla_due_at: datetime,
    idempotency_key: str,
    created_by: str,
    subject: str,
    request_snapshot_digest: str,
    priority: str = "NORMAL",
    queue: str = "RETURNS_SUPPORT",
    scope: dict[str, Any],
) -> dict[str, Any]:
    """The shared work-item skeleton `create_work_item` and `open_case_thread` write.

    The two writers used to spell all ~30 fields out independently, and the only
    honest difference between the copies was `scope` -- `{"sessionId": ...}` for
    the legacy session shape, `{"caseId": ..., "tenantId": ..., "sessionId": None}`
    for Channel B. Everything else was one skeleton written twice, which is how
    the copies drift.
    """
    return {
        "_id": item_id,
        **scope,
        "threadId": thread_id,
        "status": SupportWorkItemStatus.NEW.value,
        "priority": priority,
        "priorityRank": _PRIORITY_RANK[priority],
        "queue": queue,
        "subject": subject,
        "requestSnapshotDigest": request_snapshot_digest,
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
        "slaDueAt": sla_due_at,
        "lastMessageSequence": 1,
        "version": 0,
        "idempotencyKey": idempotency_key,
        "createdBy": created_by,
        "createdAt": now,
        "updatedAt": now,
    }


def _opening_message(
    *,
    thread_id: str,
    now: datetime,
    sender_role: str,
    sender_id: str,
    text: str,
    business_payload: dict[str, Any],
) -> dict[str, Any]:
    """The thread's sequence-1 REQUEST message, shared by both openers."""
    return {
        "_id": str(uuid.uuid4()),
        "threadId": thread_id,
        "sequence": 1,
        "senderRole": sender_role,
        "senderId": sender_id,
        "messageType": SupportMessageType.REQUEST.value,
        "messageText": text,
        "attachmentIds": [],
        "businessPayload": business_payload,
        "createdAt": now,
    }


#: Which actions are legal from each status. Module-level and immutable rather
#: than rebuilt inside every `_validate_action` call, which is what it used to
#: be -- the table is configuration-shaped data, not per-call state.
_ALLOWED_ACTIONS: Final[dict[SupportWorkItemStatus, frozenset[SupportAction]]] = {
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
    # A work item waiting on a manufacturer or a carrier is still a work
    # item Support owns: it can be reassigned, chased, progressed when
    # the answer comes back, rejected, or cancelled. The action set
    # therefore matches `IN_PROGRESS` -- what changes is who everyone is
    # waiting for, not what Support may do about it.
    SupportWorkItemStatus.EXTERNAL_PARTY_REVIEW: frozenset(
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
        self._outbox = database[INTEGRATION_OUTBOX_COLLECTION]
        self._config = configuration
        self._repository = operational_repository

    async def ensure_indexes(self) -> None:
        # Partial, where it used to be plainly unique. The rule -- one thread per
        # session -- only applies to a thread that belongs to a session, and a
        # case thread has none. Left unconditional, every case thread indexed as
        # `{sessionId: null}` and the second one collided with the first.
        await self._work_items.create_index(
            "sessionId",
            unique=True,
            partialFilterExpression={"sessionId": {"$type": "string"}},
        )
        # The same rule for the case-scoped half: one Channel B thread per case.
        # There is one Returns Support conversation per return, and opening a
        # second would split the exchange a human is reading in two.
        await self._work_items.create_index(
            "caseId",
            unique=True,
            partialFilterExpression={"caseId": {"$type": "string"}},
        )
        await self._work_items.create_index("idempotencyKey", unique=True)
        await self._work_items.create_index(
            [("status", ASCENDING), ("priorityRank", ASCENDING), ("slaDueAt", ASCENDING)]
        )
        await self._work_items.create_index([("assignedTo", ASCENDING), ("status", ASCENDING)])
        await self._messages.create_index(
            [("threadId", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
        await self._messages.create_index([("threadId", ASCENDING), ("createdAt", ASCENDING)])
        # Receiver dedupe (contracts.md sect. 7). The sender's delivery identity
        # is carried on the message and made unique *here*, on B, because that
        # is the only place the guarantee can actually be kept: the sender can
        # retry a send it never learned the outcome of, and no amount of care on
        # the sending side turns "I did not hear back" into "it did not arrive".
        # Effectively-once on the wire becomes exactly-once on the desk, and the
        # observable form of the acceptance is this index: one message on B.
        #
        # Partial on the field's presence, for the reason the `sessionId` index
        # above gives -- every message without a delivery id would otherwise
        # index as `null` and the second one would collide with the first.
        await self._messages.create_index(
            "businessPayload.deliveryId",
            unique=True,
            partialFilterExpression={"businessPayload.deliveryId": {"$type": "string"}},
            name=SUPPORT_MESSAGE_DELIVERY_INDEX,
        )
        # The integration outbox is deliberately absent. This service writes
        # commands to it but owns none of the fields it was indexing, and its
        # copy of the definition had drifted from the other two: it alone built
        # `(createdAt DESC)` and never built `leaseUntil`.
        #
        # `operations.integrations.outbox.ensure_integration_outbox_indexes` now
        # owns all five, and `OperationalRepository.ensure_indexes` -- which runs
        # immediately before this method in the API lifespan and inside the
        # orchestrator -- builds them. Removing the copy is also what stops the
        # API process from building the outbox indexes twice on every boot.
        #
        # `(createdAt DESC)` was not lost with it: it is in the union, which is
        # the only reason this deletion is safe. Deleting it before the union
        # existed would have turned the operator listing in
        # `api/integration_outbox.py` into a collection scan and an in-memory
        # sort.

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
        document = _work_item_document(
            item_id=item_id,
            thread_id=thread_id,
            now=now,
            sla_due_at=now + timedelta(minutes=sla_minutes),
            idempotency_key=request.idempotencyKey,
            created_by=actor_id,
            subject=request.subject,
            request_snapshot_digest=request.requestSnapshotDigest,
            priority=request.priority,
            scope={"sessionId": request.sessionId},
        )
        message = _opening_message(
            thread_id=thread_id,
            now=now,
            sender_role="ASSOCIATE",
            sender_id=actor_id,
            text=request.supportDraft,
            business_payload={"requestSnapshotDigest": request.requestSnapshotDigest},
        )
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

    async def get_work_item_for_session(self, session_id: str) -> SupportWorkItemView | None:
        """The one work item for a return, if support has been involved.

        Singular because `sessionId` is uniquely indexed on this collection --
        a return has at most one support work item, by construction. That index
        already existed; what was missing was any way to use it in this
        direction, which is why "read a return's support" had no implementation
        even though the data supported it.
        """
        document = await self._work_items.find_one({"sessionId": session_id})
        return None if document is None else self._work_item_view(cast(dict[str, Any], document))

    async def open_case_thread(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        support_draft: str,
        idempotency_key: str,
        business_payload: Mapping[str, Any] | None = None,
        subject: str | None = None,
        work_item_id: str | None = None,
        queue: str | None = None,
        sla_due_at: datetime | None = None,
    ) -> str:
        """Open Channel B for a case, once, and return the work-item id.

        `business_payload` is the structured half of the handoff -- the same
        facts the message text states, as data. It is persisted on the opening
        message beside the prose so a screen reads business fields from it and
        never by parsing the text back into fields, which is a display that
        breaks the day the wording changes. `caseId` is always present in it;
        a caller supplying one cannot remove that.

        `subject` is the line the queue draws for this return, composed by the
        caller from the same facts as the message. It is the caller's because
        this service has no idea what the return is about -- it is handed a
        finished message and an id. Absent, the fallback below stands: it names
        the case and nothing else, which is what every row in the queue used to
        say, and is why the composed subject exists.

        `work_item_id` lets the caller decide the id. The workflow mints one so
        that the activity which *composes* the request can name the work item the
        activity which *opens* it will create -- two activities, one id, and a
        replay-stable `workflow.uuid4()` behind it. `None` keeps the previous
        behaviour of minting one here.

        `queue` is how a warranty or delivery-claim verification reaches the team
        that verifies it. It is optional and defaults to the returns queue, so
        the ordinary path is unchanged; route context travels as a queue and
        never as a work-item type field (plan sect. 7.6).

        `sla_due_at` is a deadline **somebody else computed**, and today the only
        caller that supplies one is the delivery-claim path: the reporting
        window is `delivery_claim.reporting_window` business days after
        delivery, decided by the policy evaluation that routed the case and
        carried here rather than recomputed. Recomputing it would need this
        service to resolve a business calendar it has no business resolving, and
        would produce a second answer to a question already answered -- the
        failure `PolicyOutcome.delivery_claim_window` names in as many words.

        `None` means "no deadline but yours", and the generic acknowledgement
        SLA stands. That is the ordinary case for every standard return, and it
        is also the honest fallback for a delivery claim whose window is
        `UNDETERMINED`; the caller distinguishes the two by recording a
        `SupportSlaBasis`, because the fallback and a computed deadline are
        otherwise the same field with the same shape.

        The case-scoped counterpart to `create_work_item`, which is keyed to a
        return *session*. A case has no session -- it is the thing sessions hang
        off -- so this writes `caseId` and leaves `sessionId` unset.

        Idempotent twice over: on `idempotencyKey`, which the workflow derives
        from the case so a Temporal retry re-reads rather than re-opens, and on
        the unique `caseId` index, which catches the race the key check cannot.
        There is one Returns Support conversation per return; a second would
        split the exchange a human is reading in two.

        **The two inserts share one transaction, and the reason is durability
        rather than the revision** (D28). This writes no case field: the link
        that puts the thread on the projection is `channelBWorkItemId`, set by
        `open_support_work_item`'s own `update_case` after this returns, and that
        writer bumps. So `CaseDetail` cannot observe a torn state here -- until
        the case is linked, `_load_support_work_item` reads nothing at all, which
        is why there is no `bump_case_revision` call below and adding one would
        invalidate every cached projection for a document no client can yet see.

        What the transaction fixes is worse than a stale cache and permanent.
        The work item was inserted first and the opening message second; a
        failure between them left a thread whose request text never existed, and
        the idempotency check at the top then returns that thread forever, so no
        retry ever writes the missing message. A human opens the conversation
        Support is meant to answer and finds it empty. Both inserts now commit or
        neither does, and a retry re-opens the thread properly.

        `with_transaction` re-runs the callback on a transient abort; both
        documents are built above with fixed ids and the failed attempt's writes
        are rolled back first, so the re-run is an identical pair of inserts
        rather than a duplicate. A `DuplicateKeyError` is *not* transient -- it
        aborts and surfaces here, where it means the race the unique index
        exists to catch, and the winner's thread is read back.
        """
        if sla_due_at is not None and sla_due_at.utcoffset() is None:
            raise ValueError(
                "a supplied slaDueAt must be timezone-aware: stored naive beside the aware "
                "instants every other writer produces, it sorts and compares as if it were UTC "
                "whatever zone it was meant in"
            )
        existing = await self._work_items.find_one(
            {"$or": [{"idempotencyKey": idempotency_key}, {"caseId": case_id}]}
        )
        if existing is not None:
            return str(existing["_id"])

        now = _now()
        item_id = work_item_id or str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        sla_minutes = self._config.workflow.sla_minutes["support_acknowledgement"]
        document = _work_item_document(
            item_id=item_id,
            thread_id=thread_id,
            now=now,
            # The caller's deadline wins where there is one; the desk's own SLA
            # is the fallback and not the default, which is the difference D2
            # closed. A tz-aware instant is required: a naive one stored beside
            # aware ones compares wrong in every query that sorts on this field.
            sla_due_at=(
                sla_due_at if sla_due_at is not None else now + timedelta(minutes=sla_minutes)
            ),
            idempotency_key=idempotency_key,
            created_by=principal_id,
            subject=(subject or "").strip() or f"Return request for case {case_id}",
            request_snapshot_digest=_digest({"caseId": case_id}),
            queue=queue or "RETURNS_SUPPORT",
            scope={"caseId": case_id, "tenantId": tenant_id, "sessionId": None},
        )
        message = _opening_message(
            thread_id=thread_id,
            now=now,
            sender_role="AGENT",
            sender_id="return-workflow-agent",
            text=support_draft,
            # The case id is written last so a caller cannot displace it: it is
            # the link every reader of this message resolves the case through.
            business_payload={**dict(business_payload or {}), "caseId": case_id},
        )

        async def _open(mongo_session: AsyncClientSession) -> None:
            await self._work_items.insert_one(document, session=mongo_session)
            await self._messages.insert_one(message, session=mongo_session)

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(_open)
        except DuplicateKeyError:
            # Lost the race on `caseId` or `idempotencyKey`. The winner's thread
            # is the one thread this case gets.
            winner = await self._work_items.find_one(
                {"$or": [{"idempotencyKey": idempotency_key}, {"caseId": case_id}]}
            )
            if winner is None:  # pragma: no cover - duplicate on neither key
                raise
            return str(winner["_id"])
        return item_id

    async def ensure_case_support_thread(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        support_draft: str,
        idempotency_key: str,
        business_payload: Mapping[str, Any] | None = None,
        subject: str | None = None,
        work_item_id: str | None = None,
        queue: str | None = None,
        sla_due_at: datetime | None = None,
    ) -> CaseSupportThread:
        """The case's one support thread, opened if it is not there yet.

        Contracts.md sect. 7 names this as an operation in its own right, and
        it is deliberately not a second implementation: `open_case_thread`
        already has exactly the shape the contract asks for -- idempotent on
        the unique `caseId` constraint, loser re-reads the winner's thread --
        and a parallel opener would be a second answer to "which thread is this
        case's". This adds what the callers of the delivery path need and the
        original signature does not give them: the `thread_id` to post onto,
        and whether the thread was opened here or was already standing.

        `created` is not a formality. It is how a caller distinguishes "I
        started this conversation" from "I joined one already in progress",
        which is the difference between an opening request and a reply, and it
        can only be known by whoever won the insert.
        """
        already = await self._work_items.find_one({"caseId": case_id})
        item_id = await self.open_case_thread(
            case_id=case_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            support_draft=support_draft,
            idempotency_key=idempotency_key,
            business_payload=business_payload,
            subject=subject,
            work_item_id=work_item_id,
            queue=queue,
            sla_due_at=sla_due_at,
        )
        item = await self._work_items.find_one({"_id": item_id})
        if item is None:  # pragma: no cover - the insert above just committed it
            raise KeyError(item_id)
        return CaseSupportThread(
            workItemId=item_id,
            threadId=str(item["threadId"]),
            created=already is None,
        )

    async def post_support_message(
        self,
        *,
        work_item_id: str,
        message_text: str,
        delivery_id: str | None = None,
        message_type: SupportMessageType = SupportMessageType.COMMENT,
        business_payload: Mapping[str, Any] | None = None,
        attachment_ids: Collection[str] = (),
        actor_id: str = "return-workflow-agent",
        actor_role: str = "AGENT",
    ) -> SupportMessagePost:
        """Post one message to a thread, deduped on its delivery identity.

        Kind-agnostic on purpose (contracts.md sect. 7): an approved template,
        an approved reply and a relayed clarification are the same act as far
        as the receiver is concerned -- a message arriving on the case thread
        with a delivery id -- and giving each its own posting path would give
        each its own chance to get the dedupe wrong. One path, deduped once.

        `delivery_id` is checked twice, and both checks are load-bearing. The
        read first, because a redelivery is the *expected* case on a retry and
        an absorbed send should cost one query rather than a rolled-back write.
        The unique index second, because the read cannot see a concurrent
        writer -- and two workflow workers replaying the same send at the same
        instant is precisely the race the identity exists for.

        Called without a `delivery_id` this is an ordinary undeduped post; the
        contract's guarantee is a property of the identity, not of the method,
        and pretending otherwise would let a caller believe in a protection it
        never asked for.
        """
        if delivery_id is not None:
            absorbed = await self._messages.find_one({"businessPayload.deliveryId": delivery_id})
            if absorbed is not None:
                return self._absorbed_post(work_item_id, absorbed, delivery_id)

        payload: dict[str, Any] = dict(business_payload or {})
        if delivery_id is not None:
            # Written last so a caller cannot displace it, the same rule
            # `open_case_thread` applies to `caseId`: this is the field the
            # uniqueness is enforced on, and a payload that overwrote it would
            # quietly opt out of the guarantee.
            payload["deliveryId"] = delivery_id

        item = await self._work_items.find_one({"_id": work_item_id})
        if item is None:
            raise KeyError(work_item_id)
        version_raw = item["version"]
        if not isinstance(version_raw, (int, float)):
            raise TypeError(f"Expected numeric version, got {type(version_raw).__name__}")

        try:
            _, message = await self.add_message(
                work_item_id,
                CreateSupportMessageRequest(
                    messageType=message_type,
                    messageText=message_text,
                    attachmentIds=tuple(attachment_ids),
                    businessPayload=payload,
                    expectedVersion=int(version_raw),
                ),
                actor_id=actor_id,
                actor_role=actor_role,
            )
        except DuplicateKeyError:
            # The concurrent writer won on `businessPayload.deliveryId`. Their
            # message is the delivery; this one never existed. (The work item's
            # version moved before the insert failed, so a sequence number is
            # spent -- a gap in a display counter, against a duplicate message
            # in a person's queue. The trade is not close.)
            if delivery_id is None:  # pragma: no cover - no other unique key applies
                raise
            winner = await self._messages.find_one({"businessPayload.deliveryId": delivery_id})
            if winner is None:  # pragma: no cover - duplicate on no known key
                raise
            return self._absorbed_post(work_item_id, winner, delivery_id)

        return SupportMessagePost(
            workItemId=work_item_id,
            threadId=message.threadId,
            messageId=message.id,
            sequence=message.sequence,
            deliveryId=delivery_id,
            absorbed=False,
        )

    @staticmethod
    def _absorbed_post(
        work_item_id: str, message: Mapping[str, Any], delivery_id: str
    ) -> SupportMessagePost:
        return SupportMessagePost(
            workItemId=work_item_id,
            threadId=str(message["threadId"]),
            messageId=str(message["_id"]),
            sequence=int(cast(int, message["sequence"])),
            deliveryId=delivery_id,
            absorbed=True,
        )

    async def post_reminder(
        self,
        *,
        work_item_id: str,
        reminder_number: int,
        max_reminders: int,
        idempotency_key: str,
    ) -> None:
        """A polite nudge on the existing thread.

        Support knows it is talking to an agent, and the tone is still warm:
        someone is reading this between other work, and a terse machine-stamped
        line reads as pressure rather than a question.

        Numbered and deduplicated on `businessPayload.reminderKey`, because the
        cost of getting this wrong is not a duplicate row -- it is the same
        message arriving twice in a person's queue.
        """
        already_sent = await self._messages.find_one(
            {"businessPayload.reminderKey": idempotency_key}
        )
        if already_sent is not None:
            return

        item = await self._work_items.find_one({"_id": work_item_id})
        if item is None:
            raise KeyError(work_item_id)
        # Stored documents are `dict[str, object]`; narrow rather than cast, the
        # same way `add_message` narrows `lastMessageSequence`.
        version_raw = item["version"]
        if not isinstance(version_raw, (int, float)):
            raise TypeError(f"Expected numeric version, got {type(version_raw).__name__}")

        remaining = max(0, max_reminders - reminder_number)
        closing = (
            " No rush if it's in hand -- just let me know either way."
            if remaining
            else " If this one isn't the right queue, could you point me at who to ask?"
        )
        await self.add_message(
            work_item_id,
            CreateSupportMessageRequest(
                messageType=SupportMessageType.COMMENT,
                messageText=(
                    "Hi -- just checking in on this return request."
                    " Is there anything you need from me to move it along?" + closing
                ),
                businessPayload={
                    "reminderKey": idempotency_key,
                    "reminderNumber": reminder_number,
                },
                expectedVersion=int(version_raw),
            ),
            actor_id="return-workflow-agent",
            actor_role="AGENT",
        )

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
        if action not in _ALLOWED_ACTIONS[status]:
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
        updated = await self._commit_action(work_item_id, request, status, updates, when=now)
        projected_session = updated.get("sessionId")
        if not isinstance(projected_session, str) or not projected_session:
            # A case thread. It has no return session to project onto and no
            # session event log to append to -- `open_case_thread` writes
            # `sessionId: None` deliberately, because a case is the thing
            # sessions hang off. The projection this action changed is the
            # case's, and the write above has already moved its revision.
            return self._work_item_view(updated)
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
        await self._repository.update_return(projected_session, return_updates)
        await self._repository.append_event(
            projected_session,
            event_type=f"SUPPORT_{action.value}",
            actor_type="USER",
            actor_id=actor_id,
            payload={
                "workItemId": work_item_id,
                "status": updated["status"],
                "reasonDigest": _digest(request.reason),
            },
        )
        return self._work_item_view(updated)

    async def _commit_action(
        self,
        work_item_id: str,
        request: SupportActionRequest,
        status: SupportWorkItemStatus,
        updates: dict[str, Any],
        *,
        when: datetime,
    ) -> dict[str, Any]:
        """Apply the action to the work item, and the case revision with it.

        > Any write that can change the `CaseDetail` projection must, in the
        > same transaction, bump `case.revision` and set `case.updatedAt`.
        > (plan sect. 6.5)

        This one changes it. `SupportProjection` reads `status`, `assignedTo`
        and `completedAt` (as `resolvedAt`) straight off this document, and
        every branch above sets at least one of them -- so a Support reply that
        left the revision untouched is the precise case the invariant exists to
        prevent: the projection moves, the client's `revision` comparison says
        nothing changed, and the screen never re-renders.

        The mechanism is the repository's `bump_case_revision` -- a blind `$inc`
        with `session` a required keyword -- inside `with_transaction`, which is
        the shape `case_repository.py`'s four writers use and the shape
        `create_work_item` above already opens a session with. Not a second,
        best-effort `update_one` afterwards: a bump that can fail after the work
        item commits produces a case that is resolved on the server and
        unresolved on every client that trusts the revision.

        `with_transaction` retries a transient abort by re-running the callback,
        so the compare-and-set is re-issued rather than surfaced as a conflict
        on a case nobody edited. That is safe because the callback is
        idempotent-by-refusal: the second attempt matches on the same
        `(version, status)` pair, and if a real writer moved the item in between
        it matches nothing and raises, which is the correct answer.

        **A work item with no case bumps nothing.** The session-scoped half of
        this collection is on no case projection, and there would be no case id
        to name.

        **A refused action bumps nothing.** The `None` result raises from inside
        the transaction, so the bump rolls back with it -- a caller that lost
        the compare-and-set changed nothing and must move no revision.
        """

        async def _write(mongo_session: AsyncClientSession) -> dict[str, Any]:
            document = await self._work_items.find_one_and_update(
                {"_id": work_item_id, "version": request.expectedVersion, "status": status.value},
                {"$set": updates, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
                session=mongo_session,
            )
            if document is None:
                raise ConcurrencyConflictError(work_item_id)
            case_id = document.get("caseId")
            if isinstance(case_id, str) and case_id:
                await self._repository.bump_case_revision(case_id, session=mongo_session, when=when)
            return cast(dict[str, Any], document)

        async with self._client.start_session() as mongo_session:
            return cast(dict[str, Any], await mongo_session.with_transaction(_write))
