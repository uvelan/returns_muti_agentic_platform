"""SQL Server authoritative return/RMA/tracking persistence."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import random
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeVar

import pymssql

from return_platform.configuration.settings import Settings
from return_platform.operations.models import ReturnSessionView
from return_platform.operations.return_support.providers.contracts import ReturnSupportResult
from return_platform.operations.seed_manifest import SEED_ORDERS, SEED_SCENARIOS, manifest_digest
from return_platform.operations.sql_connection_pool import (
    SQLConnectionPool,
    get_sql_connection_pool,
)

T = TypeVar("T")

logger = logging.getLogger("return_platform.operations.sql_business_state")

#: SQL Server's "chosen as a deadlock victim" error number.
#:
#: A victim's transaction is rolled back *whole* before the error reaches the
#: client, so the loser has changed nothing and holds nothing. That is what
#: makes it distinct from every other SQL error here: it is not a statement
#: that was wrong, it is a statement that was asked to step aside.
_DEADLOCK_VICTIM_ERROR = 1205

#: How many times a deadlock victim re-runs before the error is reported.
#:
#: Bounded rather than generous. A deadlock is resolved the moment the winner
#: commits, so the re-run contends with strictly fewer writers than the attempt
#: that lost; a burst of eight resolves well inside this. A path that still
#: cannot get through after four attempts is not contending, it is wedged, and
#: retrying longer converts a fast error into a slow one.
_DEADLOCK_MAX_ATTEMPTS = 4

#: Base backoff between deadlock re-runs, jittered.
#:
#: Jittered because the writers that just deadlocked are, by construction, in
#: lockstep -- they arrived together and were released together. Re-running
#: them all after the same fixed pause reproduces the collision that caused the
#: deadlock instead of resolving it.
_DEADLOCK_BACKOFF_SECONDS = 0.05


def _is_deadlock_victim(error: BaseException) -> bool:
    """Whether SQL Server rolled this caller back to break a deadlock.

    Matched on the driver's error number rather than on the message, which is
    localized and carries the process id.
    """
    args: tuple[Any, ...] = getattr(error, "args", ())
    return bool(args) and args[0] == _DEADLOCK_VICTIM_ERROR


@dataclass(frozen=True, slots=True)
class ReturnRecordItemWrite:
    """One item on one RMA. Belongs to exactly one return record."""

    return_item_id: str
    order_line_id: str
    quantity: int
    product_id: str | None = None
    reason_code: str | None = None
    item_status: str = "CREATED"


@dataclass(frozen=True, slots=True)
class ReturnRecordWrite:
    """One RMA Support issued, with the fulfilment identity that is its own.

    Label, tracking and return location are fields of the record and never of
    the case: two records on one case carry two labels, and neither can reach
    the other.
    """

    return_record_id: str
    return_reference: str
    record_status: str = "ISSUED"
    source_system: str = "RETURN_SUPPORT"
    label_reference: str | None = None
    tracking_reference: str | None = None
    return_location: str | None = None
    shipping_instruction_reference: str | None = None
    #: How the goods come back, as Support decided it (D23). Per record for the
    #: same reason the label is: completion is evaluated per RMA against this
    #: value, so one case holding a `CUSTOMER_KEEP` record and a
    #: `PREPAID_PARCEL` one must be able to say so. `None` means Support has not
    #: said, which is the state that reports `RETURN_METHOD` outstanding.
    #:
    #: Not validated here. The vocabulary is
    #: `return_policy.normalized_return_methods` in the active configuration and
    #: the check belongs at the request boundary, where a snapshot can be read.
    return_method: str | None = None
    #: Who is carrying the goods back, as Support arranged it (audit finding #9).
    #:
    #: Per record, beside the tracking reference it belongs with: a split return
    #: travels back on two carriers and one column on the case could not say so.
    #:
    #: Distinct from `dbo.return_tracking.carrier_code`, which is a property of
    #: one *observation* -- the carrier that filed a scan for one tracking
    #: number, written by `record_shipment_update` with a required
    #: `tracking_type` beside it. This is Support's statement about the RMA, made
    #: before any carrier has filed anything, and `persist_case_return_records`
    #: could not write the tracking row anyway without inventing the
    #: `tracking_type` and `event_at` that row requires (CFG-03).
    carrier: str | None = None
    items: tuple[ReturnRecordItemWrite, ...] = ()


#: The tracking types `dbo.return_tracking` will accept, mirroring
#: `CK_return_tracking_type` in `sql_migrations/002_domain_models.sql`.
#:
#: **Schema, not configuration.** Which ship-via a parcel travelled under is a
#: property of that parcel, and the set of them is a database constraint -- an
#: operator cannot add a seventh through the Control Centre, because the CHECK
#: would refuse the row. Mirrored in Python only so a bad value is a 422 at the
#: request boundary instead of a constraint violation surfacing as a 500;
#: `tests/operations/test_tracking_type_vocabulary_matches_the_schema.py` fails
#: if the two ever disagree.
TRACKING_TYPES: frozenset[str] = frozenset(
    {"PPL", "BOL", "CUSTOMER_SHIP", "NO_LABEL", "DIRECT_VENDOR", "FIELD_SCRAP"}
)


@dataclass(frozen=True, slots=True)
class ShipmentUpdate:
    """One observation of a return shipment's state, scoped to one RMA (T-15, C4).

    `status_at` is the carrier's status timestamp, not the moment we were told.
    It is the ordering authority: whether an update is newer or stale is decided
    against it and nothing else, so a late-delivered older event cannot overtake
    a newer one just by arriving second.
    """

    return_reference: str
    tracking_reference: str
    shipment_status: str
    status_at: datetime
    #: Required, not defaulted (CFG-03). It is not configuration -- the vocabulary
    #: is `CK_return_tracking_type`, a database constraint, and which ship-via a
    #: parcel travelled under is a property of that parcel rather than of the
    #: deployment. It is not a default either: `"PPL"` silently filed a BOL
    #: freight movement as a parcel, and no reader downstream could tell the value
    #: had been assumed rather than observed.
    tracking_type: str
    carrier_code: str | None = None
    shipment_details: str | None = None


#: What `record_shipment_update` did, and it is always exactly one of these.
#:
#: The canonical rule, stated once so it cannot drift between call sites:
#:
#:   APPLIED  the update's `status_at` is strictly newer than the stored one,
#:            or the shipment had no state yet. Stored truth advances.
#:   DUPLICATE the same tracking reference at the same `status_at`. The same
#:            observation submitted twice. Nothing changes, and that is success.
#:   STALE    the update's `status_at` is older than the stored one. Rejected.
#:            Stored truth does NOT regress.
SHIPMENT_UPDATE_APPLIED = "APPLIED"
SHIPMENT_UPDATE_DUPLICATE = "DUPLICATE"
SHIPMENT_UPDATE_STALE = "STALE"


@dataclass(frozen=True, slots=True)
class ShipmentGraphSyncOutcome:
    """What an RMA-scoped shipment sync committed, as the writer needs to see it.

    Mirrors `workflows.return_case_activities.ReturnRecordSyncOutcome` rather
    than introducing a second shape: both answer "the projection committed, and
    into which generation", and a caller should not have to learn two vocabularies
    for one fact.
    """

    graph_generation_id: str
    synchronized_tracking_references: tuple[str, ...]
    nodes_written: int


class ShipmentGraphSyncPort(Protocol):
    """The one method the shipment write path needs from the targeted-sync stack.

    Structural, so `operations` neither imports `dynamic_knowledge` nor learns
    what a generation lease is; the adapter is
    `dynamic_knowledge/integration/shipment_state_sync.py`. It must raise rather
    than return on failure, exactly as `ReturnRecordGraphSyncPort` does -- the
    caller has already committed the authoritative row, and a port that reported
    partial success would leave the decision in the wrong place.

    Scoped by `return_reference` and not by case: contract C4 makes shipment
    state RMA-scoped, and one RMA legitimately carries several tracking numbers
    (a split return), which is why the anchored set is a tuple.
    """

    async def synchronize_shipments(
        self, *, return_reference: str, tracking_references: tuple[str, ...]
    ) -> ShipmentGraphSyncOutcome: ...


@dataclass(frozen=True, slots=True)
class ShipmentUpdateOutcome:
    """The result of one shipment update, and the state that is now current."""

    outcome: str
    return_reference: str
    tracking_reference: str
    current_status: str
    current_status_at: datetime
    row_version: int
    #: The generation this shipment was projected into, set only when the update
    #: was APPLIED and a sync port was configured. `None` means either that the
    #: update changed no stored truth -- and `outcome` says which of DUPLICATE
    #: and STALE -- or that this process holds no sync port. A failed sync is
    #: never `None`: it raises.
    graph_generation_id: str | None = None

    @property
    def applied(self) -> bool:
        return self.outcome == SHIPMENT_UPDATE_APPLIED


@dataclass(frozen=True, slots=True)
class CaseReturnRecordsWrite:
    """Everything one support outcome adds to the authoritative SQL return store.

    A whole-case unit rather than a per-record one because T-14 requires the
    records of one outcome to commit together: a case that ended up with RMA-A
    persisted and RMA-B lost is precisely the partial state the single
    transaction exists to prevent.
    """

    case_id: str
    tenant_id: str
    principal_id: str
    order_reference: str | None
    records: tuple[ReturnRecordWrite, ...]
    case_status: str = "SUPPORT_COMPLETED"


@dataclass(frozen=True, slots=True)
class RmaTicketItemWrite:
    """One `dbo.return_items` row."""

    return_item_id: str
    order_line_id: str
    product_id: str
    quantity: int
    reason_code: str


@dataclass(frozen=True, slots=True)
class RmaTicketWrite:
    """One support ticket and the return record it opens."""

    ticket_id: str
    session_id: str
    request_digest: str
    status: str
    return_reference: str
    return_status: str
    eligibility_decision: str
    order_reference: str | None
    customer_reference: str | None
    correlation_id: str | None
    primary_reason_code: str | None
    external_reference: str | None
    clarification_request: str | None
    items: tuple[RmaTicketItemWrite, ...]


@dataclass(frozen=True, slots=True)
class RmaTrackingWrite:
    """One `dbo.return_tracking` row."""

    tracking_id: str
    return_reference: str
    tracking_type: str
    tracking_reference: str
    carrier_code: str | None
    tracking_status: str
    event_at: Any
    shipment_details: str | None


class SQLBusinessStateRepository:
    """Bounded blocking SQL access isolated behind asynchronous methods.

    Every method borrows a connection from the process-wide bounded pool rather
    than opening one of its own. `pymssql.connect` per operation put no ceiling
    on how many connections a burst of returns could open at once; the pool caps
    it at `sqlserver_pool_max_size` and makes the wait for a free connection a
    bounded, observable failure instead of an unbounded one.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        pool: SQLConnectionPool | None = None,
        shipment_graph_sync: ShipmentGraphSyncPort | None = None,
    ) -> None:
        self._settings = settings
        self._pool_override = pool
        # Optional because most processes holding this repository never record a
        # shipment update -- `api/seed.py` and `api/warehouse_placement.py`
        # construct it per request for reads and reservations. A process that
        # DOES record shipment updates supplies it; `record_shipment_update`
        # reports its absence on the outcome rather than pretending it synced.
        self._shipment_graph_sync = shipment_graph_sync

    def _pool(self) -> SQLConnectionPool:
        """Resolve the process-wide pool for this configuration.

        Looked up per operation rather than captured in `__init__`: this
        repository is constructed per request in `api/seed.py` and
        `api/warehouse_placement.py`, so a pool bound at construction time
        would either be a new pool per request -- exactly the unbounded
        connection count this replaced -- or a pool that shutdown has already
        drained. The lookup is one dict read under a lock.
        """
        return self._pool_override or get_sql_connection_pool(self._settings)

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        """Borrow a pooled connection for a write.

        Commits on success, rolls back on failure, and returns the connection to
        the pool either way -- a failed business operation must not cost the
        process one of its bounded connections.
        """
        with self._pool().transaction() as connection:
            yield connection

    @contextmanager
    def _read(self) -> Iterator[Any]:
        """Borrow a pooled connection for read-only statements.

        Rolled back rather than committed on return: with `autocommit=False`
        even a bare `SELECT` opens a transaction that must not follow the
        connection to its next borrower.
        """
        with self._pool().acquire() as connection:
            yield connection

    async def _run(self, operation: Callable[[], T]) -> T:
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            return await asyncio.to_thread(operation)

    async def _run_retrying_deadlocks(
        self, operation: Callable[[], T], *, operation_name: str
    ) -> T:
        """`_run`, re-running the operation when SQL Server picks it as a victim.

        **Only for operations that are idempotent by construction**, which is
        why it is opt-in per call site rather than folded into `_run` or into
        the pool's `transaction()`. A blanket retry there would re-run writes
        whose second application is not a no-op.

        A deadlock victim is not a failed operation. Its transaction is rolled
        back whole before the error surfaces, so nothing partial persists and
        the caller's own predicate decides the outcome again from scratch --
        which for `record_shipment_update` means the re-run observes the row the
        winner committed and answers DUPLICATE, exactly as a sequential
        resubmission would.

        Note that this does *not* weaken the locking it retries around.
        `UPDLOCK, HOLDLOCK` is what makes the APPLIED/DUPLICATE/STALE verdict
        SQL Server's rather than a read-then-write race's, and the lock
        conversion it implies is the reason a victim is chosen at all. The
        contention is correct; only the reporting of it was wrong.
        """
        for attempt in range(1, _DEADLOCK_MAX_ATTEMPTS + 1):
            try:
                return await self._run(operation)
            except pymssql.Error as error:
                if not _is_deadlock_victim(error) or attempt == _DEADLOCK_MAX_ATTEMPTS:
                    raise
                logger.info(
                    "sql_deadlock_victim_retrying",
                    extra={
                        "operation": operation_name,
                        "attempt": attempt,
                        "max_attempts": _DEADLOCK_MAX_ATTEMPTS,
                    },
                )
                await asyncio.sleep(_DEADLOCK_BACKOFF_SECONDS * attempt * (0.5 + random.random()))
        raise AssertionError("unreachable: the final attempt either returns or raises")

    async def record_return_decision(
        self,
        session: ReturnSessionView,
        *,
        decision: str,
        return_reference: str | None,
        status: str,
    ) -> None:
        def operation() -> None:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_requests WITH (UPDLOCK, SERIALIZABLE)
                        SET correlation_id=%s, customer_reference=%s, order_reference=%s,
                            reason_code=%s, eligibility_decision=%s, return_reference=%s,
                            return_status=%s, row_version=row_version+1, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO dbo.return_requests (
                            session_id, correlation_id, customer_reference, order_reference,
                            reason_code, eligibility_decision, return_reference, return_status
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            session.correlationId,
                            session.customerReference,
                            session.orderReference,
                            session.reasonCode,
                            decision,
                            return_reference,
                            status,
                            session.id,
                            session.id,
                            session.correlationId,
                            session.customerReference,
                            session.orderReference,
                            session.reasonCode,
                            decision,
                            return_reference,
                            status,
                        ),
                    )

        await self._run(operation)

    async def record_fulfillment(
        self,
        session_id: str,
        *,
        fulfillment_reference: str | None,
        tracking_reference: str | None,
        warehouse_reference: str | None,
        bay_reference: str | None,
        status: str,
    ) -> None:
        def operation() -> None:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_fulfillment WITH (UPDLOCK, SERIALIZABLE)
                        SET fulfillment_reference=%s, tracking_reference=%s,
                            warehouse_reference=%s, bay_reference=%s,
                            fulfillment_status=%s, row_version=row_version+1,
                            updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO dbo.return_fulfillment (
                            session_id, fulfillment_reference, tracking_reference,
                            warehouse_reference, bay_reference, fulfillment_status
                        ) VALUES (%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            fulfillment_reference,
                            tracking_reference,
                            warehouse_reference,
                            bay_reference,
                            status,
                            session_id,
                            session_id,
                            fulfillment_reference,
                            tracking_reference,
                            warehouse_reference,
                            bay_reference,
                            status,
                        ),
                    )

        await self._run(operation)

    async def mark_return_status(self, session_id: str, status: str) -> None:
        def operation() -> None:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_requests
                        SET return_status=%s, row_version=row_version+1, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s
                        """,
                        (status, session_id),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("Authoritative return record is missing.")

        await self._run(operation)

    async def apply_seed_manifest(
        self,
        seed_version: str,
        applied_at: datetime,
        record_limit: int | None = None,
    ) -> int:
        digest = manifest_digest(
            seed_version,
            self._settings.validation_fingerprint_key.get_secret_value(),
            record_limit,
        )
        rows: Sequence[tuple[Any, ...]] = tuple(
            (
                scenario["id"],
                seed_version,
                digest,
                scenario["orderReference"],
                scenario["customerReference"],
                scenario["reasonCode"],
                scenario["expectedDecision"],
                applied_at,
            )
            for scenario in SEED_SCENARIOS
        )

        def operation() -> int:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM dbo.e2e_seed_scenarios WHERE seed_version=%s", (seed_version,)
                    )
                    cursor.executemany(
                        """
                        INSERT INTO dbo.e2e_seed_scenarios (
                            scenario_id, seed_version, seed_digest, order_reference,
                            customer_reference, reason_code, expected_decision, applied_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        rows,
                    )
            return len(rows)

        return await self._run(operation)

    async def seed_status(
        self,
        seed_version: str,
        record_limit: int | None = None,
    ) -> dict[str, Any]:
        digest = manifest_digest(
            seed_version,
            self._settings.validation_fingerprint_key.get_secret_value(),
            record_limit,
        )

        def operation() -> dict[str, Any]:
            with self._read() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT COUNT_BIG(*), MIN(seed_digest), MAX(seed_digest)
                        FROM dbo.e2e_seed_scenarios
                        WHERE seed_version=%s
                        """,
                        (seed_version,),
                    )
                    row = cursor.fetchone()
            count = int(row[0]) if row is not None else 0
            observed = str(row[1]) if row is not None and row[1] is not None else ""
            uniform = bool(row is not None and row[1] == row[2])
            return {
                "count": count,
                "digest": observed,
                "ready": count == len(SEED_SCENARIOS) and uniform and observed == digest,
            }

        return await self._run(operation)

    async def reset_seed_manifest(self, seed_version: str) -> None:
        def operation() -> None:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM dbo.e2e_seed_scenarios WHERE seed_version=%s", (seed_version,)
                    )

        await self._run(operation)

    async def reset_demo_business_state(
        self,
        seed_version: str,
        order_count: int | None = None,
    ) -> None:
        """Delete business facts created from deterministic E2E seed orders."""

        def operation() -> None:
            # The explicit try/rollback this replaced is now the pool's
            # `transaction()` contract, which additionally returns the
            # connection instead of closing it.
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    rows: list[tuple[Any, ...]] = []
                    effective_order_count = min(
                        len(SEED_ORDERS),
                        order_count if order_count is not None else len(SEED_ORDERS),
                    )
                    for offset in range(0, effective_order_count, 1_000):
                        order_batch = tuple(
                            str(SEED_ORDERS[index]["orderReference"])
                            for index in range(
                                offset,
                                min(offset + 1_000, effective_order_count),
                            )
                        )
                        placeholders = ",".join("%s" for _ in order_batch)
                        cursor.execute(
                            f"""
                            SELECT session_id, return_reference
                            FROM dbo.return_requests
                            WHERE order_reference IN ({placeholders})
                            """,
                            order_batch,
                        )
                        rows.extend(cursor.fetchall())
                    session_ids = tuple(str(row[0]) for row in rows)
                    return_references = tuple(str(row[1]) for row in rows if row[1] is not None)

                    def delete_many(
                        table: str,
                        column: str,
                        values: tuple[str, ...],
                    ) -> None:
                        for offset in range(0, len(values), 1_000):
                            value_batch = values[offset : offset + 1_000]
                            value_placeholders = ",".join("%s" for _ in value_batch)
                            cursor.execute(
                                f"DELETE FROM {table} WHERE {column} IN ({value_placeholders})",
                                value_batch,
                            )

                    delete_many(
                        "platform.bay_assignment",
                        "return_reference",
                        return_references,
                    )
                    delete_many(
                        "dbo.return_tracking",
                        "return_reference",
                        return_references,
                    )
                    delete_many(
                        "integration.return_support_ticket",
                        "session_id",
                        session_ids,
                    )
                    delete_many("dbo.return_items", "session_id", session_ids)
                    delete_many("dbo.return_fulfillment", "session_id", session_ids)
                    delete_many("dbo.return_requests", "session_id", session_ids)
                    cursor.execute(
                        "DELETE FROM dbo.e2e_seed_scenarios WHERE seed_version=%s",
                        (seed_version,),
                    )

        await self._run(operation)

    async def persist_support_result(
        self,
        session: ReturnSessionView,
        *,
        decision: str,
        request_digest: str,
        result: ReturnSupportResult,
    ) -> None:
        """Atomically persist the support ticket and authoritative return/tracking facts."""

        def operation() -> None:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_requests WITH (UPDLOCK, SERIALIZABLE)
                        SET correlation_id=%s, customer_reference=%s, order_reference=%s,
                            reason_code=%s, eligibility_decision=%s, return_reference=%s,
                            return_status=%s, row_version=row_version+1, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO dbo.return_requests (
                            session_id, correlation_id, customer_reference, order_reference,
                            reason_code, eligibility_decision, return_reference, return_status
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            session.correlationId,
                            session.customerReference,
                            session.orderReference,
                            session.reasonCode,
                            decision,
                            result.return_reference,
                            "APPROVED" if decision == "APPROVE" else "REJECTED",
                            session.id,
                            session.id,
                            session.correlationId,
                            session.customerReference,
                            session.orderReference,
                            session.reasonCode,
                            decision,
                            result.return_reference,
                            "APPROVED" if decision == "APPROVE" else "REJECTED",
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE integration.return_support_ticket WITH (UPDLOCK, SERIALIZABLE)
                        SET request_digest=%s, status=%s, external_reference=%s,
                            return_reference=%s, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO integration.return_support_ticket (
                            ticket_id, session_id, request_digest, status,
                            external_reference, return_reference
                        ) VALUES (%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            request_digest,
                            result.ticket_status,
                            result.external_reference,
                            result.return_reference,
                            session.id,
                            result.ticket_id,
                            session.id,
                            request_digest,
                            result.ticket_status,
                            result.external_reference,
                            result.return_reference,
                        ),
                    )
                    if result.return_reference is not None:
                        item_id = f"ITEM-{result.return_reference}"
                        cursor.execute(
                            """
                            IF NOT EXISTS (SELECT 1 FROM dbo.return_items WHERE return_item_id=%s)
                            INSERT INTO dbo.return_items (
                                return_item_id, session_id, return_reference, order_line_id,
                                product_id, quantity, reason_code, item_status
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'CREATED');
                            """,
                            (
                                item_id,
                                item_id,
                                session.id,
                                result.return_reference,
                                session.itemReferences[0],
                                (session.productReferences or session.itemReferences)[0],
                                session.returnQuantity,
                                session.reasonCode,
                            ),
                        )
                        cursor.execute(
                            """
                            UPDATE dbo.return_fulfillment WITH (UPDLOCK, SERIALIZABLE)
                            SET fulfillment_reference=%s, tracking_reference=%s,
                                fulfillment_status='TRACKING_ACTIVE', row_version=row_version+1,
                                updated_at=SYSUTCDATETIME()
                            WHERE session_id=%s;
                            IF @@ROWCOUNT = 0
                            INSERT INTO dbo.return_fulfillment (
                                session_id, fulfillment_reference, tracking_reference,
                                fulfillment_status
                            ) VALUES (%s,%s,%s,'TRACKING_ACTIVE');
                            """,
                            (
                                result.fulfillment_reference,
                                result.tracking_reference,
                                session.id,
                                session.id,
                                result.fulfillment_reference,
                                result.tracking_reference,
                            ),
                        )
                        if result.tracking_reference is not None:
                            tracking_id = f"TRACK-{result.return_reference}"
                            cursor.execute(
                                """
                                IF NOT EXISTS (
                                    SELECT 1 FROM dbo.return_tracking WHERE tracking_id=%s
                                )
                                INSERT INTO dbo.return_tracking (
                                    tracking_id, return_reference, tracking_type,
                                    tracking_reference, carrier_code, tracking_status, event_at
                                ) VALUES (%s,%s,%s,%s,'UPS','LABEL_CREATED',SYSUTCDATETIME());
                                """,
                                (
                                    tracking_id,
                                    tracking_id,
                                    result.return_reference,
                                    result.shipping_path,
                                    result.tracking_reference,
                                ),
                            )

        await self._run(operation)

    async def persist_case_return_records(
        self,
        write: CaseReturnRecordsWrite,
    ) -> tuple[str, ...]:
        """Persist one case and all of its RMAs in ONE idempotent transaction (T-14).

        The canonical case path wrote no SQL at all: `record_support_outcome`
        writes MongoDB and the graph, and the only SQL return writer --
        `persist_support_result` -- is reachable only from the legacy
        session providers. This is the missing authoritative write.

        Idempotent on ids the workflow supplies, so a Temporal retry or a
        replay after `continue_as_new` rewrites the same rows instead of
        minting a second RMA. Replay is a no-op; a *different* record trying to
        claim an order line another record already owns is not, and raises
        inside the transaction so the whole outcome rolls back rather than
        committing a case whose items are split across the wrong RMAs.

        Returns the persisted record ids, so the caller synchronizes exactly
        the records that committed -- never a wider set.
        """

        if not write.records:
            return ()

        def operation() -> None:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_case WITH (UPDLOCK, SERIALIZABLE)
                        SET tenant_id=%s, principal_id=%s, order_reference=%s,
                            case_status=%s, row_version=row_version+1,
                            updated_at=SYSUTCDATETIME()
                        WHERE case_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO dbo.return_case (
                            case_id, tenant_id, principal_id, order_reference, case_status
                        ) VALUES (%s,%s,%s,%s,%s);
                        """,
                        (
                            write.tenant_id,
                            write.principal_id,
                            write.order_reference,
                            write.case_status,
                            write.case_id,
                            write.case_id,
                            write.tenant_id,
                            write.principal_id,
                            write.order_reference,
                            write.case_status,
                        ),
                    )
                    for record in write.records:
                        cursor.execute(
                            """
                            UPDATE dbo.return_record WITH (UPDLOCK, SERIALIZABLE)
                            SET case_id=%s, return_reference=%s, label_reference=%s,
                                tracking_reference=%s, return_location=%s,
                                shipping_instruction_reference=%s, return_method=%s,
                                carrier=%s, record_status=%s,
                                source_system=%s, row_version=row_version+1,
                                updated_at=SYSUTCDATETIME()
                            WHERE return_record_id=%s;
                            IF @@ROWCOUNT = 0
                            INSERT INTO dbo.return_record (
                                return_record_id, case_id, return_reference, label_reference,
                                tracking_reference, return_location,
                                shipping_instruction_reference, return_method, carrier,
                                record_status, source_system
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
                            """,
                            (
                                write.case_id,
                                record.return_reference,
                                record.label_reference,
                                record.tracking_reference,
                                record.return_location,
                                record.shipping_instruction_reference,
                                record.return_method,
                                record.carrier,
                                record.record_status,
                                record.source_system,
                                record.return_record_id,
                                record.return_record_id,
                                write.case_id,
                                record.return_reference,
                                record.label_reference,
                                record.tracking_reference,
                                record.return_location,
                                record.shipping_instruction_reference,
                                record.return_method,
                                record.carrier,
                                record.record_status,
                                record.source_system,
                            ),
                        )
                        for item in record.items:
                            # Keyed on the supplied item id so a replay is a
                            # no-op. A second record claiming the same order
                            # line is NOT absorbed here -- it reaches
                            # UQ_return_record_item_case_line and raises.
                            cursor.execute(
                                """
                                IF NOT EXISTS (
                                    SELECT 1 FROM dbo.return_record_item
                                    WHERE return_item_id=%s
                                )
                                INSERT INTO dbo.return_record_item (
                                    return_item_id, return_record_id, case_id, order_line_id,
                                    product_id, quantity, reason_code, item_status
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                                """,
                                (
                                    item.return_item_id,
                                    item.return_item_id,
                                    record.return_record_id,
                                    write.case_id,
                                    item.order_line_id,
                                    item.product_id,
                                    item.quantity,
                                    item.reason_code,
                                    item.item_status,
                                ),
                            )

        await self._run(operation)
        return tuple(record.return_record_id for record in write.records)

    async def record_shipment_update(self, update: ShipmentUpdate) -> ShipmentUpdateOutcome:
        """Apply one RMA-scoped shipment update, idempotently and stale-safely (T-15).

        One statement decides it, inside one transaction: the UPDATE carries
        `AND %s > event_at`, so whether the stored truth advances is settled by
        SQL Server under the row lock rather than by a read-then-write this
        code performs. Two updates racing therefore cannot both believe they
        are newest -- which is the whole of contract C4's "stale-update safe",
        and is not something a compare-in-Python version could promise.

        The row identity is derived from the RMA and the tracking reference, so
        the same shipment resubmitted is the same row. It is never minted, and
        `UQ_return_tracking_reference` means it could not be anyway.

        **The graph sync hangs off APPLIED and nothing else.** A DUPLICATE or a
        STALE update changed no stored truth, so syncing on either would spend a
        targeted sync writing the graph a value it already holds, and would make
        a rejected stale update indistinguishable from an accepted one in the
        sync log -- the one place an operator would look to find out whether a
        regressive carrier event had been let through.

        **A deadlock victim is retried, not reported (SHIP-CONC-01).** Taking
        `UPDLOCK, HOLDLOCK` and then inserting is a lock conversion, and SQL
        Server resolves a conversion race by rolling one side back whole (error
        1205). Nothing here retried it, so a genuinely simultaneous duplicate
        surfaced as a driver error and became an HTTP 500 -- contradicting this
        path's own documented contract, under which a duplicate is a DUPLICATE
        200. It was never a correctness fault: the row and its status were
        right in every observed trial. Only the answer to the loser was wrong,
        and this operation is idempotent by construction, so the loser can
        simply ask again.
        """

        tracking_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"return-shipment:{update.return_reference}:{update.tracking_reference}",
            )
        )

        def operation() -> ShipmentUpdateOutcome:
            with self._transaction() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    # Take the row's lock before deciding anything. UPDLOCK
                    # holds it against a concurrent updater and HOLDLOCK holds
                    # the *range* when the row does not exist yet, so two first
                    # updates for one shipment cannot both find nothing and
                    # both insert.
                    cursor.execute(
                        """
                        SELECT event_at FROM dbo.return_tracking WITH (UPDLOCK, HOLDLOCK)
                        WHERE tracking_id=%s
                        """,
                        (tracking_id,),
                    )
                    existing = cursor.fetchone()

                    if existing is None:
                        # First state for this shipment. Creating it IS the
                        # update being applied -- distinguishing this from "a
                        # row already stood at this exact timestamp" is why
                        # existence is checked rather than inferred from
                        # whether the UPDATE below matched: on a fresh insert
                        # the stored `event_at` equals the incoming one, which
                        # is indistinguishable from a duplicate after the fact.
                        cursor.execute(
                            """
                            INSERT INTO dbo.return_tracking (
                                tracking_id, return_reference, tracking_type, tracking_reference,
                                carrier_code, tracking_status, event_at, shipment_details
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                            """,
                            (
                                tracking_id,
                                update.return_reference,
                                update.tracking_type,
                                update.tracking_reference,
                                update.carrier_code,
                                update.shipment_status,
                                update.status_at,
                                update.shipment_details,
                            ),
                        )
                        advanced = 1
                    else:
                        # Advance only on a strictly newer status timestamp.
                        # The comparison is in the WHERE clause, under the lock
                        # taken above, so the decision is SQL Server's and not
                        # a read-then-write this code could lose a race on.
                        cursor.execute(
                            """
                            UPDATE dbo.return_tracking
                            SET tracking_status=%s, carrier_code=%s, shipment_details=%s,
                                event_at=%s, row_version=row_version+1,
                                updated_at=SYSUTCDATETIME()
                            WHERE tracking_id=%s AND %s > event_at;
                            SELECT @@ROWCOUNT AS applied;
                            """,
                            (
                                update.shipment_status,
                                update.carrier_code,
                                update.shipment_details,
                                update.status_at,
                                tracking_id,
                                update.status_at,
                            ),
                        )
                        advanced = int((cursor.fetchone() or {}).get("applied") or 0)

                    cursor.execute(
                        """
                        SELECT tracking_status, event_at, row_version
                        FROM dbo.return_tracking WHERE tracking_id=%s
                        """,
                        (tracking_id,),
                    )
                    current = cursor.fetchone() or {}

            if advanced:
                outcome = SHIPMENT_UPDATE_APPLIED
            elif current.get("event_at") == update.status_at:
                outcome = SHIPMENT_UPDATE_DUPLICATE
            else:
                outcome = SHIPMENT_UPDATE_STALE
            return ShipmentUpdateOutcome(
                outcome=outcome,
                return_reference=update.return_reference,
                tracking_reference=update.tracking_reference,
                current_status=str(current.get("tracking_status") or ""),
                current_status_at=current["event_at"],
                row_version=int(current.get("row_version") or 1),
            )

        outcome = await self._run_retrying_deadlocks(
            operation, operation_name="record_shipment_update"
        )
        if not outcome.applied or self._shipment_graph_sync is None:
            return outcome
        # After the commit, never inside it. The sync reads the authoritative
        # store, so running it while the transaction was open would either read
        # the pre-update row or block on the lock this connection holds.
        #
        # Raises rather than swallowing, exactly as the RMA sync does: a
        # shipment the platform has accepted and the graph has never heard of is
        # a return that reads as AWAITING_HANDOFF to every agent, which is the
        # failure this step exists to close. It is also recoverable -- the
        # fulfilment read performs its own targeted sync before reading, and the
        # scheduled sync covers the rest -- so failing here costs a retry rather
        # than the update.
        sync = await self._shipment_graph_sync.synchronize_shipments(
            return_reference=update.return_reference,
            tracking_references=(update.tracking_reference,),
        )
        return dataclasses.replace(outcome, graph_generation_id=sync.graph_generation_id)

    async def read_shipment_state(self, return_reference: str) -> list[dict[str, Any]]:
        """Every shipment this RMA has, newest status first. RMA-scoped by key."""

        def operation() -> list[dict[str, Any]]:
            with self._read() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT tracking_id, return_reference, tracking_type, tracking_reference,
                               carrier_code, tracking_status, event_at, shipment_details,
                               row_version
                        FROM dbo.return_tracking
                        WHERE return_reference=%s
                        ORDER BY event_at DESC, tracking_id ASC
                        """,
                        (return_reference,),
                    )
                    return [dict(row) for row in cursor.fetchall() or []]

        return await self._run(operation)

    async def read_return_record_by_reference(self, return_reference: str) -> dict[str, Any] | None:
        """Which case owns this RMA, and what fulfilment identity the RMA carries.

        The reverse of `read_case_return_records`, and the read a shipment update
        needs: an update names an RMA, and the associate is looking at a case.
        Answered from the authoritative SQL store rather than from the MongoDB
        projection because `UQ_return_record_reference` is what makes the answer
        singular -- the projection has no such constraint, so a duplicated
        document there would silently pick a case.
        """

        def operation() -> dict[str, Any] | None:
            with self._read() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT return_record_id, case_id, return_reference, label_reference,
                               tracking_reference, return_location, record_status, row_version
                        FROM dbo.return_record
                        WHERE return_reference=%s
                        """,
                        (return_reference,),
                    )
                    row = cursor.fetchone()
                    return dict(row) if row else None

        return await self._run(operation)

    async def read_case_return_records(self, case_id: str) -> list[dict[str, Any]]:
        """Read back one case's RMAs and their items, record-scoped.

        The shape mirrors what was written: items nested inside their own
        record, never flattened onto the case.
        """

        def operation() -> list[dict[str, Any]]:
            with self._read() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT return_record_id, case_id, return_reference, label_reference,
                               tracking_reference, return_location,
                               shipping_instruction_reference, return_method, carrier,
                               record_status, source_system, row_version
                        FROM dbo.return_record
                        WHERE case_id=%s
                        ORDER BY created_at ASC, return_record_id ASC
                        """,
                        (case_id,),
                    )
                    records = [dict(row) for row in cursor.fetchall() or []]
                    cursor.execute(
                        """
                        SELECT return_item_id, return_record_id, order_line_id, product_id,
                               quantity, reason_code, item_status
                        FROM dbo.return_record_item
                        WHERE case_id=%s
                        ORDER BY created_at ASC, return_item_id ASC
                        """,
                        (case_id,),
                    )
                    items = [dict(row) for row in cursor.fetchall() or []]
            by_record: dict[str, list[dict[str, Any]]] = {}
            for item in items:
                by_record.setdefault(str(item["return_record_id"]), []).append(item)
            for record in records:
                record["items"] = by_record.get(str(record["return_record_id"]), [])
            return records

        return await self._run(operation)

    async def assign_bay(
        self,
        session: ReturnSessionView,
        *,
        return_reference: str,
        shipping_path: str,
        package_count: int = 1,
    ) -> tuple[str, str]:
        """Select the highest-priority compatible active bay and persist one assignment."""

        def operation() -> tuple[str, str]:
            with self._transaction() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT TOP (1) configuration.bay_id, configuration.warehouse_id
                        FROM platform.bay_configuration AS configuration WITH (UPDLOCK, HOLDLOCK)
                        OUTER APPLY (
                            SELECT COALESCE(SUM(assignment.package_count), 0) AS assigned_packages
                            FROM platform.bay_assignment AS assignment WITH (HOLDLOCK)
                            WHERE assignment.bay_id=configuration.bay_id
                              AND assignment.status IN ('CREATED','CONFIRMED','HOLD')
                        ) AS capacity
                        WHERE configuration.active=1
                          AND configuration.supported_shipping_paths LIKE %s
                          AND configuration.supported_product_types LIKE %s
                          AND (%s IS NULL OR configuration.warehouse_id=%s)
                          AND configuration.max_package_count-capacity.assigned_packages >= %s
                        ORDER BY configuration.priority ASC, configuration.bay_id ASC
                        """,
                        (
                            f'%"{shipping_path}"%',
                            f'%"{session.productType or "STANDARD"}"%',
                            session.processingWarehouseReference,
                            session.processingWarehouseReference,
                            package_count,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError("No compatible active bay is configured.")
                    bay_id = str(row["bay_id"])
                    warehouse_id = str(row["warehouse_id"])
                    assignment_id = f"ASN-{return_reference}"
                    cursor.execute(
                        """
                        IF NOT EXISTS (
                            SELECT 1 FROM platform.bay_assignment
                            WHERE return_reference=%s AND order_line_id=%s
                        )
                        INSERT INTO platform.bay_assignment (
                            assignment_id, return_reference, sales_order_number, order_line_id,
                            item_number, package_count, warehouse_id, bay_id, status
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'CREATED');
                        UPDATE dbo.return_fulfillment
                        SET warehouse_reference=%s, bay_reference=%s,
                            fulfillment_status='ASSIGNED', row_version=row_version+1,
                            updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        """,
                        (
                            return_reference,
                            session.itemReferences[0],
                            assignment_id,
                            return_reference,
                            session.orderReference,
                            session.itemReferences[0],
                            session.itemReferences[0],
                            package_count,
                            warehouse_id,
                            bay_id,
                            warehouse_id,
                            bay_id,
                            session.id,
                        ),
                    )
                return warehouse_id, bay_id

        return await self._run(operation)

    async def list_bay_candidates(
        self,
        *,
        warehouse_id: str | None,
        return_method: str,
        product_type: str,
    ) -> list[dict[str, Any]]:
        """Return platform-owned bay capacity candidates for agent ranking."""

        def operation() -> list[dict[str, Any]]:
            with self._read() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT configuration.bay_id, configuration.bay_type,
                               configuration.active, configuration.priority,
                               configuration.warehouse_id,
                               configuration.supported_shipping_paths,
                               configuration.supported_product_types,
                               configuration.hazardous_allowed,
                               configuration.oversized_allowed,
                               COALESCE(configuration.max_handling_unit_count,
                                        configuration.max_package_count) AS max_capacity,
                               COALESCE(reserved.reserved_capacity, 0) AS reserved_capacity
                        FROM platform.bay_configuration AS configuration
                        OUTER APPLY (
                            SELECT SUM(reservation.reserved_capacity) AS reserved_capacity
                            FROM platform.bay_reservation AS reservation
                            WHERE reservation.bay_id = configuration.bay_id
                              AND reservation.status IN ('RESERVED','ASSIGNED')
                              AND reservation.expires_at > SYSUTCDATETIME()
                        ) AS reserved
                        WHERE (%s IS NULL OR configuration.warehouse_id = %s)
                        ORDER BY configuration.priority ASC, configuration.bay_id ASC;
                        """,
                        (warehouse_id, warehouse_id),
                    )
                    rows = cursor.fetchall() or []
            candidates: list[dict[str, Any]] = []
            for row in rows:
                paths_raw = row.get("supported_shipping_paths") or "[]"
                product_types_raw = row.get("supported_product_types") or "[]"
                try:
                    paths = tuple(str(item) for item in json.loads(str(paths_raw)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    paths = ()
                try:
                    product_types = tuple(str(item) for item in json.loads(str(product_types_raw)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    product_types = ()
                if (
                    paths
                    and return_method not in paths
                    and not (return_method == "BRANCH_UPS" and "PPL" in paths)
                    and not (return_method in {"BRANCH_LTL", "OFFSITE_LTL"} and "BOL" in paths)
                ):
                    continue
                if product_types and product_type not in product_types:
                    continue
                max_capacity = int(row.get("max_capacity") or 0)
                reserved_capacity = int(row.get("reserved_capacity") or 0)
                candidates.append(
                    {
                        "bayId": str(row["bay_id"]),
                        "bayType": str(row["bay_type"]),
                        "warehouseId": str(row["warehouse_id"]),
                        "active": bool(row["active"]),
                        "priority": int(row["priority"]),
                        "capacityAvailable": max(0, max_capacity - reserved_capacity),
                        "supportsHazardous": bool(row.get("hazardous_allowed", False)),
                        "supportsOversized": bool(row.get("oversized_allowed", False)),
                        "supportedReturnMethods": [return_method],
                    }
                )
            return candidates

        return await self._run(operation)

    async def reserved_capacity_by_bay(self, bay_ids: Sequence[str]) -> dict[str, int]:
        """Live reserved capacity per bay, from the table the reservation locks.

        The one figure the graph provably cannot hold (BAY-02). Bay
        configuration is a source read that R2 forbids on an agent path and
        W2.7 moved to the graph; this is not that. It is an aggregate over
        *unexpired platform reservations*, evaluated at the instant of the
        query -- it changes with the clock rather than with any source write,
        so no sync however targeted makes a graph node current for it, and
        `WarehouseObservation` says so in as many words.

        Deliberately returns nothing but the aggregate. Selecting a
        configuration column here would reintroduce the bypass by the back
        door, and the caller already has configuration from the graph.

        The same predicate `reserve_and_assign_handling_unit` uses under
        `HOLDLOCK`, so the recommendation is filtered by the figure the
        reservation will be tested against rather than by a different one.
        This does not replace that lock: it is a read, several returns can
        still choose the same bay between it and the write, and the
        transaction remains the only place holding a lock over the decision.
        What it removes is every return being pointed at one bay that is
        already full.
        """
        unique = sorted({str(bay_id) for bay_id in bay_ids if str(bay_id)})
        if not unique:
            return {}

        def operation() -> dict[str, int]:
            placeholders = ", ".join(["%s"] * len(unique))
            with self._read() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        f"""
                        SELECT reservation.bay_id,
                               SUM(reservation.reserved_capacity) AS reserved_capacity
                        FROM platform.bay_reservation AS reservation
                        WHERE reservation.bay_id IN ({placeholders})
                          AND reservation.status IN ('RESERVED','ASSIGNED')
                          AND reservation.expires_at > SYSUTCDATETIME()
                        GROUP BY reservation.bay_id;
                        """,
                        tuple(unique),
                    )
                    rows = cursor.fetchall() or []
            return {
                str(row["bay_id"]): int(row.get("reserved_capacity") or 0)
                for row in rows
                if row.get("bay_id")
            }

        return await self._run(operation)

    # ------------------------------------------------------------------
    # RMA tickets (Returns Support workbench)
    #
    # Added here rather than in a parallel writer so these writes borrow the
    # same bounded pool, the same transaction helper and the same deadlock
    # retry as every other business write. A second writer would have needed
    # its own copy of all three.
    # ------------------------------------------------------------------

    async def create_rma_ticket(self, write: RmaTicketWrite) -> str:
        """Persist one RMA ticket, its return record and its items atomically.

        Returns `"CREATED"` or `"DUPLICATE"`. Duplicate is decided by the
        table's own UNIQUE(session_id) rather than by a read-then-write this
        method could lose a race on: the INSERT is attempted and the row count
        settles it, so two concurrent submits produce one ticket and two honest
        answers.
        """

        def operation() -> str:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT ticket_id FROM integration.return_support_ticket "
                        "WITH (UPDLOCK, HOLDLOCK) WHERE session_id=%s",
                        (write.session_id,),
                    )
                    if cursor.fetchone() is not None:
                        return "DUPLICATE"

                    # The request row is the parent of both the ticket and the
                    # items, so it has to exist before either.
                    cursor.execute(
                        """
                        UPDATE dbo.return_requests WITH (UPDLOCK, SERIALIZABLE)
                        SET correlation_id=%s, customer_reference=%s, order_reference=%s,
                            reason_code=%s, return_reference=%s, return_status=%s,
                            row_version=row_version+1, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO dbo.return_requests (
                            session_id, correlation_id, customer_reference, order_reference,
                            reason_code, eligibility_decision, return_reference, return_status
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            write.correlation_id,
                            write.customer_reference,
                            write.order_reference,
                            write.primary_reason_code,
                            write.return_reference,
                            write.return_status,
                            write.session_id,
                            write.session_id,
                            write.correlation_id,
                            write.customer_reference,
                            write.order_reference,
                            write.primary_reason_code,
                            write.eligibility_decision,
                            write.return_reference,
                            write.return_status,
                        ),
                    )
                    cursor.execute(
                        """
                        INSERT INTO integration.return_support_ticket (
                            ticket_id, session_id, request_digest, status,
                            external_reference, clarification_request, return_reference
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            write.ticket_id,
                            write.session_id,
                            write.request_digest,
                            write.status,
                            write.external_reference,
                            write.clarification_request,
                            write.return_reference,
                        ),
                    )
                    for item in write.items:
                        cursor.execute(
                            """
                            IF NOT EXISTS (
                                SELECT 1 FROM dbo.return_items WHERE return_item_id=%s
                            )
                            INSERT INTO dbo.return_items (
                                return_item_id, session_id, return_reference, order_line_id,
                                product_id, quantity, reason_code, item_status
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'CREATED');
                            """,
                            (
                                item.return_item_id,
                                item.return_item_id,
                                write.session_id,
                                write.return_reference,
                                item.order_line_id,
                                item.product_id,
                                item.quantity,
                                item.reason_code,
                            ),
                        )
                    return "CREATED"

        return await self._run_retrying_deadlocks(operation, operation_name="create_rma_ticket")

    async def upsert_return_tracking(self, write: RmaTrackingWrite) -> str:
        """Insert or update one return tracking row. Returns INSERTED/UPDATED.

        Keyed on `tracking_reference`, which the table already declares UNIQUE,
        so re-sending the same carrier reference updates its status rather than
        failing on the constraint or silently creating a second row.
        """

        def operation() -> str:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_tracking WITH (UPDLOCK, SERIALIZABLE)
                        SET return_reference=%s, tracking_type=%s, carrier_code=%s,
                            tracking_status=%s, event_at=%s, shipment_details=%s,
                            row_version=row_version+1, updated_at=SYSUTCDATETIME()
                        WHERE tracking_reference=%s;
                        SELECT @@ROWCOUNT;
                        """,
                        (
                            write.return_reference,
                            write.tracking_type,
                            write.carrier_code,
                            write.tracking_status,
                            write.event_at,
                            write.shipment_details,
                            write.tracking_reference,
                        ),
                    )
                    row = cursor.fetchone()
                    updated = int(row[0]) if row else 0
                    if updated:
                        return "UPDATED"
                    cursor.execute(
                        """
                        INSERT INTO dbo.return_tracking (
                            tracking_id, return_reference, tracking_type, tracking_reference,
                            carrier_code, tracking_status, event_at, shipment_details
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            write.tracking_id,
                            write.return_reference,
                            write.tracking_type,
                            write.tracking_reference,
                            write.carrier_code,
                            write.tracking_status,
                            write.event_at,
                            write.shipment_details,
                        ),
                    )
                    return "INSERTED"

        return await self._run_retrying_deadlocks(
            operation, operation_name="upsert_return_tracking"
        )

    async def read_rma_ticket(self, session_id: str) -> dict[str, Any] | None:
        """One ticket with its items and tracking, or None."""

        def operation() -> dict[str, Any] | None:
            with self._read() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT t.ticket_id, t.session_id, t.status, t.external_reference,
                               t.clarification_request, t.return_reference, t.created_at,
                               t.updated_at, r.order_reference, r.customer_reference
                        FROM integration.return_support_ticket AS t
                        LEFT JOIN dbo.return_requests AS r ON r.session_id = t.session_id
                        WHERE t.session_id=%s
                        """,
                        (session_id,),
                    )
                    ticket = cursor.fetchone()
                    if ticket is None:
                        return None
                    cursor.execute(
                        """
                        SELECT return_item_id, order_line_id, product_id, quantity,
                               reason_code, item_status
                        FROM dbo.return_items WHERE session_id=%s ORDER BY return_item_id
                        """,
                        (session_id,),
                    )
                    items = [dict(row) for row in cursor.fetchall()]
                    reference = ticket.get("return_reference")
                    tracking: list[dict[str, Any]] = []
                    if reference:
                        cursor.execute(
                            """
                            SELECT tracking_id, tracking_reference, tracking_type, carrier_code,
                                   tracking_status, event_at, shipment_details
                            FROM dbo.return_tracking WHERE return_reference=%s
                            ORDER BY event_at DESC
                            """,
                            (reference,),
                        )
                        tracking = [dict(row) for row in cursor.fetchall()]
                    return {"ticket": dict(ticket), "items": items, "tracking": tracking}

        return await self._run(operation)

    async def list_rma_tickets(self, limit: int) -> list[dict[str, Any]]:
        """The support queue, newest first."""

        def operation() -> list[dict[str, Any]]:
            with self._read() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT TOP (%s)
                               t.ticket_id, t.session_id, t.status, t.external_reference,
                               t.clarification_request, t.return_reference, t.created_at,
                               t.updated_at, r.order_reference, r.customer_reference
                        FROM integration.return_support_ticket AS t
                        LEFT JOIN dbo.return_requests AS r ON r.session_id = t.session_id
                        ORDER BY t.updated_at DESC
                        """,
                        (limit,),
                    )
                    return [dict(row) for row in cursor.fetchall()]

        return await self._run(operation)

    async def set_rma_ticket_status(
        self, session_id: str, status: str, clarification: str | None
    ) -> bool:
        """Move a ticket's status. False when no such ticket exists."""

        def operation() -> bool:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE integration.return_support_ticket WITH (UPDLOCK, SERIALIZABLE)
                        SET status=%s, clarification_request=%s, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        SELECT @@ROWCOUNT;
                        """,
                        (status, clarification, session_id),
                    )
                    row = cursor.fetchone()
                    return bool(row and int(row[0]))

        return await self._run_retrying_deadlocks(operation, operation_name="set_rma_ticket_status")

    async def reserve_and_assign_handling_unit(
        self,
        session: ReturnSessionView,
        *,
        handling_unit_id: str,
        return_reference: str,
        bay_id: str,
        warehouse_id: str,
        required_capacity: int,
        actor_id: str,
        reservation_minutes: int = 60,
    ) -> tuple[str, str]:
        """Atomically reserve platform bay capacity and create one assignment."""
        if required_capacity < 1:
            raise ValueError("required_capacity must be at least one")
        reservation_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"bay-reservation:{return_reference}:{handling_unit_id}")
        )
        assignment_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"bay-assignment:{return_reference}:{handling_unit_id}")
        )

        def operation() -> tuple[str, str]:
            with self._transaction() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT configuration.bay_id, configuration.warehouse_id,
                               configuration.active,
                               COALESCE(configuration.max_handling_unit_count,
                                        configuration.max_package_count) AS max_capacity,
                               COALESCE(reserved.reserved_capacity, 0) AS reserved_capacity
                        FROM platform.bay_configuration AS configuration WITH (UPDLOCK, HOLDLOCK)
                        OUTER APPLY (
                            SELECT SUM(reservation.reserved_capacity) AS reserved_capacity
                            FROM platform.bay_reservation AS reservation WITH (HOLDLOCK)
                            WHERE reservation.bay_id = configuration.bay_id
                              AND reservation.status IN ('RESERVED','ASSIGNED')
                              AND reservation.expires_at > SYSUTCDATETIME()
                              AND reservation.reservation_id <> %s
                        ) AS reserved
                        WHERE configuration.bay_id = %s
                          AND configuration.warehouse_id = %s;
                        """,
                        (reservation_id, bay_id, warehouse_id),
                    )
                    row = cursor.fetchone()
                    if row is None or not bool(row["active"]):
                        raise RuntimeError("Selected bay is missing or inactive.")
                    available = int(row["max_capacity"] or 0) - int(row["reserved_capacity"] or 0)
                    if available < required_capacity:
                        raise RuntimeError("Selected bay has insufficient available capacity.")
                    cursor.execute(
                        """
                        IF NOT EXISTS (
                            SELECT 1 FROM platform.bay_reservation
                            WHERE reservation_id = %s
                        )
                        INSERT INTO platform.bay_reservation (
                            reservation_id, return_reference, session_id,
                            handling_unit_id, warehouse_id, bay_id,
                            reserved_capacity, status, expires_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,'ASSIGNED',
                            DATEADD(MINUTE,%s,SYSUTCDATETIME())
                        );
                        """,
                        (
                            reservation_id,
                            reservation_id,
                            return_reference,
                            session.id,
                            handling_unit_id,
                            warehouse_id,
                            bay_id,
                            required_capacity,
                            reservation_minutes,
                        ),
                    )
                    first_item = session.itemReferences[0]
                    cursor.execute(
                        """
                        IF NOT EXISTS (
                            SELECT 1 FROM platform.bay_assignment
                            WHERE return_reference = %s AND handling_unit_id = %s
                        )
                        INSERT INTO platform.bay_assignment (
                            assignment_id, return_reference, sales_order_number,
                            order_line_id, item_number, package_count,
                            warehouse_id, bay_id, status,
                            confirmed_by_associate_id, confirmed_at,
                            reservation_id, handling_unit_id,
                            physical_receipt_confirmed, assignment_reason,
                            staged_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,'STAGED',
                            %s,SYSUTCDATETIME(),%s,%s,1,
                            'AGENT_RECOMMENDED_AND_HUMAN_CONFIRMED',SYSUTCDATETIME()
                        );
                        """,
                        (
                            return_reference,
                            handling_unit_id,
                            assignment_id,
                            return_reference,
                            session.orderReference,
                            first_item,
                            first_item,
                            required_capacity,
                            warehouse_id,
                            bay_id,
                            actor_id,
                            reservation_id,
                            handling_unit_id,
                        ),
                    )
            return reservation_id, assignment_id

        return await self._run(operation)
