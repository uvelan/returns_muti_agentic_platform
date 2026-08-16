"""Support's answer, written down before anything is told about it.

The defect this replaces was a dual write. `submit_return_outcome` signalled
Temporal straight from the request handler, so a closed workflow raised a raw
`RPCError` and the caller got an HTTP 500 with the RMA nowhere -- and the
ordering (persist, then signal) meant a crash between the two left a stored
event the retry saw as a duplicate and reported as success while the workflow
had never heard of it.

Here the commit is the only thing the request does. One MongoDB transaction
writes the Support event and the outbox command that will deliver it, and the
handler returns as soon as that commits. Delivery is the outbox worker's job,
retried until it succeeds or is classified permanent -- see
`operations.integrations.temporal_signal`.

Identity is `(caseId, supportEventId)` under a unique index. The digest stored
next to it is *not* the identity: it exists so that the same id arriving with a
different business payload can be refused rather than silently ignored. Two
sends of the same reply differ in nothing that matters and must be a no-op; the
same id carrying different records is a caller bug and gets a 409.

    same eventId + same payload      -> idempotent no-op
    same eventId + different payload -> IdempotencyConflictError
    new eventId                      -> recorded, queued for delivery
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings

#: Where Support events live. Its own collection rather than a row in
#: `case_facts`: facts are an append-only observation log with no uniqueness at
#: all, and the whole point here is a constraint the database enforces.
CASE_SUPPORT_EVENTS: Final = "case_support_events"

#: Named so a migration can find it, and so a test can assert it exists by name
#: rather than by guessing what MongoDB called it.
SUPPORT_EVENT_IDENTITY_INDEX: Final = "case_support_event_identity_unique"

#: Duplicated from `operations.repository.INTEGRATION_OUTBOX` rather than
#: imported, because `repository.ensure_indexes` imports *this* module for the
#: index above and a cycle broken by a deferred import is a cycle that fails on
#: whichever module is imported first. `return_support/service.py` already
#: writes the same literal for the same reason.
_INTEGRATION_OUTBOX: Final = "integration_outbox"

#: One topic, one signal. The dispatcher registered against this topic sends
#: `support_response` and nothing else, so a stored document can never name the
#: method that gets called on the workflow.
SUPPORT_RESPONSE_SIGNAL_TOPIC: Final = "return-case.support-response.signal"

#: The aggregate the outbox command belongs to, for the operator surface that
#: lists commands by aggregate.
SUPPORT_EVENT_AGGREGATE_TYPE: Final = "RETURN_CASE"


class SupportEventKind(StrEnum):
    """Which Support mutation an event records.

    One member today. Declared as an enum because Phase 4 adds tracking, label
    and cancellation as independently expressible notices, and a bare string
    would leave each of them free to invent its own spelling.
    """

    RETURN_OUTCOME = "RETURN_OUTCOME"


class IdempotencyConflictError(RuntimeError):
    """The same `supportEventId` has already been recorded, saying something else.

    Never raised for a repeat of an identical reply -- that is the no-op this
    whole mechanism exists to make free. This is the caller who reused an id
    across two different business events, which is the one case where silently
    accepting either answer loses data.
    """

    def __init__(self, case_id: str, support_event_id: str) -> None:
        super().__init__(
            f"support event {support_event_id!r} on case {case_id!r} "
            "was already recorded with a different payload"
        )
        self.case_id = case_id
        self.support_event_id = support_event_id


@dataclass(frozen=True, slots=True)
class SupportEventReceipt:
    """What the caller is told once the write has committed.

    `delivered` is deliberately absent. Nothing here knows whether the signal
    reached the workflow, and a field that looked like it did would be the
    dual-write claim again in a different shape.
    """

    case_id: str
    support_event_id: str
    payload_digest: str
    outbox_command_id: str
    #: True when this exact event was already on file. The caller returns the
    #: same success it returned the first time; no second mutation happened.
    duplicate: bool


def _canonical(value: Any) -> Any:
    """Reduce a payload to the form two equal business events share.

    Absent and null are the same statement -- "Support has not told us the
    tracking number" -- so a null is dropped rather than hashed, and a client
    that starts sending an explicit `null` for a field it used to omit does not
    turn every resend into a 409.

    Lists of references are sorted. `orderLineReferences` names a set of lines;
    the order the console happened to render them in is not part of the event.
    """
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if item is not None
        }
    if isinstance(value, (str, bytes)):
        return value.decode() if isinstance(value, bytes) else value
    if isinstance(value, Sequence):
        canonical_items = [_canonical(item) for item in value if item is not None]
        return sorted(canonical_items, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def canonical_payload_digest(payload: Mapping[str, Any]) -> str:
    """A digest of what the event *says*, not of how it was serialized.

    Only ever compared against another digest under the same id. It is not the
    identity: property order, an added optional field and a timestamp all move
    a digest without changing the business event, which is exactly why the plan
    forbids keying on one.
    """
    canonical = _canonical(payload)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def support_return_record(
    *,
    return_reference: str,
    tracking_reference: str | None = None,
    label_reference: str | None = None,
    return_location: str | None = None,
    shipping_instruction_reference: str | None = None,
    return_method: str | None = None,
    carrier: str | None = None,
    order_line_references: Iterable[str] = (),
) -> dict[str, Any]:
    """One RMA, keyed the way the workflow's dataclass names its fields.

    Snake case on purpose. This dict is the signal argument, and Temporal
    converts it into `SupportResponseNotice`/`SupportReturnRecord` by matching
    field names -- so the keys here are a contract with
    `workflows/return_case_workflow.py`, and
    `test_support_event_delivery.py::test_the_signal_envelope_fits_the_workflow_dataclass`
    fails if either side moves.

    `return_method` is the field that closes D23. It had a writer
    (`record_support_outcome`) and a home (`ReturnRecordView.returnMethod`, the
    per-record `return_method` fact) before it had a sender, so every case
    projected `returnMethod: None`, the completion profile never resolved, and
    `businessComplete` could not become true for any return. `None` still means
    "Support did not say" and never erases a method already recorded --
    `_canonical` above drops nulls before hashing for the same reason.

    `carrier` closes audit finding #9 the same way and for the same reason. It
    is the middle link the case path never had: a carrier reached the platform
    only through `SupportActionRequest`, on the session path, so
    `ShipmentProjection.carrier` had no producer and the Copilot filled its
    "Carrier & Service" tile from an order's source system instead. `None` here
    is silence, exactly as above.
    """
    return {
        "return_reference": return_reference,
        "tracking_reference": tracking_reference,
        "label_reference": label_reference,
        "return_location": return_location,
        "shipping_instruction_reference": shipping_instruction_reference,
        "return_method": return_method,
        "carrier": carrier,
        "order_line_references": list(order_line_references),
    }


def support_response_notice(
    *,
    work_item_id: str,
    support_event_id: str,
    records: Sequence[Mapping[str, Any]],
    rejected: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """The signal argument, carrying one field the workflow does not read yet.

    `support_event_id` is a superset field. Temporal's converter ignores keys a
    dataclass has no field for, so today's `SupportResponseNotice` deserializes
    without it and Phase 4 turns it on by adding the field -- no coordinated
    deploy, no version of this dispatcher that sends something the workflow
    chokes on.

    It has to travel now rather than in Phase 4 because the guarantee below the
    workflow is at-least-once: a dispatcher that signals and then dies before
    acknowledging redelivers, and the id is what lets the workflow recognise the
    second arrival as the same business event.
    """
    return {
        "work_item_id": work_item_id,
        "records": [dict(record) for record in records],
        "rejected": rejected,
        "reason": reason,
        "support_event_id": support_event_id,
    }


async def ensure_support_event_indexes(database: AsyncDatabase[dict[str, object]]) -> None:
    """The unique constraint that makes a Support event identifiable.

    Called from `OperationalRepository.ensure_indexes` so there is one place
    that builds indexes, and defined here so the constraint lives beside the
    code that depends on it.
    """
    await database[CASE_SUPPORT_EVENTS].create_index(
        [("caseId", ASCENDING), ("supportEventId", ASCENDING)],
        unique=True,
        name=SUPPORT_EVENT_IDENTITY_INDEX,
    )
    await database[CASE_SUPPORT_EVENTS].create_index(
        [("caseId", ASCENDING), ("recordedAt", ASCENDING)]
    )


class DurableSupportEventStore:
    """Commit a Support mutation and its delivery command as one act."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
    ) -> None:
        self._client = client
        database = client[settings.mongo_database]
        self._events = database[CASE_SUPPORT_EVENTS]
        self._outbox = database[_INTEGRATION_OUTBOX]

    async def ensure_indexes(self) -> None:
        await ensure_support_event_indexes(self._events.database)

    @staticmethod
    def outbox_idempotency_key(case_id: str, support_event_id: str) -> str:
        """The outbox's own unique key, derived from the event's identity.

        A second constraint saying the same thing as the first. The event insert
        and the command insert commit together, so this can only be hit by a
        writer that bypassed the event -- and one command per event is the
        property that keeps redelivery bounded to redelivery.
        """
        return f"support-response:{case_id}:{support_event_id}"

    async def record_support_response(
        self,
        *,
        case_id: str,
        work_item_id: str,
        support_event_id: str,
        records: Sequence[Mapping[str, Any]],
        rejected: bool,
        reason: str | None,
        workflow_id: str,
        actor_id: str,
        correlation_id: str | None = None,
    ) -> SupportEventReceipt:
        """Persist the event and queue its delivery, or recognise a repeat.

        Returns rather than raises for a repeat: Support pressing send twice is
        ordinary, and the second press must look exactly like the first from the
        console's side. The only refusal is a reused id that means something
        else, which raises `IdempotencyConflictError`.
        """
        notice = support_response_notice(
            work_item_id=work_item_id,
            support_event_id=support_event_id,
            records=records,
            rejected=rejected,
            reason=reason,
        )
        digest = canonical_payload_digest(notice)

        existing = await self._events.find_one(
            {"caseId": case_id, "supportEventId": support_event_id}
        )
        if existing is not None:
            return self._receipt_for(existing, case_id, support_event_id, digest)

        now = datetime.now(UTC)
        command_id = str(uuid.uuid4())
        event_document: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "caseId": case_id,
            "supportEventId": support_event_id,
            "kind": SupportEventKind.RETURN_OUTCOME.value,
            "workItemId": work_item_id,
            "workflowId": workflow_id,
            "payloadDigest": digest,
            "notice": notice,
            "outboxCommandId": command_id,
            "actorId": actor_id,
            "correlationId": correlation_id,
            "recordedAt": now,
        }
        command_document: dict[str, Any] = {
            "_id": command_id,
            "topic": SUPPORT_RESPONSE_SIGNAL_TOPIC,
            "aggregateType": SUPPORT_EVENT_AGGREGATE_TYPE,
            "aggregateId": case_id,
            "idempotencyKey": self.outbox_idempotency_key(case_id, support_event_id),
            "payload": {
                "caseId": case_id,
                "workflowId": workflow_id,
                "supportEventId": support_event_id,
                "notice": notice,
            },
            "status": "PENDING",
            "attemptCount": 0,
            "nextAttemptAt": now,
            "createdAt": now,
            "updatedAt": now,
        }

        async def transaction(mongo_session: Any) -> None:
            await self._events.insert_one(dict(event_document), session=mongo_session)
            await self._outbox.insert_one(dict(command_document), session=mongo_session)

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(transaction)
        except DuplicateKeyError:
            # Lost the race to a concurrent send of the same id. The winner's
            # document decides whether this is a no-op or a conflict, exactly as
            # it would have on the read above.
            winner = await self._events.find_one(
                {"caseId": case_id, "supportEventId": support_event_id}
            )
            if winner is None:
                # The collision was on the outbox key, not the event -- which
                # means something wrote a command for this event without the
                # event. Re-raised rather than papered over: the transaction has
                # already rolled back, so nothing partial survives, and a caller
                # told "recorded" for a write that did not happen is the failure
                # mode this whole module exists to remove.
                raise
            return self._receipt_for(winner, case_id, support_event_id, digest)

        return SupportEventReceipt(
            case_id=case_id,
            support_event_id=support_event_id,
            payload_digest=digest,
            outbox_command_id=command_id,
            duplicate=False,
        )

    @staticmethod
    def _receipt_for(
        stored: Mapping[str, Any],
        case_id: str,
        support_event_id: str,
        digest: str,
    ) -> SupportEventReceipt:
        if str(stored.get("payloadDigest")) != digest:
            raise IdempotencyConflictError(case_id, support_event_id)
        return SupportEventReceipt(
            case_id=case_id,
            support_event_id=support_event_id,
            payload_digest=digest,
            outbox_command_id=str(stored.get("outboxCommandId", "")),
            duplicate=True,
        )

    async def get_support_event(
        self, *, case_id: str, support_event_id: str
    ) -> dict[str, Any] | None:
        """One recorded event. The read a reconciler and the tests need."""
        document = await self._events.find_one(
            {"caseId": case_id, "supportEventId": support_event_id}
        )
        return dict(document) if document is not None else None

    async def list_support_events(self, case_id: str) -> list[dict[str, Any]]:
        cursor = self._events.find({"caseId": case_id}).sort("recordedAt", ASCENDING)
        return [dict(document) async for document in cursor]
