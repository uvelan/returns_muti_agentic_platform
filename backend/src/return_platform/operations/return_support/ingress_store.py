"""The commit an inbound support message is (contracts.md sect. 5, sect. 7).

`DurableSupportEventStore` already answers this question for the structured
door: one transaction writes the event and the outbox command that delivers it,
and the handler returns the moment that commits. This is that mechanism
extended to the natural-language door, which has three things the structured one
does not.

**A raw body to keep.** The message as it arrived is evidence. The classifier's
answer is an interpretation of it, lives under S2's analysis record, and can be
re-derived; the words cannot. They are written in the same transaction as the
event so there is never an event whose source text is missing.

**A causation chain to fill.** Sect. 7 says the ordering fields are populated
*only by the enqueuing store* -- so they are populated here, and acceptance 18's
ordered drain of the inbound stream is real only because of it. Each event's
`required_predecessor_ids` names the previous inbound event on the same case, so
the stream is a chain rather than a set of independently-dispatchable commands
that happen to have increasing sequence numbers. A dispatcher can only start
message N when N-1 has completed, which is what "in order" has to mean when the
worker pool has more than one worker.

**A door that may be shut.** `nl_enabled=false` parks (contracts.md sect. 5):
the message is persisted with status `PARKED`, no classify command is enqueued,
operations is told once per window rather than once per message, and a case that
parks past its quota escalates. Parking is never a 409 -- refusing the message
would put the operator's switch in the transport's error budget. When the switch
flips, the parked messages enter the chain **before** any new one: the drain
runs on the accept path, so a message arriving after the flip chains behind the
backlog rather than in front of it.

`DurableSupportIngressStore` subclasses `DurableSupportEventStore` rather than
copying it. The digest semantics, the "same id + same payload is a no-op, same
id + different payload is a 409" rule and the outbox document shape are the same
rules; a second implementation of them is a second place for them to drift.

S1's binding module and S2's stores are consumed here, never modified.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings
from return_platform.configuration.support_ingress_configuration import (
    SupportIngressConfiguration,
)
from return_platform.operations.integrations.outbox import (
    CaseStream,
    ordered_command_fields,
)
from return_platform.operations.return_support.ingress import (
    NormalizedSupportEvent,
    SupportEventStatus,
)
from return_platform.operations.support_events import (
    SUPPORT_EVENT_AGGREGATE_TYPE,
    DurableSupportEventStore,
    IdempotencyConflictError,
    canonical_payload_digest,
)

logger = logging.getLogger("return_platform.support_ingress")

#: Inbound messages and their normalized events. Its own collection rather than
#: rows in `case_support_events`: the structured store's identity is
#: `(caseId, supportEventId)` and this one's is the contract's three-part
#: `(caseId, transportId, externalMessageId)`, and one collection cannot carry
#: two identities without one of them being advisory.
CASE_SUPPORT_INBOUND: Final = "case_support_inbound_messages"

#: Named so a migration can find it and a test can assert it by name.
INBOUND_IDENTITY_INDEX: Final = "case_support_inbound_identity_unique"
INBOUND_EVENT_INDEX: Final = "case_support_inbound_event_unique"

#: Where the parking alert's dedupe state lives. One document per case; the
#: window is a timestamp on it, not a timer in a process, so a restart does not
#: reset the operator's inbox.
SUPPORT_PARKING_ALERTS: Final = "support_parking_alerts"

_INTEGRATION_OUTBOX: Final = "integration_outbox"

#: The classify topic (contracts.md sect. 7's new outbox topics). One topic, one
#: dispatcher: a stored document can never name what runs on it.
SUPPORT_MESSAGE_CLASSIFY_TOPIC: Final = "return-case.support-message.classify"


class ParkingQuotaExceeded(RuntimeError):
    """A case has parked more messages than its released quota allows.

    Not raised at the caller -- the message is still persisted, because losing
    it is strictly worse than holding one too many. Recorded on the receipt and
    escalated, so "this case is accumulating unread support traffic" reaches a
    person as a fact rather than as a number nobody queries.
    """


@dataclass(frozen=True, slots=True)
class SupportIngressReceipt:
    """What the caller is told once the write has committed.

    No `classified` field, and no `delivered` one, for the reason
    `SupportEventReceipt` gives: nothing here knows what the dispatcher has
    done, and a field that looked like it did would be the dual-write claim in
    a different shape.
    """

    case_id: str
    support_event_id: str
    status: str
    payload_digest: str
    #: `None` when the message parked: there is nothing to classify yet.
    outbox_command_id: str | None
    #: True when this exact message was already on file. The caller returns the
    #: same success it returned the first time.
    duplicate: bool
    #: Parked messages on this case after this write. Feeds the degraded panel
    #: entry (contracts.md sect. 5).
    parked_count: int = 0
    quota_exceeded: bool = False


async def ensure_support_ingress_indexes(database: Any) -> None:
    """The two constraints that make an inbound message identifiable.

    The three-part identity is the contract's dedupe key. The event-id index is
    a second constraint saying the same thing from the derived side: the id is
    a pure function of the identity, so a document that satisfies one and
    violates the other would mean the derivation had changed under a live
    collection -- which is a thing worth failing on rather than absorbing.
    """
    collection = database[CASE_SUPPORT_INBOUND]
    await collection.create_index(
        [
            ("caseId", ASCENDING),
            ("transportId", ASCENDING),
            ("externalMessageId", ASCENDING),
        ],
        unique=True,
        name=INBOUND_IDENTITY_INDEX,
    )
    await collection.create_index("supportEventId", unique=True, name=INBOUND_EVENT_INDEX)
    await collection.create_index([("caseId", ASCENDING), ("recordedAt", ASCENDING)])
    await database[SUPPORT_PARKING_ALERTS].create_index("caseId")


class DurableSupportIngressStore(DurableSupportEventStore):
    """Persist an inbound message, its event and its classify command as one act."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        configuration: SupportIngressConfiguration,
    ) -> None:
        super().__init__(client, settings)
        self._client = client
        self._database = client[settings.mongo_database]
        self._inbound = self._database[CASE_SUPPORT_INBOUND]
        self._outbox_collection = self._database[_INTEGRATION_OUTBOX]
        self._parking_alerts = self._database[SUPPORT_PARKING_ALERTS]
        self._configuration = configuration

    async def ensure_indexes(self) -> None:
        await super().ensure_indexes()
        await ensure_support_ingress_indexes(self._database)

    @staticmethod
    def classify_idempotency_key(case_id: str, support_event_id: str) -> str:
        """The classify command's own unique key, derived from the event.

        One classify command per event, enforced by the database rather than by
        the drain being careful. The dispatcher is at-least-once; a second
        *command* would be a second analysis, which is the thing sect. 5's
        record exists to make impossible.
        """
        return f"support-message-classify:{case_id}:{support_event_id}"

    # ------------------------------------------------------------------ reads

    async def get_inbound(self, *, support_event_id: str) -> dict[str, Any] | None:
        document = await self._inbound.find_one({"supportEventId": support_event_id})
        return dict(document) if document is not None else None

    async def list_inbound(self, case_id: str) -> list[dict[str, Any]]:
        cursor = self._inbound.find({"caseId": case_id}).sort("recordedAt", ASCENDING)
        return [dict(document) async for document in cursor]

    async def list_parked(self, case_id: str) -> list[dict[str, Any]]:
        """Parked messages, oldest first. Stream order is arrival order here.

        Sorted by `recordedAt` and then `_id`: two messages committed in the
        same millisecond must still have a total order, or "reprocess in stream
        order" is a claim the drain cannot keep.
        """
        cursor = self._inbound.find({"caseId": case_id, "status": SupportEventStatus.PARKED}).sort(
            [("recordedAt", ASCENDING), ("_id", ASCENDING)]
        )
        return [dict(document) async for document in cursor]

    async def parked_count(self, case_id: str) -> int:
        return await self._inbound.count_documents(
            {"caseId": case_id, "status": SupportEventStatus.PARKED}
        )

    async def recent_messages_in_window(self, case_id: str, *, since: datetime) -> int:
        """The rate-limit read. Per case, per window (contracts.md sect. 5)."""
        return await self._inbound.count_documents(
            {"caseId": case_id, "recordedAt": {"$gte": since}}
        )

    # ------------------------------------------------------------ the commit

    async def record_inbound_message(
        self,
        *,
        event: NormalizedSupportEvent,
        workflow_id: str,
        actor_id: str,
        nl_enabled: bool,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> SupportIngressReceipt:
        """Persist one inbound message, and queue its classification or park it.

        Returns rather than raises for a repeat: a transport redelivering is
        ordinary, and the second delivery must look exactly like the first.
        The only refusal is the contract's -- the same identity carrying a
        different body, which raises `IdempotencyConflictError` for the handler
        to turn into a 409.

        When the door is open, the *backlog drains first*. Parked messages are
        enqueued ahead of this one, so the chain this message joins already has
        them in it. Doing that here rather than in a separate operator action is
        what makes "reprocesses parked messages in stream order before new ones"
        true without anybody having to remember to run something.
        """
        stamp = now or datetime.now(UTC)
        payload = event.canonical_business_form()
        payload["bodyText"] = event.body_text
        digest = canonical_payload_digest(payload)

        existing = await self._inbound.find_one(
            {
                "caseId": event.case_id,
                "transportId": event.transport_id,
                "externalMessageId": event.external_message_id,
            }
        )
        if existing is not None:
            return await self._receipt_for_existing(existing, event, digest)

        if nl_enabled:
            # Ahead of this message's own commit, so the predecessor this
            # message names is the last of the backlog rather than whatever
            # preceded the outage.
            await self.drain_parked(
                case_id=event.case_id, workflow_id=workflow_id, actor_id=actor_id
            )

        status = SupportEventStatus.ACCEPTED if nl_enabled else SupportEventStatus.PARKED
        command_id = str(uuid.uuid4()) if nl_enabled else None
        document: dict[str, Any] = {
            "_id": event.support_event_id,
            "supportEventId": event.support_event_id,
            "caseId": event.case_id,
            "workItemId": event.work_item_id,
            "workflowId": workflow_id,
            "transportId": event.transport_id,
            "externalMessageId": event.external_message_id,
            "status": status,
            "payloadDigest": digest,
            # The message as it arrived. Never overwritten by an analysis.
            "rawBody": event.body_text,
            "sender": event.sender.as_document(),
            "transportMetadata": dict(event.transport_metadata),
            "normalizedEvent": payload,
            "outboxCommandId": command_id,
            "actorId": actor_id,
            "correlationId": correlation_id,
            "recordedAt": stamp,
            "parkedAt": None if nl_enabled else stamp,
            "releasedAt": None,
        }

        if not nl_enabled:
            try:
                await self._inbound.insert_one(dict(document))
            except DuplicateKeyError:
                winner = await self._inbound.find_one({"supportEventId": event.support_event_id})
                if winner is None:  # pragma: no cover - duplicate on no known key
                    raise
                return await self._receipt_for_existing(winner, event, digest)
            count = await self.parked_count(event.case_id)
            exceeded = count > self._configuration.parking.per_case_quota
            await self._alert_parked(event, count=count, exceeded=exceeded, now=stamp)
            return SupportIngressReceipt(
                case_id=event.case_id,
                support_event_id=event.support_event_id,
                status=SupportEventStatus.PARKED,
                payload_digest=digest,
                outbox_command_id=None,
                duplicate=False,
                parked_count=count,
                quota_exceeded=exceeded,
            )

        async def transaction(mongo_session: Any) -> None:
            # Inside the transaction, and passed the session -- both halves
            # matter, and the second is what makes the first mean anything.
            #
            # Resolving the chain tail outside the transaction is a fork
            # waiting for two messages to arrive together on one case: both
            # reads see the same tail, both name it as predecessor, and the two
            # events end up at the same depth with nothing ordering them
            # against each other. Sequence numbers stay distinct -- the `$inc`
            # is server-side and correct -- which is exactly why the defect is
            # invisible to an enqueue-order assertion.
            #
            # In here, the `$inc` on the per-`(case, stream)` counter is part of
            # this transaction. Two concurrent enqueues therefore conflict on
            # that one document, one aborts, and `with_transaction` re-runs it
            # -- re-reading a tail that now includes the winner. The chain is a
            # chain because the counter write serialises the tail read, not
            # because the callers happened not to overlap.
            command = await self._classify_command(
                event=event,
                command_id=str(command_id),
                workflow_id=workflow_id,
                now=stamp,
                session=mongo_session,
            )
            await self._inbound.insert_one(dict(document), session=mongo_session)
            await self._outbox_collection.insert_one(dict(command), session=mongo_session)

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(transaction)
        except DuplicateKeyError:
            winner = await self._inbound.find_one({"supportEventId": event.support_event_id})
            if winner is None:
                # The collision was on the outbox key, not the message: a
                # classify command exists for an event that does not. Re-raised
                # rather than papered over -- the transaction rolled back, so
                # nothing partial survives, and a caller told "recorded" for a
                # write that did not happen is the failure this module removes.
                raise
            return await self._receipt_for_existing(winner, event, digest)

        return SupportIngressReceipt(
            case_id=event.case_id,
            support_event_id=event.support_event_id,
            status=SupportEventStatus.ACCEPTED,
            payload_digest=digest,
            outbox_command_id=str(command_id),
            duplicate=False,
            parked_count=await self.parked_count(event.case_id),
        )

    # ------------------------------------------------------------- the drain

    async def drain_parked(self, *, case_id: str, workflow_id: str, actor_id: str) -> list[str]:
        """Release parked messages into the inbound chain, oldest first.

        Returns the event ids released, in the order they were chained.
        Check-then-act and safely re-runnable: a message already `ACCEPTED` is
        not in `list_parked`, and the classify command's idempotency key would
        refuse a second enqueue anyway.

        The chaining is the point. Each released message names the previously
        enqueued inbound event as its predecessor, so the drain does not merely
        enqueue them in order -- it makes the *dispatcher* unable to run them
        out of order, whatever the worker pool does.
        """
        del actor_id  # the release is not an actor's decision; the switch is
        released: list[str] = []
        for parked in await self.list_parked(case_id):
            support_event_id = str(parked["supportEventId"])
            command_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            work_item_id = str(parked.get("workItemId") or "")

            async def transaction(
                mongo_session: Any,
                _event_id: str = support_event_id,
                _command_id: str = command_id,
                _work_item_id: str = work_item_id,
                _now: datetime = now,
            ) -> None:
                # Inside the transaction for the same reason as the accept
                # path: two drains racing the same backlog would otherwise both
                # read the same tail and fork it, which is the failure mode
                # "reprocess in stream order" exists to rule out.
                _command = await self._classify_command_fields(
                    case_id=case_id,
                    support_event_id=_event_id,
                    work_item_id=_work_item_id,
                    command_id=_command_id,
                    workflow_id=workflow_id,
                    now=_now,
                    session=mongo_session,
                )
                await self._outbox_collection.insert_one(dict(_command), session=mongo_session)
                await self._inbound.update_one(
                    {"supportEventId": _event_id},
                    {
                        "$set": {
                            "status": SupportEventStatus.ACCEPTED,
                            "outboxCommandId": _command_id,
                            "releasedAt": _now,
                        }
                    },
                    session=mongo_session,
                )

            try:
                async with self._client.start_session() as mongo_session:
                    await mongo_session.with_transaction(transaction)
            except DuplicateKeyError:
                # A concurrent drain already released this one. Theirs is the
                # release; carry on down the backlog rather than stopping, or a
                # single race would leave the rest parked forever.
                continue
            released.append(support_event_id)
        return released

    # --------------------------------------------------------- the internals

    async def _classify_command(
        self,
        *,
        event: NormalizedSupportEvent,
        command_id: str,
        workflow_id: str,
        now: datetime,
        session: Any,
    ) -> dict[str, Any]:
        return await self._classify_command_fields(
            case_id=event.case_id,
            support_event_id=event.support_event_id,
            work_item_id=event.work_item_id,
            command_id=command_id,
            workflow_id=workflow_id,
            now=now,
            session=session,
        )

    async def _classify_command_fields(
        self,
        *,
        case_id: str,
        support_event_id: str,
        work_item_id: str,
        command_id: str,
        workflow_id: str,
        now: datetime,
        session: Any,
    ) -> dict[str, Any]:
        """One classify command, with its ordering fields filled in.

        `causation_id` is the previous inbound event; so is the single entry in
        `required_predecessor_ids`. They are the same id here and that is not a
        redundancy: causation says *why this exists* and the predecessor list
        says *what must finish first*, and they diverge as soon as an event is
        caused by something on another stream. Filling both from the chain now
        is what makes acceptance 18 hold rather than merely be claimed.

        **`session` is required, not optional.** Every caller is inside a
        transaction, and a default of `None` would let a future one call this
        outside one and get a chain that forks under concurrent arrival --
        silently, because the sequence numbers would still be distinct and only
        the *predecessor* would be wrong. Making the parameter mandatory is how
        that stops being a thing anyone has to remember.
        """
        predecessor = await self._last_enqueued_inbound_event(case_id, session=session)
        fields = await ordered_command_fields(
            self._database,
            case_id=case_id,
            stream=CaseStream.INBOUND,
            event_id=support_event_id,
            causation_id=predecessor,
            required_predecessor_ids=() if predecessor is None else (predecessor,),
            session=session,
        )
        return {
            "_id": command_id,
            "topic": SUPPORT_MESSAGE_CLASSIFY_TOPIC,
            "aggregateType": SUPPORT_EVENT_AGGREGATE_TYPE,
            "aggregateId": case_id,
            "idempotencyKey": self.classify_idempotency_key(case_id, support_event_id),
            "payload": {
                "caseId": case_id,
                "workflowId": workflow_id,
                "workItemId": work_item_id,
                "supportEventId": support_event_id,
            },
            "status": "PENDING",
            "attemptCount": 0,
            "nextAttemptAt": now,
            "createdAt": now,
            "updatedAt": now,
            **fields,
        }

    async def _last_enqueued_inbound_event(self, case_id: str, *, session: Any) -> str | None:
        """The tail of this case's inbound chain, or `None` for the first link.

        Read from the *outbox* rather than from the message collection, because
        the chain is over enqueued events: a parked message has no event on the
        stream yet, and naming one as a predecessor would be
        `UnknownPredecessorError` at best and a permanently-held dependent at
        worst.

        Read *in the caller's transaction*, so this is the tail as of the same
        point the sequence is allocated. Outside one it is a read that a
        concurrent commit invalidates the instant it returns.
        """
        latest = await self._outbox_collection.find_one(
            {"aggregateId": case_id, "stream": CaseStream.INBOUND.value},
            sort=[("streamSequence", -1)],
            session=session,
        )
        if latest is None:
            return None
        event_id = latest.get("eventId")
        return str(event_id) if event_id else None

    async def _receipt_for_existing(
        self,
        stored: Mapping[str, Any],
        event: NormalizedSupportEvent,
        digest: str,
    ) -> SupportIngressReceipt:
        if str(stored.get("payloadDigest")) != digest:
            raise IdempotencyConflictError(event.case_id, event.support_event_id)
        return SupportIngressReceipt(
            case_id=event.case_id,
            support_event_id=str(stored.get("supportEventId")),
            status=str(stored.get("status")),
            payload_digest=digest,
            outbox_command_id=(
                str(stored["outboxCommandId"]) if stored.get("outboxCommandId") else None
            ),
            duplicate=True,
            parked_count=await self.parked_count(event.case_id),
        )

    async def _alert_parked(
        self,
        event: NormalizedSupportEvent,
        *,
        count: int,
        exceeded: bool,
        now: datetime,
    ) -> None:
        """One alert per case per window, and an escalation past the quota.

        The window is a stored timestamp rather than in-process state: a
        disabled switch with live traffic behind it would otherwise page once
        per restart as well as once per window, and the dedupe would reset
        every deploy.

        A quota breach escalates *through* the dedupe. That is deliberate --
        the window exists to stop routine parking from paging in proportion to
        traffic, and a case past its quota is not routine parking.
        """
        window = timedelta(seconds=self._configuration.parking.alert_dedupe_window_seconds)
        state = await self._parking_alerts.find_one({"_id": event.case_id})
        last = state.get("lastAlertedAt") if state else None
        suppressed = isinstance(last, datetime) and (now - last) < window

        if exceeded:
            logger.error(
                "support_ingress_parking_quota_exceeded",
                extra={
                    "caseId": event.case_id,
                    "supportEventId": event.support_event_id,
                    "parkedCount": count,
                    "quota": self._configuration.parking.per_case_quota,
                },
            )
        elif not suppressed:
            logger.warning(
                "support_ingress_message_parked",
                extra={
                    "caseId": event.case_id,
                    "supportEventId": event.support_event_id,
                    "parkedCount": count,
                },
            )

        if not suppressed:
            await self._parking_alerts.update_one(
                {"_id": event.case_id},
                {"$set": {"caseId": event.case_id, "lastAlertedAt": now}},
                upsert=True,
            )

    def alert_should_fire(self, *, last_alerted_at: datetime | None, now: datetime) -> bool:
        """The dedupe predicate, exposed so a test can assert it directly."""
        if last_alerted_at is None:
            return True
        window = timedelta(seconds=self._configuration.parking.alert_dedupe_window_seconds)
        return (now - last_alerted_at) >= window


def inbound_chain(commands: Sequence[Mapping[str, Any]]) -> list[tuple[str, tuple[str, ...]]]:
    """`(event_id, predecessors)` for one case's inbound commands, in sequence.

    A reading helper for the operator surface and for the tests that assert the
    chain is a chain: sect. 7's guarantee is not "these have increasing
    sequence numbers", it is "each one names the last one", and only the second
    of those constrains a dispatcher.
    """
    ordered = sorted(
        (command for command in commands if command.get("stream") == CaseStream.INBOUND.value),
        key=lambda command: int(command.get("streamSequence") or 0),
    )
    return [
        (
            str(command.get("eventId")),
            tuple(str(item) for item in command.get("requiredPredecessorIds") or ()),
        )
        for command in ordered
    ]
