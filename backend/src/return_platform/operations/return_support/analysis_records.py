"""One analysis record per support event, and the determinism it buys.

Contracts.md sect. 5. A support message is classified and then has entities
extracted from it, both by a model. Models are the least deterministic thing in
this system, and the ordinary way of using one -- ask, and if the answer is
unusable ask again, perhaps of something else -- means a case's analysis is a
function of which provider happened to be reachable at the moment somebody hit
retry. That is not a property anyone can audit, reproduce, or defend.

The record makes it one. Each stage pins its **routing decision** -- the
release, the routing policy version, and the ordered list of candidate routes
-- **before the first invocation**, and the pin never moves. Attempts live
beneath the record and each names the route it used, so the trail says which
provider produced which answer. Exactly one result per stage is *accepted*, by
CAS, and once a stage is accepted a retry reuses that result and never invokes
anything again: the second call is a redelivery of a decision, not a second
decision.

Two consequences worth stating in their own right, because both are places the
obvious implementation quietly gets it wrong:

**Artifact writes are gated on `accepted_extraction`.** Artifacts are the
durable consequence of the extraction -- what actually lands on the case. If
they could be written from an attempt rather than from the accepted result, a
losing attempt's artifacts would be on the case beside the winner's, and
nothing downstream could tell which extraction the case's own data came from.
`require_accepted_extraction` is the gate, and it is a function so that every
writer passes through the same one.

**Exhausted candidates block; they do not fall back to nothing.** When every
pinned route has been tried and none was available, the honest outcome is that
this event was not analysed, and a person has to know. The record goes
`BLOCKED` with the exhausted list recorded, an operations alert is logged, and
`CandidateRoutesExhaustedError` is raised for the dispatcher to classify as a
permanent failure -- a dead letter, not a silent empty result that would read
downstream as "the message contained nothing".

S2 owns this store, its schema, its indexes and its gate. The invocations that
fill it are V2's.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, NoReturn, cast

from pymongo import ASCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings

logger = logging.getLogger("return_platform.support_analysis")

#: The store's one collection. Attempts live beneath the record rather than in
#: their own: an attempt has no meaning apart from the record it belongs to,
#: and keeping them together is what makes "accept this result if nothing is
#: accepted yet" a single-document CAS instead of a transaction.
SUPPORT_ANALYSIS_RECORDS: Final = "support_analysis_records"

#: Asserted by name in tests.
ANALYSIS_EVENT_INDEX: Final = "support_analysis_event_unique"


class AnalysisStage(StrEnum):
    """The two staged model calls, each independently pinned and accepted."""

    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"


class AnalysisStatus(StrEnum):
    PENDING = "PENDING"
    CLASSIFIED = "CLASSIFIED"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


#: What a stage's fields are called on the record. Spelled once, here, because
#: the contract names these fields and three functions need to agree on them.
_RELEASE_FIELD: Final[Mapping[AnalysisStage, str]] = {
    AnalysisStage.CLASSIFICATION: "classification_release_id",
    AnalysisStage.EXTRACTION: "extraction_release_id",
}
_ROUTING_FIELD: Final[Mapping[AnalysisStage, str]] = {
    AnalysisStage.CLASSIFICATION: "classification_routing_decision",
    AnalysisStage.EXTRACTION: "extraction_routing_decision",
}
_ACCEPTED_FIELD: Final[Mapping[AnalysisStage, str]] = {
    AnalysisStage.CLASSIFICATION: "accepted_classification",
    AnalysisStage.EXTRACTION: "accepted_extraction",
}

#: Which status a stage's acceptance moves the record to. Extraction completes
#: it; classification only gets it halfway.
_STATUS_AFTER_ACCEPT: Final[Mapping[AnalysisStage, AnalysisStatus]] = {
    AnalysisStage.CLASSIFICATION: AnalysisStatus.CLASSIFIED,
    AnalysisStage.EXTRACTION: AnalysisStatus.COMPLETE,
}


class AnalysisRecordNotFoundError(KeyError):
    def __init__(self, support_event_id: str) -> None:
        super().__init__(f"no analysis record for support event {support_event_id!r}")
        self.support_event_id = support_event_id


class RoutingNotPinnedError(RuntimeError):
    """A stage was invoked before its routing decision was pinned.

    The pin is what makes the analysis reproducible; invoking first and
    recording the route afterwards would record which provider answered, not
    which providers were eligible, and those are different facts.
    """

    def __init__(self, support_event_id: str, stage: AnalysisStage) -> None:
        super().__init__(
            f"support event {support_event_id!r}: the {stage.value} routing decision must "
            "be pinned before the first invocation"
        )
        self.support_event_id = support_event_id
        self.stage = stage


class RouteNotPinnedError(RuntimeError):
    """An attempt or acceptance named a route the pinned decision does not list."""

    def __init__(self, support_event_id: str, stage: AnalysisStage, route_id: str) -> None:
        super().__init__(
            f"support event {support_event_id!r}: route {route_id!r} is not among the "
            f"pinned {stage.value} candidates"
        )
        self.support_event_id = support_event_id
        self.stage = stage
        self.route_id = route_id


class CandidateRoutesExhaustedError(RuntimeError):
    """Every pinned candidate was tried and none was available.

    Raised for the dispatcher to classify as a permanent failure. The record is
    already `BLOCKED` by the time this is raised -- the block is the durable
    part, and the exception is how the caller stops.
    """

    def __init__(self, support_event_id: str, stage: AnalysisStage, tried: Sequence[str]) -> None:
        super().__init__(
            f"support event {support_event_id!r}: every pinned {stage.value} candidate was "
            f"unavailable ({', '.join(tried) or 'none pinned'}); blocked for operations"
        )
        self.support_event_id = support_event_id
        self.stage = stage
        self.tried = tuple(tried)


class ArtifactWriteBlockedError(RuntimeError):
    """An artifact write was attempted before the extraction was accepted."""

    def __init__(self, support_event_id: str) -> None:
        super().__init__(
            f"support event {support_event_id!r}: artifacts may only be written from a "
            "committed accepted_extraction (contracts.md sect. 5)"
        )
        self.support_event_id = support_event_id


def require_accepted_extraction(record: Mapping[str, Any]) -> dict[str, Any]:
    """The artifact-write gate. Every artifact writer passes through here.

    Returns the accepted extraction so a caller cannot both take the gate and
    read the payload from somewhere else -- the gate and the source of the data
    are the same call, which is the only arrangement that cannot drift apart.
    """
    accepted = record.get(_ACCEPTED_FIELD[AnalysisStage.EXTRACTION])
    if not isinstance(accepted, Mapping) or not accepted:
        raise ArtifactWriteBlockedError(str(record.get("supportEventId")))
    return dict(accepted)


def _now() -> datetime:
    return datetime.now(UTC)


async def ensure_support_analysis_indexes(database: Any) -> None:
    collection = database[SUPPORT_ANALYSIS_RECORDS]
    await collection.create_index("supportEventId", unique=True, name=ANALYSIS_EVENT_INDEX)
    await collection.create_index([("caseId", ASCENDING), ("status", ASCENDING)])


class SupportAnalysisRecordStore:
    """One record per support event; pins, attempts, acceptance and the block."""

    def __init__(self, client: AsyncMongoClient[dict[str, object]], settings: Settings) -> None:
        self._database = client[settings.mongo_database]
        self._records = self._database[SUPPORT_ANALYSIS_RECORDS]

    async def ensure_indexes(self) -> None:
        await ensure_support_analysis_indexes(self._database)

    # ------------------------------------------------------------------ reads

    async def get(self, support_event_id: str) -> dict[str, Any]:
        document = await self._records.find_one({"supportEventId": support_event_id})
        if document is None:
            raise AnalysisRecordNotFoundError(support_event_id)
        return dict(document)

    async def find(self, support_event_id: str) -> dict[str, Any] | None:
        document = await self._records.find_one({"supportEventId": support_event_id})
        return dict(document) if document is not None else None

    async def list_blocked(self, case_id: str) -> list[dict[str, Any]]:
        """What the operations surface reads: the analyses nobody could finish."""
        cursor = self._records.find(
            {"caseId": case_id, "status": AnalysisStatus.BLOCKED.value}
        ).sort("createdAt", ASCENDING)
        return [dict(document) async for document in cursor]

    # ----------------------------------------------------------------- record

    async def ensure_record(self, *, case_id: str, support_event_id: str) -> dict[str, Any]:
        """One record per support event, however many times this is called.

        A fallback attempt that minted a second record would give the event two
        analyses and no way to say which one the case believes.
        """
        existing = await self._records.find_one({"supportEventId": support_event_id})
        if existing is not None:
            return dict(existing)
        now = _now()
        document: dict[str, Any] = {
            "_id": support_event_id,
            "supportEventId": support_event_id,
            "caseId": case_id,
            "status": AnalysisStatus.PENDING.value,
            "classification_release_id": None,
            "classification_routing_decision": None,
            "accepted_classification": None,
            "extraction_release_id": None,
            "extraction_routing_decision": None,
            "accepted_extraction": None,
            "attempts": [],
            "blockReason": None,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self._records.insert_one(dict(document))
        except DuplicateKeyError:
            winner = await self._records.find_one({"supportEventId": support_event_id})
            if winner is None:  # pragma: no cover - duplicate on no known key
                raise
            return dict(winner)
        return document

    # ------------------------------------------------------------ the pinning

    async def pin_routing_decision(
        self,
        *,
        support_event_id: str,
        stage: AnalysisStage,
        release_id: str,
        routing_policy_version: str,
        ordered_candidate_routes: Sequence[str],
    ) -> dict[str, Any]:
        """Pin a stage's routing decision, once, before anything is invoked.

        Idempotent by *keeping the first pin*, not by overwriting with the
        latest: a re-pin under a newer policy version would silently change
        what the already-recorded attempts were attempts at. The stored
        decision is returned either way, so a caller that re-pins gets the
        decision it must actually route by rather than the one it proposed.
        """
        if not ordered_candidate_routes:
            raise ValueError(
                f"a pinned {stage.value} decision needs at least one candidate route: "
                "an empty list is a block, and should be recorded as one"
            )
        record = await self.get(support_event_id)
        field = _ROUTING_FIELD[stage]
        pinned = record.get(field)
        if isinstance(pinned, Mapping) and pinned:
            return dict(pinned)
        decision = {
            "release_id": release_id,
            "routing_policy_version": routing_policy_version,
            "ordered_candidate_routes": [str(route) for route in ordered_candidate_routes],
            "pinned_at": _now(),
        }
        updated = await self._records.find_one_and_update(
            {"supportEventId": support_event_id, field: None},
            {
                "$set": {
                    field: decision,
                    _RELEASE_FIELD[stage]: release_id,
                    "updatedAt": _now(),
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            # Lost the race; the winner's pin is the pin.
            fresh = await self.get(support_event_id)
            return dict(cast(Mapping[str, Any], fresh[field]))
        return decision

    def routing_decision(self, record: Mapping[str, Any], stage: AnalysisStage) -> dict[str, Any]:
        """The pinned decision, or the refusal to invoke without one."""
        decision = record.get(_ROUTING_FIELD[stage])
        if not isinstance(decision, Mapping) or not decision:
            raise RoutingNotPinnedError(str(record.get("supportEventId")), stage)
        return dict(decision)

    def next_candidate_route(self, record: Mapping[str, Any], stage: AnalysisStage) -> str | None:
        """The next pinned route nothing has been attempted on yet.

        `None` means the candidates are exhausted -- which is the caller's cue
        to `block_exhausted`, not to invent a route that was never pinned.
        """
        decision = self.routing_decision(record, stage)
        tried = {
            str(attempt["routeId"])
            for attempt in cast(Sequence[Mapping[str, Any]], record.get("attempts") or [])
            if attempt.get("stage") == stage.value
        }
        for route in cast(Sequence[str], decision["ordered_candidate_routes"]):
            if route not in tried:
                return str(route)
        return None

    # ----------------------------------------------------------- the attempts

    async def record_attempt(
        self,
        *,
        support_event_id: str,
        stage: AnalysisStage,
        route_id: str,
        outcome: str,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One attempt, beneath the record, naming the route it used.

        The route is checked against the pin. An attempt on an unpinned route
        is not a routing mistake to be recorded and moved past -- it means the
        caller routed by something other than the decision, and the record
        would then describe an analysis that did not happen.
        """
        record = await self.get(support_event_id)
        decision = self.routing_decision(record, stage)
        if route_id not in decision["ordered_candidate_routes"]:
            raise RouteNotPinnedError(support_event_id, stage, route_id)
        attempt = {
            "stage": stage.value,
            "routeId": route_id,
            "outcome": outcome,
            "detail": dict(detail or {}),
            "attemptedAt": _now(),
            "releaseId": decision["release_id"],
            "routingPolicyVersion": decision["routing_policy_version"],
        }
        updated = await self._records.find_one_and_update(
            {"supportEventId": support_event_id},
            {
                "$push": {"attempts": attempt},
                "$set": {"updatedAt": _now()},
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:  # pragma: no cover - `get` above proved it exists
            raise AnalysisRecordNotFoundError(support_event_id)
        return attempt

    # --------------------------------------------------------- the acceptance

    async def accept_result(
        self,
        *,
        support_event_id: str,
        stage: AnalysisStage,
        route_id: str,
        result: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """CAS one result into a stage. Returns `(accepted_result, is_new)`.

        `is_new=False` means a result was already accepted and *this one was
        discarded*. That is the contract's "retries reuse the accepted result,
        never re-invoke" seen from the inside: the second answer is not merged,
        not preferred for being newer, and not recorded as the accepted one.
        The first accepted answer is the analysis, and it stays the analysis.
        """
        record = await self.get(support_event_id)
        decision = self.routing_decision(record, stage)
        if route_id not in decision["ordered_candidate_routes"]:
            raise RouteNotPinnedError(support_event_id, stage, route_id)
        field = _ACCEPTED_FIELD[stage]
        existing = record.get(field)
        if isinstance(existing, Mapping) and existing:
            return dict(existing), False
        accepted = {
            **dict(result),
            "route_id": route_id,
            "release_id": decision["release_id"],
            "accepted_at": _now(),
        }
        updated = await self._records.find_one_and_update(
            {"supportEventId": support_event_id, field: None},
            {
                "$set": {
                    field: accepted,
                    "status": _STATUS_AFTER_ACCEPT[stage].value,
                    "updatedAt": _now(),
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            # A concurrent acceptance won. Theirs is the accepted result.
            fresh = await self.get(support_event_id)
            return dict(cast(Mapping[str, Any], fresh[field])), False
        return accepted, True

    # -------------------------------------------------------------- the block

    async def block_exhausted(
        self, *, support_event_id: str, stage: AnalysisStage, reason: str | None = None
    ) -> NoReturn:
        """Every candidate tried and none available: block and dead-letter.

        Blocking first and raising second is deliberate. The block is the
        durable half -- it is what puts the event on the operations surface --
        and it must survive whatever the caller does with the exception.
        """
        record = await self.get(support_event_id)
        decision = self.routing_decision(record, stage)
        tried = [
            str(attempt["routeId"])
            for attempt in cast(Sequence[Mapping[str, Any]], record.get("attempts") or [])
            if attempt.get("stage") == stage.value
        ]
        blocked = {
            "stage": stage.value,
            "reason": reason or "ALL_CANDIDATE_ROUTES_UNAVAILABLE",
            "triedRoutes": tried,
            "orderedCandidateRoutes": list(decision["ordered_candidate_routes"]),
            "routingPolicyVersion": decision["routing_policy_version"],
            "blockedAt": _now(),
        }
        updated = await self._records.find_one_and_update(
            {"supportEventId": support_event_id},
            {
                "$set": {
                    "status": AnalysisStatus.BLOCKED.value,
                    "blockReason": blocked,
                    "updatedAt": _now(),
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:  # pragma: no cover - `get` above proved it exists
            raise AnalysisRecordNotFoundError(support_event_id)
        # The operations alert. A blocked analysis is a message nobody read; it
        # has to be louder than a status field waiting to be noticed.
        logger.error(
            "support_analysis_blocked",
            extra={
                "supportEventId": support_event_id,
                "caseId": record.get("caseId"),
                "stage": stage.value,
                "triedRoutes": tried,
            },
        )
        raise CandidateRoutesExhaustedError(support_event_id, stage, tried)
