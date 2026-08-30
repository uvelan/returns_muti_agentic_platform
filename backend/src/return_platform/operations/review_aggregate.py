"""The review aggregate: one support request's outbound draft, reviewed once.

Contracts.md sect. 6, in full. The unit is a support request; one review + one
outbound payload per request; `review_id` is an opaque per-attempt id scoped
`(case_id, request_id)` and `redraft` mints a new attempt under the same
scope. Both review kinds -- `TEMPLATE` and `SUPPORT_REPLY` -- live on this one
aggregate with the same guarantees.

State machine (frozen)::

    OPEN -> APPROVING -> SENT
    APPROVING -> DELIVERY_FAILED | HELD_FOR_OPERATIONS
    OPEN -> CANCELLED
    DELIVERY_FAILED -> APPROVING (authorized retry, same delivery identity)
                     | ABANDONED
    HELD_FOR_OPERATIONS -> OPEN | ABANDONED

`ABANDONED` is terminal and audited (actor, reason, timestamp). `actor=SYSTEM`
is reserved: it is how `auto_send` approves, and it is never assignable by a
client.

The three stores -- review aggregate, command/outbox
(`DurableCaseCommandStore`), draft-edit -- are co-located in one Mongo
database and one transaction scope. `OPEN -> APPROVING` is the transition that
uses all three at once: it locks the review, rejects unresolved conflicts and
pending revisions, verifies the expected `draft_version`, the expected
`canonical_edit_version` and the `canonical_approved_payload_hash` (that exact
field name, in request bodies and command payloads alike), persists the frozen
approved payload, and creates the command + outbox row -- one transaction, or
nothing. Version authority is this store and the edit store, never a Temporal
query.

Delivery identity (contracts.md sect. 7) is minted at approval and *stored*:
`logical_operation_id` derived from the approving command id, `delivery_id`
generated once and reused by every retry, `content_hash` of the frozen payload
for integrity/audit. A retry that is absorbed by the receiver dedupe
downstream still reaches `SENT` -- absorption is delivery.

S2 owns this file's schemas, indexes, transitions and tests; V1 drives them
through these methods and never around them (frozen boundary, contracts.md
sect. 10).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, cast

from pymongo import ASCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings
from return_platform.operations.case_commands import (
    CaseCommandKind,
    CaseCommandReceipt,
    DurableCaseCommandStore,
)
from return_platform.operations.support_events import canonical_payload_digest

#: The three collections of the aggregate's transactional home.
CASE_REVIEWS: Final = "case_reviews"
REVIEW_DRAFT_EDITS: Final = "review_draft_edits"
CASE_REVIEW_CONFLICTS: Final = "case_review_conflicts"

#: Named unique indexes, asserted by name in tests.
REVIEW_SCOPE_INDEX: Final = "case_review_open_scope_unique"
DRAFT_EDIT_ACTOR_INDEX: Final = "review_draft_edit_actor_unique"

#: The reserved system actor (contracts.md sect. 6). `auto_send` approves as
#: it; no client-supplied request may ever carry it.
SYSTEM_ACTOR: Final = "SYSTEM"


class ReviewKind(StrEnum):
    TEMPLATE = "TEMPLATE"
    SUPPORT_REPLY = "SUPPORT_REPLY"


class ReviewState(StrEnum):
    OPEN = "OPEN"
    APPROVING = "APPROVING"
    SENT = "SENT"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    HELD_FOR_OPERATIONS = "HELD_FOR_OPERATIONS"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"


#: The frozen transition table. Everything not listed is a 409.
_TRANSITIONS: Final[Mapping[ReviewState, frozenset[ReviewState]]] = {
    ReviewState.OPEN: frozenset({ReviewState.APPROVING, ReviewState.CANCELLED}),
    ReviewState.APPROVING: frozenset(
        {ReviewState.SENT, ReviewState.DELIVERY_FAILED, ReviewState.HELD_FOR_OPERATIONS}
    ),
    ReviewState.DELIVERY_FAILED: frozenset({ReviewState.APPROVING, ReviewState.ABANDONED}),
    ReviewState.HELD_FOR_OPERATIONS: frozenset({ReviewState.OPEN, ReviewState.ABANDONED}),
    ReviewState.SENT: frozenset(),
    ReviewState.CANCELLED: frozenset(),
    ReviewState.ABANDONED: frozenset(),
}

#: Terminal states. A redraft may follow one of these; nothing else may.
TERMINAL_REVIEW_STATES: Final[frozenset[ReviewState]] = frozenset(
    {ReviewState.SENT, ReviewState.CANCELLED, ReviewState.ABANDONED}
)


class TemplateReviewParkReason(StrEnum):
    """Why a case is parked on its template review (contracts.md sect. 6)."""

    TEMPLATE_REVIEW_UNANSWERED = "TEMPLATE_REVIEW_UNANSWERED"
    TEMPLATE_REVIEW_CANCELLED = "TEMPLATE_REVIEW_CANCELLED"
    TEMPLATE_REVIEW_GUARD_BLOCKED = "TEMPLATE_REVIEW_GUARD_BLOCKED"


class ReviewNotFoundError(KeyError):
    def __init__(self, case_id: str, review_id: str) -> None:
        super().__init__(f"review {review_id!r} on case {case_id!r} does not exist")
        self.case_id = case_id
        self.review_id = review_id


class ReviewStateError(RuntimeError):
    """The review is not in a state that allows this action. API: 409.

    Carries the state so the UI can surface *the transition* -- "this review
    is already approving" -- rather than a bare error (contracts.md sect. 6).
    """

    def __init__(self, review_id: str, state: ReviewState, action: str) -> None:
        super().__init__(f"review {review_id!r} is {state.value}; {action} is not available")
        self.review_id = review_id
        self.state = state
        self.action = action


class ReviewVersionMismatchError(RuntimeError):
    """An expected version did not match the store's. API: 409."""

    def __init__(self, review_id: str, field: str, expected: int, actual: int) -> None:
        super().__init__(f"review {review_id!r}: expected {field}={expected}, store holds {actual}")
        self.review_id = review_id
        self.field = field
        self.expected = expected
        self.actual = actual


class ReviewConflictError(RuntimeError):
    """Unresolved multi-actor edits. Approval and submit refuse until a
    canonical edit resolves them (select/merge/discard). API: 409."""

    def __init__(self, review_id: str) -> None:
        super().__init__(
            f"review {review_id!r} has edits from several actors; resolve a canonical "
            "edit before approving"
        )
        self.review_id = review_id


class PendingRevisionError(RuntimeError):
    """A requested revision has not been re-rendered yet. API: 409."""

    def __init__(self, review_id: str) -> None:
        super().__init__(f"review {review_id!r} has a revision pending; approve after re-render")
        self.review_id = review_id


class ApprovedPayloadHashMismatchError(RuntimeError):
    """The client approved bytes that are not the store's canonical payload."""

    def __init__(self, review_id: str) -> None:
        super().__init__(
            f"review {review_id!r}: canonical_approved_payload_hash does not match the "
            "canonical payload this store holds"
        )
        self.review_id = review_id


class ReservedActorError(RuntimeError):
    """`SYSTEM` is the platform's own actor, never a client's."""

    def __init__(self, action: str) -> None:
        super().__init__(f"actor {SYSTEM_ACTOR!r} is reserved and cannot {action}")
        self.action = action


def require_assignable_actor(actor_id: str, *, action: str) -> None:
    """The guard every client-facing mutation runs before touching the store."""
    if actor_id == SYSTEM_ACTOR:
        raise ReservedActorError(action)


def canonical_review_payload(review: Mapping[str, Any]) -> dict[str, Any]:
    """What approval freezes: the canonical edit if one exists, else the draft."""
    canonical_edit = review.get("canonicalEdit")
    if isinstance(canonical_edit, Mapping) and canonical_edit.get("canonical_payload"):
        return dict(cast(Mapping[str, Any], canonical_edit["canonical_payload"]))
    return dict(cast(Mapping[str, Any], review.get("draftPayload") or {}))


def _now() -> datetime:
    return datetime.now(UTC)


async def ensure_review_indexes(database: Any) -> None:
    reviews = database[CASE_REVIEWS]
    await reviews.create_index([("caseId", ASCENDING), ("requestId", ASCENDING)])
    await reviews.create_index([("caseId", ASCENDING), ("state", ASCENDING)])
    # One *non-terminal* attempt per (case, request, kind, scope): redraft can
    # mint attempt after attempt, but two live reviews over one request would
    # be two answers to one question.
    await reviews.create_index(
        [
            ("caseId", ASCENDING),
            ("requestId", ASCENDING),
            ("reviewKind", ASCENDING),
            ("scopeId", ASCENDING),
        ],
        unique=True,
        partialFilterExpression={
            "state": {
                "$in": [state.value for state in ReviewState if state not in TERMINAL_REVIEW_STATES]
            }
        },
        name=REVIEW_SCOPE_INDEX,
    )
    edits = database[REVIEW_DRAFT_EDITS]
    await edits.create_index(
        [("reviewId", ASCENDING), ("actorId", ASCENDING)],
        unique=True,
        name=DRAFT_EDIT_ACTOR_INDEX,
    )
    await edits.create_index([("caseId", ASCENDING), ("reviewId", ASCENDING)])


class ReviewAggregateStore:
    """Every write to the review aggregate, and the reads V1's surfaces need."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        *,
        command_store: DurableCaseCommandStore,
    ) -> None:
        self._client = client
        self._database = client[settings.mongo_database]
        self._reviews = self._database[CASE_REVIEWS]
        self._edits = self._database[REVIEW_DRAFT_EDITS]
        self._conflicts = self._database[CASE_REVIEW_CONFLICTS]
        self._commands = command_store

    async def ensure_indexes(self) -> None:
        await ensure_review_indexes(self._database)

    # ------------------------------------------------------------------ reads

    async def get_review(self, *, case_id: str, review_id: str) -> dict[str, Any]:
        document = await self._reviews.find_one({"_id": review_id, "caseId": case_id})
        if document is None:
            raise ReviewNotFoundError(case_id, review_id)
        return dict(document)

    async def list_reviews(self, case_id: str) -> list[dict[str, Any]]:
        cursor = self._reviews.find({"caseId": case_id}).sort("createdAt", ASCENDING)
        return [dict(document) async for document in cursor]

    async def get_edit_state(
        self, *, case_id: str, review_id: str, actor_id: str
    ) -> dict[str, Any] | None:
        """One actor's private edit row. Never in the shared panel hash."""
        document = await self._edits.find_one(
            {"caseId": case_id, "reviewId": review_id, "actorId": actor_id}
        )
        return dict(document) if document is not None else None

    async def conflict_marker(self, case_id: str) -> dict[str, Any]:
        """The case-scoped, versioned conflict-presence marker.

        Participates in the shared panel hash; private edit *contents* never
        do. `present` derives from which reviews currently hold a conflict.
        """
        document = await self._conflicts.find_one({"_id": case_id})
        if document is None:
            return {"caseId": case_id, "version": 0, "present": False, "reviewIds": []}
        reviews = cast(Mapping[str, Any], document.get("reviews") or {})
        review_ids = sorted(review_id for review_id, flagged in reviews.items() if flagged)
        return {
            "caseId": case_id,
            "version": int(cast(int, document.get("version", 0))),
            "present": bool(review_ids),
            "reviewIds": review_ids,
        }

    # -------------------------------------------------------------- lifecycle

    async def create_review(
        self,
        *,
        case_id: str,
        request_id: str,
        review_kind: ReviewKind,
        draft_payload: Mapping[str, Any],
        scope_id: str | None = None,
        review_id: str | None = None,
    ) -> dict[str, Any]:
        """Open one review attempt, or return the live one already open.

        `scope_id` defaults to the request id for a TEMPLATE review; a
        SUPPORT_REPLY review's scope id is minted server-side here
        (contracts.md sect. 6) unless the gating transition supplies one.
        Check-then-act idempotent: re-running returns the open attempt, and
        the partial unique index catches the race the read cannot.
        """
        resolved_scope = scope_id or (
            request_id if review_kind is ReviewKind.TEMPLATE else str(uuid.uuid4())
        )
        query = {
            "caseId": case_id,
            "requestId": request_id,
            "reviewKind": review_kind.value,
            "scopeId": resolved_scope,
            "state": {"$in": [s.value for s in ReviewState if s not in TERMINAL_REVIEW_STATES]},
        }
        existing = await self._reviews.find_one(query)
        if existing is not None:
            return dict(existing)
        now = _now()
        document: dict[str, Any] = {
            "_id": review_id or str(uuid.uuid4()),
            "caseId": case_id,
            "requestId": request_id,
            "reviewKind": review_kind.value,
            "scopeId": resolved_scope,
            "state": ReviewState.OPEN.value,
            "draftVersion": 1,
            "draftPayload": dict(draft_payload),
            "pendingRevision": False,
            "canonicalEditVersion": 0,
            "canonicalEdit": None,
            "conflictPresent": False,
            "approvedPayload": None,
            "canonicalApprovedPayloadHash": None,
            "approvingCommandId": None,
            "logicalOperationId": None,
            "deliveryId": None,
            "contentHash": None,
            "holdReason": None,
            "abandonAudit": None,
            "stateHistory": [
                {"from": None, "to": ReviewState.OPEN.value, "actorId": None, "at": now}
            ],
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self._reviews.insert_one(dict(document))
        except DuplicateKeyError:
            winner = await self._reviews.find_one(query)
            if winner is None:
                raise
            return dict(winner)
        return document

    async def request_revision(
        self, *, case_id: str, review_id: str, actor_id: str
    ) -> dict[str, Any]:
        """Mark the draft as awaiting a re-render. Approval refuses meanwhile."""
        require_assignable_actor(actor_id, action="request a revision")
        return await self._guarded_update(
            case_id=case_id,
            review_id=review_id,
            action="revise",
            allowed_states=(ReviewState.OPEN,),
            update={"$set": {"pendingRevision": True}},
        )

    async def record_draft_revision(
        self,
        *,
        case_id: str,
        review_id: str,
        draft_payload: Mapping[str, Any],
        expected_draft_version: int,
    ) -> dict[str, Any]:
        """The re-rendered draft lands: `draft_version` moves, the flag clears."""
        review = await self.get_review(case_id=case_id, review_id=review_id)
        self._require_state(review, "record a draft revision", (ReviewState.OPEN,))
        current = int(review["draftVersion"])
        if current != expected_draft_version:
            raise ReviewVersionMismatchError(
                review_id, "draft_version", expected_draft_version, current
            )
        updated = await self._reviews.find_one_and_update(
            {
                "_id": review_id,
                "caseId": case_id,
                "state": ReviewState.OPEN.value,
                "draftVersion": expected_draft_version,
            },
            {
                "$set": {
                    "draftPayload": dict(draft_payload),
                    "pendingRevision": False,
                    "updatedAt": _now(),
                },
                "$inc": {"draftVersion": 1, "version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ReviewVersionMismatchError(
                review_id, "draft_version", expected_draft_version, current
            )
        return dict(updated)

    # ------------------------------------------------------------- draft edits

    async def upsert_draft_edit(
        self,
        *,
        case_id: str,
        review_id: str,
        actor_id: str,
        client_edit_id: str,
        base_draft_version: int,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """One coalesced autosave. Mutable row per `(review_id, actor_id)`.

        Not a fact (contracts.md sect. 6): autosaves coalesce in place, and
        `client_edit_id` makes the retried save a no-op rather than a version
        bump. Refused with a 409-shaped error once the review has left `OPEN`
        -- the UI surfaces the transition, and no edit is dropped: the row
        stays readable at `get_edit_state`.
        """
        require_assignable_actor(actor_id, action="edit a draft")
        review = await self.get_review(case_id=case_id, review_id=review_id)
        self._require_state(review, "edit", (ReviewState.OPEN,))
        current_draft = int(review["draftVersion"])
        if base_draft_version != current_draft:
            raise ReviewVersionMismatchError(
                review_id, "base_draft_version", base_draft_version, current_draft
            )
        now = _now()
        existing = await self._edits.find_one({"reviewId": review_id, "actorId": actor_id})
        if existing is not None and existing.get("clientEditId") == client_edit_id:
            return dict(existing)
        if existing is None:
            document = {
                "_id": str(uuid.uuid4()),
                "caseId": case_id,
                "reviewId": review_id,
                "actorId": actor_id,
                "editVersion": 1,
                "baseDraftVersion": base_draft_version,
                "clientEditId": client_edit_id,
                "payload": dict(payload),
                "createdAt": now,
                "updatedAt": now,
            }
            try:
                await self._edits.insert_one(dict(document))
            except DuplicateKeyError:
                return await self.upsert_draft_edit(
                    case_id=case_id,
                    review_id=review_id,
                    actor_id=actor_id,
                    client_edit_id=client_edit_id,
                    base_draft_version=base_draft_version,
                    payload=payload,
                )
            await self._after_edit_written(case_id, review_id)
            return document
        updated = await self._edits.find_one_and_update(
            {"reviewId": review_id, "actorId": actor_id},
            {
                "$set": {
                    "payload": dict(payload),
                    "clientEditId": client_edit_id,
                    "baseDraftVersion": base_draft_version,
                    "updatedAt": now,
                },
                "$inc": {"editVersion": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        assert updated is not None
        return dict(updated)

    async def _after_edit_written(self, case_id: str, review_id: str) -> None:
        """Conflict appears the moment a second actor holds an edit row.

        The review flag and the case-scoped marker move together, in one
        transaction. Torn the other way -- marker clear, review flagged --
        `approve()` refuses with `ReviewConflictError` while the panel shows
        nothing wrong: a 409 with no visible cause and nothing to resolve.
        """
        actors = {
            str(document["actorId"]) async for document in self._edits.find({"reviewId": review_id})
        }
        if len(actors) < 2:
            return
        review = await self._reviews.find_one({"_id": review_id})
        if review is None or review.get("conflictPresent") is True:
            return

        async def transaction(mongo_session: Any) -> None:
            await self._reviews.update_one(
                {"_id": review_id, "conflictPresent": {"$ne": True}},
                {"$set": {"conflictPresent": True, "updatedAt": _now()}, "$inc": {"version": 1}},
                session=mongo_session,
            )
            await self._bump_conflict_marker(
                case_id, review_id, present=True, session=mongo_session
            )

        async with self._client.start_session() as mongo_session:
            await mongo_session.with_transaction(transaction)

    async def _bump_conflict_marker(
        self, case_id: str, review_id: str, *, present: bool, session: Any = None
    ) -> None:
        await self._conflicts.update_one(
            {"_id": case_id},
            {
                "$set": {f"reviews.{review_id}": present, "updatedAt": _now()},
                "$inc": {"version": 1},
                "$setOnInsert": {"caseId": case_id},
            },
            upsert=True,
            session=session,
        )

    async def submit_edit(self, *, case_id: str, review_id: str, actor_id: str) -> dict[str, Any]:
        """A sole actor's submit auto-promotes to the canonical edit.

        With edits from several actors this refuses: the conflict must be
        resolved to a canonical edit (select/merge/discard) first.
        """
        require_assignable_actor(actor_id, action="submit an edit")
        review = await self.get_review(case_id=case_id, review_id=review_id)
        self._require_state(review, "submit", (ReviewState.OPEN,))
        edits = [dict(document) async for document in self._edits.find({"reviewId": review_id})]
        actors = {str(edit["actorId"]) for edit in edits}
        if actors != {actor_id}:
            raise ReviewConflictError(review_id)
        own = next(edit for edit in edits if str(edit["actorId"]) == actor_id)
        return await self.resolve_canonical_edit(
            case_id=case_id,
            review_id=review_id,
            resolved_by=actor_id,
            canonical_payload=cast(Mapping[str, Any], own["payload"]),
            resolved_from_actor_edit_ids=(str(own["_id"]),),
        )

    async def resolve_canonical_edit(
        self,
        *,
        case_id: str,
        review_id: str,
        resolved_by: str,
        canonical_payload: Mapping[str, Any],
        resolved_from_actor_edit_ids: Sequence[str],
    ) -> dict[str, Any]:
        """Write the canonical edit; the conflict marker clears in the same act.

        "In the same act" is contracts.md sect. 6's wording -- the marker is
        *"cleared by the canonical-edit write"*, one write and not a write
        followed by another -- and it is now literally true: both legs commit
        in one transaction or neither does. Torn, the pair is unrecoverable,
        because `conflict_marker()` reads the stored flags rather than
        recomputing from the edit rows: the panel would show a conflict for
        ever, the associate would be told to resolve one that no longer exists,
        and resolving again is a no-op against an already-clean review.
        """
        require_assignable_actor(resolved_by, action="resolve a canonical edit")
        review = await self.get_review(case_id=case_id, review_id=review_id)
        self._require_state(review, "resolve", (ReviewState.OPEN,))
        now = _now()
        canonical_edit = {
            "canonical_edit_version": int(review["canonicalEditVersion"]) + 1,
            "canonical_payload": dict(canonical_payload),
            "resolved_from_actor_edit_ids": [str(item) for item in resolved_from_actor_edit_ids],
            "resolved_by": resolved_by,
            "resolved_at": now,
        }
        updated: dict[str, Any] | None = None

        async def transaction(mongo_session: Any) -> None:
            nonlocal updated
            updated = await self._reviews.find_one_and_update(
                {
                    "_id": review_id,
                    "caseId": case_id,
                    "state": ReviewState.OPEN.value,
                    "canonicalEditVersion": review["canonicalEditVersion"],
                },
                {
                    "$set": {
                        "canonicalEdit": canonical_edit,
                        "conflictPresent": False,
                        "updatedAt": now,
                    },
                    "$inc": {"canonicalEditVersion": 1, "version": 1},
                },
                session=mongo_session,
                return_document=ReturnDocument.AFTER,
            )
            if updated is None:
                # Lost the version CAS. Nothing to clear, and raising inside the
                # transaction is what stops the marker moving on its own.
                raise _CanonicalEditLockLost()
            await self._bump_conflict_marker(
                case_id, review_id, present=False, session=mongo_session
            )

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(transaction)
        except _CanonicalEditLockLost:
            updated = None

        if updated is None:
            raise ReviewVersionMismatchError(
                review_id,
                "canonical_edit_version",
                int(review["canonicalEditVersion"]),
                -1,
            )
        return dict(updated)

    # ---------------------------------------------------------------- approval

    async def approve(
        self,
        *,
        case_id: str,
        review_id: str,
        actor_id: str,
        expected_draft_version: int,
        expected_canonical_edit_version: int,
        canonical_approved_payload_hash: str,
        workflow_id: str,
        signal_id: str,
        allow_system: bool = False,
        correlation_id: str | None = None,
    ) -> tuple[dict[str, Any], CaseCommandReceipt]:
        """`OPEN -> APPROVING`, atomically, with the command and outbox row.

        `auto_send` is this same transition with `actor_id=SYSTEM` and
        `allow_system=True`, refused by exactly the same rejections -- an
        unresolved conflict or pending revision holds the system actor just
        as it holds a person (the gap-forces-hold rule rides on that).
        """
        if actor_id == SYSTEM_ACTOR and not allow_system:
            raise ReservedActorError("approve")
        review = await self.get_review(case_id=case_id, review_id=review_id)
        self._require_state(review, "approve", (ReviewState.OPEN,))
        if review.get("pendingRevision"):
            raise PendingRevisionError(review_id)
        if review.get("conflictPresent"):
            raise ReviewConflictError(review_id)
        draft_version = int(review["draftVersion"])
        canonical_edit_version = int(review["canonicalEditVersion"])
        if draft_version != expected_draft_version:
            raise ReviewVersionMismatchError(
                review_id, "draft_version", expected_draft_version, draft_version
            )
        if canonical_edit_version != expected_canonical_edit_version:
            raise ReviewVersionMismatchError(
                review_id,
                "canonical_edit_version",
                expected_canonical_edit_version,
                canonical_edit_version,
            )
        frozen_payload = canonical_review_payload(review)
        actual_hash = canonical_payload_digest(frozen_payload)
        if actual_hash != canonical_approved_payload_hash:
            raise ApprovedPayloadHashMismatchError(review_id)

        kind = (
            CaseCommandKind.TEMPLATE_APPROVED
            if review["reviewKind"] == ReviewKind.TEMPLATE.value
            else CaseCommandKind.REPLY_APPROVED
        )
        now = _now()
        receipt: CaseCommandReceipt | None = None
        # Built once: the digest the duplicate branch compares against has to be
        # the digest of the payload that was actually planned, or "same signal,
        # same command" would be decided on two different sets of bytes.
        approval_payload: dict[str, Any] = {
            "review_id": review_id,
            "scope_id": str(review["scopeId"]),
            "signal_id": signal_id,
            "review_kind": str(review["reviewKind"]),
            "draft_version": draft_version,
            "canonical_edit_version": canonical_edit_version,
            "canonical_approved_payload_hash": canonical_approved_payload_hash,
        }

        async def transaction(mongo_session: Any) -> None:
            nonlocal receipt
            planned = await self._commands.plan_command(
                case_id=case_id,
                workflow_id=workflow_id,
                kind=kind,
                signal_id=signal_id,
                actor_id=actor_id,
                payload=approval_payload,
                review_id=review_id,
                draft_version=draft_version,
                canonical_edit_version=canonical_edit_version,
                correlation_id=correlation_id,
                session=mongo_session,
            )
            command_id = planned.receipt.command_id
            delivery_id = str(uuid.uuid4())
            locked = await self._reviews.find_one_and_update(
                {
                    "_id": review_id,
                    "caseId": case_id,
                    "state": ReviewState.OPEN.value,
                    "pendingRevision": False,
                    "conflictPresent": False,
                    "draftVersion": draft_version,
                    "canonicalEditVersion": canonical_edit_version,
                },
                {
                    "$set": {
                        "state": ReviewState.APPROVING.value,
                        "approvedPayload": frozen_payload,
                        "canonicalApprovedPayloadHash": canonical_approved_payload_hash,
                        "approvingCommandId": command_id,
                        "logicalOperationId": f"review-delivery:{command_id}",
                        "deliveryId": delivery_id,
                        "contentHash": actual_hash,
                        "approvedBy": actor_id,
                        "approvedAt": now,
                        "updatedAt": now,
                    },
                    "$inc": {"version": 1},
                    "$push": {
                        "stateHistory": {
                            "from": ReviewState.OPEN.value,
                            "to": ReviewState.APPROVING.value,
                            "actorId": actor_id,
                            "at": now,
                        }
                    },
                },
                session=mongo_session,
                return_document=ReturnDocument.AFTER,
            )
            if locked is None:
                # Somebody moved the review between the read and the lock.
                raise _ApprovalLockLost()
            # Command + outbox, in the transaction that just locked the review.
            receipt = await self._commands.insert_planned(planned, session=mongo_session)

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(transaction)
        except _ApprovalLockLost:
            fresh = await self.get_review(case_id=case_id, review_id=review_id)
            state = ReviewState(str(fresh["state"]))
            if state is not ReviewState.OPEN:
                raise ReviewStateError(review_id, state, "approve") from None
            raise ReviewVersionMismatchError(
                review_id, "draft_version", expected_draft_version, int(fresh["draftVersion"])
            ) from None
        except DuplicateKeyError:
            duplicate = await self._commands.classify_duplicate(
                case_id=case_id,
                signal_id=signal_id,
                payload=approval_payload,
                review_id=review_id,
                draft_version=draft_version,
                canonical_edit_version=canonical_edit_version,
            )
            fresh = await self.get_review(case_id=case_id, review_id=review_id)
            return fresh, duplicate

        assert receipt is not None
        approved = await self.get_review(case_id=case_id, review_id=review_id)
        return approved, receipt

    # ------------------------------------------------------- state transitions

    async def cancel(
        self, *, case_id: str, review_id: str, actor_id: str, reason: str
    ) -> dict[str, Any]:
        require_assignable_actor(actor_id, action="cancel a review")
        return await self._guarded_update(
            case_id=case_id,
            review_id=review_id,
            action="cancel",
            allowed_states=(ReviewState.OPEN,),
            new_state=ReviewState.CANCELLED,
            actor_id=actor_id,
            reason=reason,
        )

    async def mark_sent(self, *, case_id: str, review_id: str) -> dict[str, Any]:
        """Delivery landed -- including a retry absorbed by receiver dedupe."""
        return await self._guarded_update(
            case_id=case_id,
            review_id=review_id,
            action="mark sent",
            allowed_states=(ReviewState.APPROVING,),
            new_state=ReviewState.SENT,
            actor_id=SYSTEM_ACTOR,
        )

    async def mark_delivery_failed(
        self, *, case_id: str, review_id: str, error_code: str
    ) -> dict[str, Any]:
        return await self._guarded_update(
            case_id=case_id,
            review_id=review_id,
            action="mark delivery failed",
            allowed_states=(ReviewState.APPROVING,),
            new_state=ReviewState.DELIVERY_FAILED,
            actor_id=SYSTEM_ACTOR,
            update={"$set": {"lastDeliveryErrorCode": error_code[:128]}},
        )

    async def hold_for_operations(
        self, *, case_id: str, review_id: str, reason: TemplateReviewParkReason | str
    ) -> dict[str, Any]:
        value = reason.value if isinstance(reason, TemplateReviewParkReason) else str(reason)
        return await self._guarded_update(
            case_id=case_id,
            review_id=review_id,
            action="hold for operations",
            allowed_states=(ReviewState.APPROVING,),
            new_state=ReviewState.HELD_FOR_OPERATIONS,
            actor_id=SYSTEM_ACTOR,
            reason=value,
            update={"$set": {"holdReason": value}},
        )

    async def resume_from_hold(
        self, *, case_id: str, review_id: str, actor_id: str
    ) -> dict[str, Any]:
        """The blocking condition cleared: back to `OPEN`, versions intact."""
        require_assignable_actor(actor_id, action="resume a held review")
        return await self._guarded_update(
            case_id=case_id,
            review_id=review_id,
            action="resume",
            allowed_states=(ReviewState.HELD_FOR_OPERATIONS,),
            new_state=ReviewState.OPEN,
            actor_id=actor_id,
            update={"$set": {"holdReason": None}},
        )

    async def abandon(
        self, *, case_id: str, review_id: str, actor_id: str, reason: str
    ) -> dict[str, Any]:
        """Terminal, audited, panel-visible (contracts.md sect. 6)."""
        require_assignable_actor(actor_id, action="abandon a review")
        now = _now()
        return await self._guarded_update(
            case_id=case_id,
            review_id=review_id,
            action="abandon",
            allowed_states=(ReviewState.DELIVERY_FAILED, ReviewState.HELD_FOR_OPERATIONS),
            new_state=ReviewState.ABANDONED,
            actor_id=actor_id,
            reason=reason,
            update={
                "$set": {"abandonAudit": {"actorId": actor_id, "reason": reason[:2_000], "at": now}}
            },
        )

    async def retry_delivery(
        self,
        *,
        case_id: str,
        review_id: str,
        actor_id: str,
        workflow_id: str,
        signal_id: str,
        correlation_id: str | None = None,
    ) -> tuple[dict[str, Any], CaseCommandReceipt]:
        """`DELIVERY_FAILED -> APPROVING` with the *same* delivery identity.

        The command kind is `review_delivery_retry` (capability
        `RETURNS_REVIEW_RECOVERY` at the API); its payload carries the stored
        `logical_operation_id`, `delivery_id` and frozen-payload hash so the
        send that follows is a redelivery, never a second message. Command +
        outbox + state move in one transaction.
        """
        require_assignable_actor(actor_id, action="retry a delivery")
        review = await self.get_review(case_id=case_id, review_id=review_id)
        self._require_state(review, "retry delivery", (ReviewState.DELIVERY_FAILED,))
        now = _now()
        receipt: CaseCommandReceipt | None = None
        # The stored identity, verbatim: this is a redelivery of one message,
        # not a second message that happens to say the same thing.
        retry_payload: dict[str, Any] = {
            "review_id": review_id,
            "scope_id": str(review["scopeId"]),
            "signal_id": signal_id,
            "logical_operation_id": str(review["logicalOperationId"]),
            "delivery_id": str(review["deliveryId"]),
            "content_hash": str(review["contentHash"]),
        }

        async def transaction(mongo_session: Any) -> None:
            nonlocal receipt
            planned = await self._commands.plan_command(
                case_id=case_id,
                workflow_id=workflow_id,
                kind=CaseCommandKind.REVIEW_DELIVERY_RETRY,
                signal_id=signal_id,
                actor_id=actor_id,
                payload=retry_payload,
                review_id=review_id,
                correlation_id=correlation_id,
                session=mongo_session,
            )
            moved = await self._reviews.find_one_and_update(
                {
                    "_id": review_id,
                    "caseId": case_id,
                    "state": ReviewState.DELIVERY_FAILED.value,
                },
                {
                    "$set": {"state": ReviewState.APPROVING.value, "updatedAt": now},
                    "$inc": {"version": 1},
                    "$push": {
                        "stateHistory": {
                            "from": ReviewState.DELIVERY_FAILED.value,
                            "to": ReviewState.APPROVING.value,
                            "actorId": actor_id,
                            "at": now,
                        }
                    },
                },
                session=mongo_session,
                return_document=ReturnDocument.AFTER,
            )
            if moved is None:
                raise _ApprovalLockLost()
            receipt = await self._commands.insert_planned(planned, session=mongo_session)

        try:
            async with self._client.start_session() as mongo_session:
                await mongo_session.with_transaction(transaction)
        except _ApprovalLockLost:
            fresh = await self.get_review(case_id=case_id, review_id=review_id)
            raise ReviewStateError(
                review_id, ReviewState(str(fresh["state"])), "retry delivery"
            ) from None
        except DuplicateKeyError:
            # The same retry, resent. The command already exists; the review is
            # already `APPROVING`. Report the stored receipt, not a second send.
            duplicate = await self._commands.classify_duplicate(
                case_id=case_id,
                signal_id=signal_id,
                payload=retry_payload,
                review_id=review_id,
                draft_version=None,
                canonical_edit_version=None,
            )
            return await self.get_review(case_id=case_id, review_id=review_id), duplicate

        assert receipt is not None
        moved = await self.get_review(case_id=case_id, review_id=review_id)
        return moved, receipt

    async def redraft(
        self,
        *,
        case_id: str,
        review_id: str,
        actor_id: str,
        draft_payload: Mapping[str, Any],
        reason: str = "REDRAFT",
    ) -> dict[str, Any]:
        """Mint a new attempt under the same `(case_id, request_id)` scope.

        An `OPEN` predecessor is cancelled first; a review in flight
        (`APPROVING` onward) refuses with the transition, exactly as edit and
        cancel do.
        """
        require_assignable_actor(actor_id, action="redraft")
        review = await self.get_review(case_id=case_id, review_id=review_id)
        state = ReviewState(str(review["state"]))
        if state is ReviewState.OPEN:
            await self.cancel(
                case_id=case_id, review_id=review_id, actor_id=actor_id, reason=reason
            )
        elif state not in TERMINAL_REVIEW_STATES:
            raise ReviewStateError(review_id, state, "redraft")
        return await self.create_review(
            case_id=case_id,
            request_id=str(review["requestId"]),
            review_kind=ReviewKind(str(review["reviewKind"])),
            scope_id=str(review["scopeId"]),
            draft_payload=draft_payload,
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _require_state(
        review: Mapping[str, Any], action: str, allowed: Sequence[ReviewState]
    ) -> None:
        state = ReviewState(str(review["state"]))
        if state not in allowed:
            raise ReviewStateError(str(review["_id"]), state, action)

    async def _guarded_update(
        self,
        *,
        case_id: str,
        review_id: str,
        action: str,
        allowed_states: Sequence[ReviewState],
        new_state: ReviewState | None = None,
        actor_id: str | None = None,
        reason: str | None = None,
        update: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One CAS transition. The filter is the transition table's guard."""
        if new_state is not None:
            for allowed in allowed_states:
                if new_state not in _TRANSITIONS[allowed]:  # pragma: no cover - table guard
                    raise RuntimeError(
                        f"transition {allowed.value} -> {new_state.value} is not in the "
                        "frozen table; this is a programming error, not a request error"
                    )
        now = _now()
        sets: dict[str, Any] = {"updatedAt": now}
        pushes: dict[str, Any] = {}
        if new_state is not None:
            sets["state"] = new_state.value
            pushes["stateHistory"] = {
                "from": [state.value for state in allowed_states],
                "to": new_state.value,
                "actorId": actor_id,
                "reason": reason,
                "at": now,
            }
        extra = dict(update or {})
        for field, value in cast(Mapping[str, Any], extra.get("$set", {})).items():
            sets[field] = value
        mongo_update: dict[str, Any] = {"$set": sets, "$inc": {"version": 1}}
        if pushes:
            mongo_update["$push"] = pushes
        updated = await self._reviews.find_one_and_update(
            {
                "_id": review_id,
                "caseId": case_id,
                "state": {"$in": [state.value for state in allowed_states]},
            },
            mongo_update,
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            review = await self.get_review(case_id=case_id, review_id=review_id)
            raise ReviewStateError(review_id, ReviewState(str(review["state"])), action)
        return dict(updated)


class _ApprovalLockLost(RuntimeError):
    """Internal: the CAS inside the approval transaction matched nothing."""


class _CanonicalEditLockLost(RuntimeError):
    """Internal: the version CAS inside the canonical-edit transaction missed.

    Raised rather than returned so the transaction aborts: the marker clear
    must not commit for a canonical edit that was never written.
    """
