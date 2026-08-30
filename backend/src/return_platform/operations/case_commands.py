"""Review-plane commands, durable before any workflow hears about them.

Contracts.md sect. 7: REST never signals Temporal directly. A command record
and its outbox row commit in ONE MongoDB transaction; the outbox worker's
`CaseCommandSignalDispatcher` delivers the signal; the workflow applies it
once, deduplicating on `signal_id`. The mechanics are
`DurableSupportEventStore.record_support_response`'s
(`operations/support_events.py`), re-keyed for the review plane:

    same signalId + same payload      -> idempotent no-op
    same signalId + different payload -> CommandIdempotencyConflictError
    stale (reviewId, draftVersion,
           canonicalEditVersion)      -> StaleReviewVersionError (API: 409)
    new signalId                      -> recorded, queued for delivery

Every command is enqueued on the case's `review_commands` stream
(`ordered_command_fields`), so its outbox row carries an `eventId`, a
`causationId` and `requiredPredecessorIds[]` and dispatches only when its
predecessors have completed.

The split between `plan_command` and `record_command` exists for the review
aggregate: its `OPEN -> APPROVING` transition must freeze the approved payload,
create the command and create the outbox row in *one* transaction
(contracts.md sect. 6), so it plans here and inserts inside its own
transaction. A caller with no larger transaction uses `record_command`, which
owns its own.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, cast

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError
from temporalio.client import Client
from temporalio.service import RPCError

from return_platform.configuration.settings import Settings
from return_platform.operations.integrations.outbox import (
    CaseStream,
    DispatchResult,
    OutboxCommand,
    PermanentDeliveryFailure,
    TransientDeliveryFailure,
    ordered_command_fields,
)
from return_platform.operations.integrations.temporal_signal import classify_rpc_error
from return_platform.operations.support_events import (
    SUPPORT_EVENT_AGGREGATE_TYPE,
    canonical_payload_digest,
)

logger = logging.getLogger("return_platform.operations.case_commands")

#: Where review-plane command records live. Their own collection: the identity
#: constraints below are the whole point, and `case_support_events` carries a
#: different aggregate's identity.
CASE_COMMAND_RECORDS: Final = "case_command_records"

#: Named so a test can assert each exists by name (the
#: `SUPPORT_EVENT_IDENTITY_INDEX` precedent).
CASE_COMMAND_SIGNAL_INDEX: Final = "case_command_signal_unique"
CASE_COMMAND_REVIEW_CAS_INDEX: Final = "case_command_review_version_unique"

#: One topic, one dispatcher, a closed signal map. The stored document names a
#: *kind*, never a method: `CaseCommandSignalDispatcher` refuses any kind
#: outside `SIGNAL_FOR_COMMAND_KIND`.
CASE_COMMAND_SIGNAL_TOPIC: Final = "return-case.command.signal"

#: Duplicated from `operations.repository.INTEGRATION_OUTBOX` for the same
#: cycle reason `operations/support_events.py` records.
_INTEGRATION_OUTBOX: Final = "integration_outbox"


class CaseCommandKind(StrEnum):
    """Every command the review plane can durably record (contracts.md sect. 6-7)."""

    TEMPLATE_APPROVED = "template_approved"
    TEMPLATE_REVISED = "template_revised"
    TEMPLATE_CANCELLED = "template_cancelled"
    REPLY_APPROVED = "reply_approved"
    REPLY_REVISED = "reply_revised"
    REPLY_CANCELLED = "reply_cancelled"
    CLARIFICATION_ANSWERED = "clarification_answered"
    #: Recovery surface (contracts.md sect. 6): capability
    #: `RETURNS_REVIEW_RECOVERY`, enforced at the API (V1).
    REVIEW_DELIVERY_RETRY = "review_delivery_retry"
    REVIEW_ABANDON = "review_abandon"


#: kind -> workflow signal name. A fixed code-side allowlist, exactly as
#: `SUPPORT_RESPONSE_SIGNAL` is fixed: a stored document can never name an
#: arbitrary method on a running workflow. The names equal the kind values
#: today; the map is what makes that a decision instead of a coincidence.
SIGNAL_FOR_COMMAND_KIND: Final[Mapping[CaseCommandKind, str]] = {
    kind: kind.value for kind in CaseCommandKind
}

#: Kinds that address one review attempt and therefore must carry `review_id`
#: and `scope_id` in their signal payload (contracts.md sect. 7).
_REVIEW_SCOPED_KINDS: Final[frozenset[CaseCommandKind]] = frozenset(
    {
        CaseCommandKind.TEMPLATE_APPROVED,
        CaseCommandKind.TEMPLATE_REVISED,
        CaseCommandKind.TEMPLATE_CANCELLED,
        CaseCommandKind.REPLY_APPROVED,
        CaseCommandKind.REPLY_REVISED,
        CaseCommandKind.REPLY_CANCELLED,
        CaseCommandKind.REVIEW_DELIVERY_RETRY,
        CaseCommandKind.REVIEW_ABANDON,
    }
)


class CommandIdempotencyConflictError(RuntimeError):
    """The same `signalId` was already recorded, saying something else."""

    def __init__(self, case_id: str, signal_id: str) -> None:
        super().__init__(
            f"case command signal {signal_id!r} on case {case_id!r} "
            "was already recorded with a different payload"
        )
        self.case_id = case_id
        self.signal_id = signal_id


class StaleReviewVersionError(RuntimeError):
    """The frozen approval CAS refused this write (contracts.md sect. 6).

    A command already exists for this exact
    `(case_id, review_id, draft_version, canonical_edit_version)` under a
    different signal id: somebody else approved this draft first. The API maps
    this to HTTP 409.
    """

    def __init__(
        self,
        case_id: str,
        review_id: str,
        draft_version: int,
        canonical_edit_version: int,
    ) -> None:
        super().__init__(
            f"review {review_id!r} on case {case_id!r} was already approved at "
            f"draft_version={draft_version} canonical_edit_version={canonical_edit_version}"
        )
        self.case_id = case_id
        self.review_id = review_id
        self.draft_version = draft_version
        self.canonical_edit_version = canonical_edit_version


@dataclass(frozen=True, slots=True)
class CaseCommandReceipt:
    """What a caller is told once the command has committed.

    No `delivered` field, for the reason `SupportEventReceipt` gives: nothing
    here knows whether the signal reached the workflow.
    """

    case_id: str
    command_id: str
    signal_id: str
    kind: CaseCommandKind
    payload_digest: str
    outbox_command_id: str
    case_sequence: int
    duplicate: bool


@dataclass(frozen=True, slots=True)
class PlannedCaseCommand:
    """The two documents one command commit inserts, plus its receipt."""

    command_document: dict[str, Any]
    outbox_document: dict[str, Any]
    receipt: CaseCommandReceipt


async def ensure_case_command_indexes(database: AsyncDatabase[dict[str, object]]) -> None:
    """The two identity constraints the store depends on.

    `(caseId, signalId)` is command identity -- redelivery bounded to
    redelivery. The partial `(caseId, reviewId, draftVersion,
    canonicalEditVersion)` index is the frozen approval CAS: exactly one
    approving command per version pair, enforced by the database rather than
    by whoever read the review last.
    """
    collection = database[CASE_COMMAND_RECORDS]
    await collection.create_index(
        [("caseId", ASCENDING), ("signalId", ASCENDING)],
        unique=True,
        name=CASE_COMMAND_SIGNAL_INDEX,
    )
    await collection.create_index(
        [
            ("caseId", ASCENDING),
            ("reviewId", ASCENDING),
            ("draftVersion", ASCENDING),
            ("canonicalEditVersion", ASCENDING),
        ],
        unique=True,
        partialFilterExpression={
            "reviewId": {"$type": "string"},
            "draftVersion": {"$type": "number"},
            "canonicalEditVersion": {"$type": "number"},
        },
        name=CASE_COMMAND_REVIEW_CAS_INDEX,
    )
    await collection.create_index([("caseId", ASCENDING), ("recordedAt", ASCENDING)])


class DurableCaseCommandStore:
    """Commit a review-plane command and its delivery command as one act."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
    ) -> None:
        self._client = client
        self._database = client[settings.mongo_database]
        self._commands = self._database[CASE_COMMAND_RECORDS]
        self._outbox = self._database[_INTEGRATION_OUTBOX]

    async def ensure_indexes(self) -> None:
        await ensure_case_command_indexes(self._commands.database)

    @staticmethod
    def outbox_idempotency_key(case_id: str, signal_id: str) -> str:
        return f"case-command:{case_id}:{signal_id}"

    async def plan_command(
        self,
        *,
        case_id: str,
        workflow_id: str,
        kind: CaseCommandKind,
        signal_id: str,
        actor_id: str,
        payload: Mapping[str, Any],
        review_id: str | None = None,
        return_record_id: str | None = None,
        draft_version: int | None = None,
        canonical_edit_version: int | None = None,
        causation_id: str | None = None,
        required_predecessor_ids: Sequence[str] = (),
        correlation_id: str | None = None,
        session: Any = None,
    ) -> PlannedCaseCommand:
        """Build one command's pair of documents inside the caller's transaction.

        Allocates the `review_commands` stream sequence through the supplied
        `session` and validates the payload against the kind: a review-scoped
        kind must carry `review_id` and `scope_id` in its signal payload
        (contracts.md sect. 7), and refusing that here is what keeps every
        producer honest rather than only the polite ones.
        """
        if (draft_version is None) != (canonical_edit_version is None):
            raise ValueError(
                "draft_version and canonical_edit_version travel together: "
                "the frozen CAS key is the pair, and half a key is no key"
            )
        signal_payload = dict(payload)
        if kind in _REVIEW_SCOPED_KINDS:
            if review_id is None:
                raise ValueError(f"{kind.value} commands must name a review_id")
            for required in ("review_id", "scope_id"):
                if not signal_payload.get(required):
                    raise ValueError(
                        f"{kind.value} signal payloads must carry {required!r} "
                        "(contracts.md sect. 7)"
                    )
            if signal_payload["review_id"] != review_id:
                raise ValueError("the payload's review_id must match the command's")

        digest = canonical_payload_digest(signal_payload)
        now = datetime.now(UTC)
        command_id = str(uuid.uuid4())
        outbox_command_id = str(uuid.uuid4())
        ordering = await ordered_command_fields(
            self._database,
            case_id=case_id,
            stream=CaseStream.REVIEW_COMMANDS,
            event_id=command_id,
            causation_id=causation_id,
            required_predecessor_ids=required_predecessor_ids,
            session=session,
        )
        command_document: dict[str, Any] = {
            "_id": command_id,
            "caseId": case_id,
            "workflowId": workflow_id,
            "kind": kind.value,
            "signalId": signal_id,
            "reviewId": review_id,
            "returnRecordId": return_record_id,
            "draftVersion": draft_version,
            "canonicalEditVersion": canonical_edit_version,
            #: Server-stamped, never client-supplied (contracts.md sect. 4).
            "actorId": actor_id,
            "payload": signal_payload,
            "payloadDigest": digest,
            "caseSequence": ordering["streamSequence"],
            "outboxCommandId": outbox_command_id,
            "correlationId": correlation_id,
            "recordedAt": now,
        }
        outbox_document: dict[str, Any] = {
            "_id": outbox_command_id,
            "topic": CASE_COMMAND_SIGNAL_TOPIC,
            "aggregateType": SUPPORT_EVENT_AGGREGATE_TYPE,
            "aggregateId": case_id,
            "idempotencyKey": self.outbox_idempotency_key(case_id, signal_id),
            "payload": {
                "caseId": case_id,
                "workflowId": workflow_id,
                "commandId": command_id,
                "signalId": signal_id,
                "kind": kind.value,
                "signal": signal_payload,
            },
            "status": "PENDING",
            "attemptCount": 0,
            "nextAttemptAt": now,
            "createdAt": now,
            "updatedAt": now,
            **ordering,
        }
        receipt = CaseCommandReceipt(
            case_id=case_id,
            command_id=command_id,
            signal_id=signal_id,
            kind=kind,
            payload_digest=digest,
            outbox_command_id=outbox_command_id,
            case_sequence=int(ordering["streamSequence"]),
            duplicate=False,
        )
        return PlannedCaseCommand(
            command_document=command_document,
            outbox_document=outbox_document,
            receipt=receipt,
        )

    async def record_command(self, **kwargs: Any) -> CaseCommandReceipt:
        """Persist one command and queue its delivery, or recognise a repeat.

        The standalone form of the plan/insert pair, owning its own
        transaction. `plan_command`'s keyword surface, minus `session`.
        """
        case_id = str(kwargs["case_id"])
        signal_id = str(kwargs["signal_id"])
        existing = await self._commands.find_one({"caseId": case_id, "signalId": signal_id})
        if existing is not None:
            return self._duplicate_receipt(existing, canonical_payload_digest(kwargs["payload"]))

        planned: PlannedCaseCommand | None = None

        async def transaction(mongo_session: Any) -> None:
            nonlocal planned
            planned = await self.plan_command(session=mongo_session, **kwargs)
            await self._commands.insert_one(dict(planned.command_document), session=mongo_session)
            await self._outbox.insert_one(dict(planned.outbox_document), session=mongo_session)

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(transaction)
        except DuplicateKeyError:
            return await self.classify_duplicate(
                case_id=case_id,
                signal_id=signal_id,
                payload=kwargs["payload"],
                review_id=kwargs.get("review_id"),
                draft_version=kwargs.get("draft_version"),
                canonical_edit_version=kwargs.get("canonical_edit_version"),
            )
        assert planned is not None
        return planned.receipt

    async def classify_duplicate(
        self,
        *,
        case_id: str,
        signal_id: str,
        payload: Mapping[str, Any],
        review_id: str | None,
        draft_version: int | None,
        canonical_edit_version: int | None,
    ) -> CaseCommandReceipt:
        """Decide what a `DuplicateKeyError` on a command insert meant.

        Three possibilities, in the order they are checked: the same signal
        already recorded (idempotent no-op or
        `CommandIdempotencyConflictError`, by digest); a *different* signal
        already holding the frozen `(reviewId, draftVersion,
        canonicalEditVersion)` slot (`StaleReviewVersionError` -> 409); or an
        outbox-key collision with no command, which is re-raised for the
        reason `DurableSupportEventStore` gives -- nothing partial survived
        the rollback, and success must not be reported for a write that did
        not happen.
        """
        winner = await self._commands.find_one({"caseId": case_id, "signalId": signal_id})
        if winner is not None:
            return self._duplicate_receipt(winner, canonical_payload_digest(payload))
        if (
            review_id is not None
            and draft_version is not None
            and canonical_edit_version is not None
        ):
            occupant = await self._commands.find_one(
                {
                    "caseId": case_id,
                    "reviewId": review_id,
                    "draftVersion": draft_version,
                    "canonicalEditVersion": canonical_edit_version,
                }
            )
            if occupant is not None:
                raise StaleReviewVersionError(
                    case_id, review_id, draft_version, canonical_edit_version
                )
        raise DuplicateKeyError(
            f"case command insert for signal {signal_id!r} on case {case_id!r} collided "
            "on neither the signal identity nor the review CAS key"
        )

    @staticmethod
    def _duplicate_receipt(stored: Mapping[str, Any], digest: str) -> CaseCommandReceipt:
        if str(stored.get("payloadDigest")) != digest:
            raise CommandIdempotencyConflictError(
                str(stored.get("caseId")), str(stored.get("signalId"))
            )
        return CaseCommandReceipt(
            case_id=str(stored.get("caseId")),
            command_id=str(stored.get("_id")),
            signal_id=str(stored.get("signalId")),
            kind=CaseCommandKind(str(stored.get("kind"))),
            payload_digest=digest,
            outbox_command_id=str(stored.get("outboxCommandId", "")),
            case_sequence=int(cast(int, stored.get("caseSequence", 0))),
            duplicate=True,
        )

    async def get_command(self, *, case_id: str, signal_id: str) -> dict[str, Any] | None:
        document = await self._commands.find_one({"caseId": case_id, "signalId": signal_id})
        return dict(document) if document is not None else None

    async def list_commands(self, case_id: str) -> list[dict[str, Any]]:
        cursor = self._commands.find({"caseId": case_id}).sort("caseSequence", ASCENDING)
        return [dict(document) async for document in cursor]


class CaseCommandSignalDispatcher:
    """Deliver one durably recorded review command as a workflow signal.

    `TemporalSignalDispatcher`'s shape (`operations/integrations/
    temporal_signal.py`) with one difference: the signal name comes from
    `SIGNAL_FOR_COMMAND_KIND`, a closed code-side map keyed by the stored
    `kind` -- so the reachable signal set is exactly the seven review signals
    plus the two recovery kinds, and a forged document naming anything else
    dead-letters as permanent.
    """

    def __init__(self, *, client_factory: Callable[[], Awaitable[Client]]) -> None:
        self._client_factory = client_factory
        self._client: Client | None = None
        self._connect_lock = asyncio.Lock()

    async def _client_or_transient(self) -> Client:
        if self._client is not None:
            return self._client
        async with self._connect_lock:
            if self._client is not None:
                return self._client
            try:
                self._client = await self._client_factory()
            except RPCError as error:
                raise self._classified(error, stage="CONNECT") from error
            except Exception as error:  # noqa: BLE001 - see TemporalSignalDispatcher
                raise TransientDeliveryFailure(
                    f"TEMPORAL_CONNECT_{type(error).__name__}"
                ) from error
        return self._client

    @staticmethod
    def _classified(error: RPCError, *, stage: str) -> Exception:
        code = f"TEMPORAL_{stage}_{error.status.name}"
        if classify_rpc_error(error):
            return PermanentDeliveryFailure(code, error_code=code)
        return TransientDeliveryFailure(code)

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        payload = command.payload
        workflow_id = payload.get("workflowId")
        signal = payload.get("signal")
        raw_kind = payload.get("kind")
        if not isinstance(workflow_id, str) or not workflow_id:
            raise PermanentDeliveryFailure(
                "CASE_COMMAND_HAS_NO_WORKFLOW_ID",
                error_code="CASE_COMMAND_HAS_NO_WORKFLOW_ID",
            )
        if not isinstance(signal, dict):
            raise PermanentDeliveryFailure(
                "CASE_COMMAND_HAS_NO_SIGNAL_PAYLOAD",
                error_code="CASE_COMMAND_HAS_NO_SIGNAL_PAYLOAD",
            )
        try:
            kind = CaseCommandKind(str(raw_kind))
        except ValueError:
            raise PermanentDeliveryFailure(
                "CASE_COMMAND_KIND_UNKNOWN",
                error_code="CASE_COMMAND_KIND_UNKNOWN",
            ) from None
        signal_name = SIGNAL_FOR_COMMAND_KIND[kind]

        client = await self._client_or_transient()
        handle = client.get_workflow_handle(workflow_id)
        try:
            await handle.signal(signal_name, cast(Any, signal))
        except RPCError as error:
            classified = self._classified(error, stage="SIGNAL")
            logger.warning(
                "case_command_signal_dispatch_failed",
                extra={
                    "workflow_id": workflow_id,
                    "signal_id": payload.get("signalId"),
                    "kind": kind.value,
                    "status": error.status.name,
                    "permanent": isinstance(classified, PermanentDeliveryFailure),
                },
            )
            raise classified from error
        except Exception as error:  # noqa: BLE001 - see TemporalSignalDispatcher
            if isinstance(error, (PermanentDeliveryFailure, TransientDeliveryFailure)):
                raise
            raise TransientDeliveryFailure(f"TEMPORAL_SIGNAL_{type(error).__name__}") from error
        return DispatchResult(external_reference=workflow_id, response_digest=None)
