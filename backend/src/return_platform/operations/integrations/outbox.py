"""Lease-based transactional outbox dispatcher for external integrations."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol, cast

import httpx
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from return_platform.configuration.settings import Settings

logger = logging.getLogger("return_platform.integration_outbox")

#: The collection every command in this module lives in.
#:
#: Exported because three processes build its indexes and one API surface reads
#: it, and a collection name repeated as a literal in four files is the first
#: half of the drift `ensure_integration_outbox_indexes` closes.
#: `operations.repository.INTEGRATION_OUTBOX` is the same string, kept there for
#: the collection handle the repository exposes.
INTEGRATION_OUTBOX_COLLECTION: Final = "integration_outbox"


@dataclass(frozen=True, slots=True)
class OutboxCommand:
    id: str
    topic: str
    aggregate_type: str
    aggregate_id: str
    idempotency_key: str
    payload: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class DispatchResult:
    external_reference: str | None
    response_digest: str | None


class TopicDispatcher(Protocol):
    async def dispatch(self, command: OutboxCommand) -> DispatchResult: ...


class ExternalDependencyNotConfigured(RuntimeError):
    """Raised when a topic has no approved external adapter configuration."""


class PermanentDeliveryFailure(RuntimeError):
    """This command can never succeed as written. Stop retrying it.

    A closed workflow, a rejected contract, an id that resolves to nothing.
    Retrying any of them is an infinite loop that nobody is watching, so the
    command is dead-lettered into `REQUIRES_RECONCILIATION` instead -- a state
    an operator can list and Phase 10 can consume.

    `error_code` rather than the exception's type name, because a dispatcher
    knows *which* permanent failure this was and "PermanentDeliveryFailure" on
    the operator surface would tell nobody anything.
    """

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class TransientDeliveryFailure(RuntimeError):
    """The target is momentarily unreachable. Retry with backoff.

    A `RuntimeError` subclass so it lands in the retry branch below alongside
    the `RETRYABLE_HTTP_*` errors the HTTP adapters already raise. Declared
    anyway, so a dispatcher can say "not right now" without the reader having
    to know that a bare `RuntimeError` happens to mean retry.
    """

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code or message


#: The statuses `claim()` will take a command in.
#:
#: One constant because two things have to agree about it and they are in
#: different functions: `claim()`'s `$in` filter, and the
#: `partialFilterExpression` of the index that plans it. A partial index is only
#: usable when the query's predicate implies the index's, so a status added to
#: one and not the other does not fail -- MongoDB silently falls back to the
#: plan this index exists to replace, a blocking in-memory sort of every
#: claimable command. Written once so that cannot happen by editing.
CLAIMABLE_STATUSES: Final[tuple[str, ...]] = ("PENDING", "RETRY")

#: The status a permanently failed command comes to rest in.
DEAD_LETTER_STATUS: Final = "DEAD_LETTER"

#: Written beside it, and the field Phase 10 reads. Separate from `status`
#: because the two answer different questions: `status` is what the outbox will
#: do next (nothing), this is what a human or a reconciler still has to do.
REQUIRES_RECONCILIATION: Final = "REQUIRES_RECONCILIATION"


async def ensure_integration_outbox_indexes(
    database: AsyncDatabase[dict[str, object]],
) -> None:
    """Every index the integration outbox needs, in the one place that owns them.

    Three owners used to build these independently -- the operational
    repository, this dispatcher and `ReturnSupportService` -- and all three
    disagreed. Each built `idempotencyKey` and `(status, nextAttemptAt)`; only
    the dispatcher built `leaseUntil`, which is the index behind its own
    `claim()` lease predicate; only `ReturnSupportService` built
    `(createdAt DESC)`, which is the index behind the operator listing's sort in
    `api/integration_outbox.py`. The repository built neither of those two, and
    the repository is the one the orchestrator calls -- so which indexes a
    deployment ended up with depended on which processes had booted.

    Defined here, following the precedent `operations.support_events`
    already sets with `ensure_support_event_indexes`, because this module owns
    the collection's schema: `OutboxCommand`, `DEAD_LETTER_STATUS`,
    `REQUIRES_RECONCILIATION`, the lease fields, `attemptCount` and
    `nextAttemptAt` are all declared above, and `claim()` is the query these
    indexes exist to plan.

    The six, and the query each one plans:

    ``idempotencyKey`` (unique)
        One command per business event. A constraint before it is an index --
        it is what bounds redelivery to redelivery rather than duplication.

    ``(nextAttemptAt ASC, createdAt ASC)``, partial on `CLAIMABLE_STATUSES`
        `claim()`, in full: its `nextAttemptAt` range *and* both keys of its
        `sort=[("nextAttemptAt", 1), ("createdAt", 1)]`, over only the commands
        it can take.

        This is the index that ends `claim()`'s blocking sort. Its predicate is
        `{"status": {"$in": [...]}}` on the leading key of
        `(status, nextAttemptAt)`, and a multi-point range on a leading key
        stops that index from delivering rows in `nextAttemptAt` order -- so the
        server sorted every claimable command in memory to pick one. Measured
        against a real server on 4,000 commands: `SORT -> FETCH -> IXSCAN`,
        2,000 keys and 2,000 documents examined to return one. With this index
        the same `findAndModify` plans as `LIMIT -> FETCH -> IXSCAN`, one key
        and one document, no `SORT` stage.

        Partial rather than plain, for two reasons. It indexes only what
        `claim()` can claim, so it stays proportional to the queue rather than
        to every command ever delivered; and the partial predicate is what lets
        `status` drop out of the key entirely, which is what frees the sort.

    ``(status ASC, nextAttemptAt ASC)``
        Retained. It is no longer `claim()`'s winning plan, but it is the plan
        `claim()` falls back to -- correctly, just slowly -- against a database
        whose indexes were built by a release older than the partial index
        above, and it already exists on disk in every deployed environment.
        Dropping it is a separate decision from adding the one that supersedes
        it, and this function only ever creates.

    ``leaseUntil``
        The other half of `claim()`'s filter: the `$or` that lets a worker take
        a record whose lease has expired, is unset, or is already its own.

    ``(createdAt DESC)``
        `api/integration_outbox.py`'s `.sort("createdAt", -1)`. Without it that
        operator listing is a collection scan plus an in-memory sort, which is
        exactly what deleting the "redundant" third copy of this definition
        would have caused.

    ``(status ASC, reconciliationState ASC)``
        `reconciliationState` was unindexed. `_dead_letter` below writes
        `status=DEAD_LETTER` and `reconciliationState=REQUIRES_RECONCILIATION`
        together and Phase 10 sweeps on exactly that pair, so both equality
        terms are covered by one index rather than one of them being a filter
        applied after the fact.

    **`leaseOwner` is deliberately not indexed.** It appears in `claim()`'s
    `$or`, so the branch that lets a worker re-take its own lease is a residual
    filter rather than an index seek, and that is the right trade: measured on
    the same 4,000-command collection, adding `leaseOwner_1` changed `claim()`'s
    winning plan not at all -- same index, same stages, same keys and documents
    examined. The planner enters through the `nextAttemptAt` range either way
    and applies the whole `$or` after the fact. So the index would cost a write
    on every claim, every delivery and every failure, and buy nothing. The
    branch it would serve is also the rare one: a worker only meets its own
    lease after crashing mid-dispatch and restarting under the same id.

    The other five indexes are deliberately left to MongoDB's default naming.
    They already exist under those names in every deployed environment, and
    naming them now would make `create_index` raise `IndexOptionsConflict`
    against the same key pattern already on disk -- turning a consolidation into
    a boot failure. The partial index is left to default naming for the same
    consistency, which it can afford because it exists nowhere yet.
    `create_index` on an index that already exists is a no-op, so calling this
    from every process that touches the collection is safe and idempotent.
    """
    collection = database[INTEGRATION_OUTBOX_COLLECTION]
    await collection.create_index("idempotencyKey", unique=True)
    await collection.create_index([("status", ASCENDING), ("nextAttemptAt", ASCENDING)])
    await collection.create_index(
        [("nextAttemptAt", ASCENDING), ("createdAt", ASCENDING)],
        partialFilterExpression={"status": {"$in": list(CLAIMABLE_STATUSES)}},
    )
    await collection.create_index("leaseUntil")
    await collection.create_index([("createdAt", DESCENDING)])
    await collection.create_index([("status", ASCENDING), ("reconciliationState", ASCENDING)])


class HttpTicketDispatcher:
    """HTTP adapter for an explicitly configured external Support mirror."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if settings.support_ticket_base_url is None:
            raise ExternalDependencyNotConfigured("External Support base URL is not configured.")
        self._settings = settings
        self._client = client

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        headers = {
            "Idempotency-Key": command.idempotency_key,
            "X-Correlation-ID": command.id,
        }
        if self._settings.support_ticket_api_key is not None:
            headers["Authorization"] = (
                f"Bearer {self._settings.support_ticket_api_key.get_secret_value()}"
            )
        response = await self._client.post(
            f"{self._settings.support_ticket_base_url}/tickets",
            json=command.payload,
            headers=headers,
            timeout=self._settings.operation_timeout_seconds,
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"RETRYABLE_HTTP_{response.status_code}")
        if response.status_code >= 400:
            raise ExternalDependencyNotConfigured(
                f"External Support rejected the contract with HTTP {response.status_code}."
            )
        body = response.content
        digest = hashlib.sha256(body).hexdigest()
        external_reference: str | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                raw = parsed.get("id") or parsed.get("ticketId") or parsed.get("reference")
                external_reference = str(raw) if raw is not None else None
        except ValueError:
            pass
        return DispatchResult(
            external_reference=external_reference,
            response_digest=digest,
        )


class HttpJsonDispatcher:
    """Generic approved JSON command adapter for OMC, carrier, or notifications."""

    def __init__(
        self,
        *,
        base_url: str,
        resource_path: str,
        client: httpx.AsyncClient,
        timeout_seconds: float,
        api_key: str | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/{resource_path.lstrip('/')}"
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._api_key = api_key

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        headers = {
            "Idempotency-Key": command.idempotency_key,
            "X-Correlation-ID": command.id,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = await self._client.post(
            self._url,
            json=command.payload,
            headers=headers,
            timeout=self._timeout_seconds,
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"RETRYABLE_HTTP_{response.status_code}")
        if response.status_code >= 400:
            raise ExternalDependencyNotConfigured(
                f"External contract rejected with HTTP {response.status_code}."
            )
        digest = hashlib.sha256(response.content).hexdigest()
        external_reference: str | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                raw = (
                    parsed.get("id")
                    or parsed.get("commandId")
                    or parsed.get("bookingId")
                    or parsed.get("notificationId")
                    or parsed.get("reference")
                )
                external_reference = str(raw) if raw is not None else None
        except ValueError:
            pass
        return DispatchResult(
            external_reference=external_reference,
            response_digest=digest,
        )


class IntegrationOutboxDispatcher:
    """Claim one outbox record, dispatch it, and persist auditable delivery state."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        dispatchers: dict[str, TopicDispatcher],
        *,
        worker_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._collection = client[settings.mongo_database][INTEGRATION_OUTBOX_COLLECTION]
        self._worker_id = worker_id or f"outbox-{uuid.uuid4()}"
        self._dispatchers = dispatchers

    async def ensure_indexes(self) -> None:
        """The union, not this worker's own subset.

        `run_forever` calls it, so an outbox worker booting into a fresh
        database creates the operator listing's sort index too -- it used to
        create only the three its own `claim()` needed.
        """
        await ensure_integration_outbox_indexes(self._collection.database)

    async def claim(self, *, lease_seconds: int = 60) -> OutboxCommand | None:
        """Take the oldest claimable command and lease it to this worker.

        The status filter is `CLAIMABLE_STATUSES` verbatim, not a literal list.
        `(nextAttemptAt, createdAt)` is indexed *partially on that predicate*,
        and a partial index serves a query only when the query's predicate
        implies the index's -- so a status added here and not there would not
        break anything, it would quietly restore the blocking in-memory sort
        this arrangement removed.

        `leaseOwner` is unindexed on purpose and the `$or` is a residual filter;
        `ensure_integration_outbox_indexes` records the measurement behind that.
        """
        now = datetime.now(UTC)
        document = await self._collection.find_one_and_update(
            {
                "status": {"$in": list(CLAIMABLE_STATUSES)},
                "nextAttemptAt": {"$lte": now},
                "$or": [
                    {"leaseUntil": None},
                    {"leaseUntil": {"$exists": False}},
                    {"leaseUntil": {"$lt": now}},
                    {"leaseOwner": self._worker_id},
                ],
            },
            {
                "$set": {
                    "status": "DISPATCHING",
                    "leaseOwner": self._worker_id,
                    "leaseUntil": now + timedelta(seconds=lease_seconds),
                    "updatedAt": now,
                },
                "$inc": {"attemptCount": 1},
            },
            sort=[("nextAttemptAt", ASCENDING), ("createdAt", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        raw = cast(dict[str, Any], document)
        return OutboxCommand(
            id=str(raw["_id"]),
            topic=str(raw["topic"]),
            aggregate_type=str(raw["aggregateType"]),
            aggregate_id=str(raw["aggregateId"]),
            idempotency_key=str(raw["idempotencyKey"]),
            payload=cast(dict[str, Any], raw.get("payload", {})),
            attempt_count=int(raw.get("attemptCount", 1)),
        )

    async def _mark_delivered(self, command: OutboxCommand, result: DispatchResult) -> None:
        now = datetime.now(UTC)
        await self._collection.update_one(
            {"_id": command.id, "leaseOwner": self._worker_id},
            {
                "$set": {
                    "status": "DELIVERED",
                    "externalReference": result.external_reference,
                    "responseDigest": result.response_digest,
                    "deliveredAt": now,
                    "updatedAt": now,
                    "leaseOwner": None,
                    "leaseUntil": None,
                    "lastErrorCode": None,
                }
            },
        )

    async def _mark_failed(
        self,
        command: OutboxCommand,
        *,
        error_code: str,
        retryable: bool,
    ) -> None:
        now = datetime.now(UTC)
        delay_seconds = min(3_600, 2 ** min(command.attempt_count, 10))
        await self._collection.update_one(
            {"_id": command.id, "leaseOwner": self._worker_id},
            {
                "$set": {
                    "status": "RETRY" if retryable else "BLOCKED_EXTERNAL_DEPENDENCY",
                    "lastErrorCode": error_code[:128],
                    "nextAttemptAt": now + timedelta(seconds=delay_seconds),
                    "updatedAt": now,
                    "leaseOwner": None,
                    "leaseUntil": None,
                }
            },
        )

    async def _dead_letter(self, command: OutboxCommand, *, error_code: str) -> None:
        """Come to rest where an operator can find it.

        `claim` only looks at `PENDING` and `RETRY`, so writing `DEAD_LETTER`
        is what actually stops the retrying -- no counter, no tombstone
        collection. The lease is released so the record is not also reported as
        in-flight by a worker that has moved on.
        """
        now = datetime.now(UTC)
        await self._collection.update_one(
            {"_id": command.id, "leaseOwner": self._worker_id},
            {
                "$set": {
                    "status": DEAD_LETTER_STATUS,
                    "reconciliationState": REQUIRES_RECONCILIATION,
                    "lastErrorCode": error_code[:128],
                    "deadLetteredAt": now,
                    "updatedAt": now,
                    "leaseOwner": None,
                    "leaseUntil": None,
                }
            },
        )
        logger.error(
            "integration_outbox_dead_letter",
            extra={
                "topic": command.topic,
                "aggregate_id": command.aggregate_id,
                "idempotency_key": command.idempotency_key,
                "error_code": error_code,
            },
        )

    async def dispatch_once(self) -> bool:
        command = await self.claim()
        if command is None:
            return False
        dispatcher = self._dispatchers.get(command.topic)
        if dispatcher is None:
            await self._mark_failed(
                command,
                error_code="ADAPTER_NOT_CONFIGURED",
                retryable=False,
            )
            return True
        try:
            result = await dispatcher.dispatch(command)
        except PermanentDeliveryFailure as error:
            # First, and necessarily: it is a `RuntimeError`, so the retry
            # branch below would otherwise swallow it into a loop that never
            # ends.
            await self._dead_letter(command, error_code=error.error_code)
        except ExternalDependencyNotConfigured as error:
            await self._mark_failed(
                command,
                error_code=type(error).__name__,
                retryable=False,
            )
        except (httpx.TimeoutException, httpx.TransportError, RuntimeError) as error:
            logger.warning(
                "integration_outbox_dispatch_retry",
                extra={
                    "topic": command.topic,
                    "aggregate_id": command.aggregate_id,
                    "error_type": type(error).__name__,
                },
            )
            await self._mark_failed(
                command,
                # A dispatcher that named the failure keeps its name. Without
                # this the operator surface reports `TransientDeliveryFailure`
                # for every retry and cannot tell a Temporal outage from a
                # carrier timeout. The `getattr` keeps the existing adapters --
                # which raise bare `RuntimeError` and `httpx` errors -- reporting
                # exactly what they reported before.
                error_code=getattr(error, "error_code", None) or type(error).__name__,
                retryable=True,
            )
        else:
            await self._mark_delivered(command, result)
        return True

    async def run_forever(self, *, idle_seconds: float = 1.0) -> None:
        await self.ensure_indexes()
        while True:
            processed = await self.dispatch_once()
            if not processed:
                await asyncio.sleep(idle_seconds)
