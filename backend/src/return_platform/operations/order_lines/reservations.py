"""The quantity reservation: its states, its legal transitions, its storage.

Plan sect. 12.3 freezes four states and forbids three transitions. This module
is where both statements live, and it is deliberately the *only* place either
one is written down -- a second copy of "ACTIVE may become CONSUMED" is a second
thing that can disagree with the conditional update that enforces it.

```text
ACTIVE      selection persisted, quantity held
CONSUMED    an RMA authorized the quantity
RELEASED    case cancelled/rejected/expired, or the selection changed
EXPIRED     TTL elapsed before authorization
```

**Nothing here reads or writes Mongo.** The document shape, the state machine
and the index definitions are here; the transactional writers that apply them
are on `CaseRepository`, because a reservation write must share a transaction
with the case return item it holds quantity for and with the revision bump both
of them owe (plan sect. 6.5), and `_in_transaction` / `bump_case_revision` are
over there. Splitting it the other way would have meant a reservation module
that had to import the case aggregate to be able to write anything.

**Why `ACTIVE` is the only state a transition may start from.** Every legal
edge below leaves `ACTIVE`, so every enforcing update filters on
`state: ACTIVE` and, where the plan says so, on `expiresAt`. That makes the
three forbidden transitions -- `EXPIRED -> CONSUMED`, `RELEASED -> CONSUMED`,
`CONSUMED -> EXPIRED` -- unreachable by construction rather than by a check
somebody has to remember: a document that is not `ACTIVE` matches no filter, the
update reports zero matched, and the caller is told it lost rather than being
allowed to overwrite a settled outcome.

**Expiry is a read-side fact before it is a write.** `is_held` treats an
`ACTIVE` reservation whose `expiresAt` has passed as not held, so a hold cannot
leak the quantity permanently even if no sweep ever runs. The sweep exists to
settle the record, not to make the arithmetic correct.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from return_platform.operations.models import normalize_utc_datetime

__all__ = [
    "ORDER_LINE_RESERVATIONS_COLLECTION",
    "RESERVATION_LEDGER_COLLECTION",
    "IllegalReservationTransitionError",
    "LineAlreadyAuthorizedError",
    "LineSelection",
    "QuantityReservationExpiredError",
    "QuantityUnavailableError",
    "ReservationRelease",
    "ReservationState",
    "ReservationView",
    "SelectionOutcome",
    "ensure_order_line_reservation_indexes",
    "is_held",
    "ledger_id",
    "new_reservation_document",
    "read_reservation",
    "reservation_transition_is_legal",
]

#: One document per hold. Named for what it holds rather than for the case that
#: holds it: the availability read is by order line across every case, and a
#: collection named after the case would have to be scanned to answer it.
ORDER_LINE_RESERVATIONS_COLLECTION: Final = "order_line_reservations"

#: One document per order line, and it carries **no quantity**.
#:
#: A transaction's reads take no locks, so two associates can each read "2
#: available" inside their own transaction and each commit a reservation for 2 --
#: both transactions are internally consistent and the line is oversold. The
#: writers therefore increment a per-line token *before* they read, which makes
#: the two transactions collide on one document; MongoDB aborts the loser as a
#: transient error, `with_transaction` re-runs the whole callback, and the
#: re-run reads the winner's committed reservation and refuses.
#:
#: The token is deliberately not a cached `reservedQuantity`. A number here
#: would be a second answer to "how much is held", maintained by hand beside the
#: reservations that are the real answer, and the two would drift the first time
#: a writer failed between them. Serialization is all this collection is for.
RESERVATION_LEDGER_COLLECTION: Final = "order_line_reservation_ledger"


class ReservationState(StrEnum):
    """Plan sect. 12.3, verbatim and complete. No fifth member."""

    #: The selection is persisted and the quantity is held for this case.
    ACTIVE = "ACTIVE"
    #: An RMA authorized the quantity. Terminal, and the reason the
    #: authorization mutation and this transition share one transaction.
    CONSUMED = "CONSUMED"
    #: The case was cancelled, rejected or expired, or the selection changed.
    RELEASED = "RELEASED"
    #: The TTL elapsed before anything authorized the hold.
    EXPIRED = "EXPIRED"


class ReservationRelease(StrEnum):
    """Why a hold was released. Recorded, never inferred.

    `SELECTION_CHANGED` and `CASE_CLOSED` are the same state and very different
    events, and an operator reconciling a line's history cannot tell them apart
    from the state alone.
    """

    SELECTION_CHANGED = "SELECTION_CHANGED"
    SELECTION_WITHDRAWN = "SELECTION_WITHDRAWN"
    CASE_CLOSED = "CASE_CLOSED"


#: The state machine, as a graph. Every edge starts at `ACTIVE`; the three the
#: plan forbids are absent because nothing else has any outgoing edge at all.
_LEGAL_TRANSITIONS: Final[Mapping[ReservationState, frozenset[ReservationState]]] = {
    ReservationState.ACTIVE: frozenset(
        {
            ReservationState.CONSUMED,
            ReservationState.RELEASED,
            ReservationState.EXPIRED,
        }
    ),
    ReservationState.CONSUMED: frozenset(),
    ReservationState.RELEASED: frozenset(),
    ReservationState.EXPIRED: frozenset(),
}


def reservation_transition_is_legal(current: ReservationState, target: ReservationState) -> bool:
    """Whether `current -> target` is an edge of the frozen state machine.

    A no-op (`current is target`) is **not** legal. Re-asserting a state looks
    harmless and is how a second RMA comes to believe it consumed a hold the
    first one already consumed; the conditional updates report "you lost" for
    exactly that case, and this function must agree with them.
    """
    return target in _LEGAL_TRANSITIONS[current]


class QuantityUnavailableError(RuntimeError):
    """A selection asked for more than the line can still give.

    Carries the recomputed figures so the route can answer `409
    QUANTITY_UNAVAILABLE` with something the client can re-render from, rather
    than telling it to guess (plan sect. 12.4).
    """

    def __init__(self, *, order_reference: str, unavailable: Mapping[str, int]) -> None:
        self.order_reference = order_reference
        #: line reference -> the quantity that *is* still returnable, recomputed
        #: inside the transaction that refused the write.
        self.unavailable = dict(unavailable)
        detail = ", ".join(
            f"{line}: {quantity} returnable" for line, quantity in sorted(self.unavailable.items())
        )
        super().__init__(f"order {order_reference} cannot satisfy the selection ({detail})")


class QuantityReservationExpiredError(RuntimeError):
    """An authorization lost the race for the hold it meant to consume.

    Raised when the conditional `ACTIVE and not expired` update matches nothing,
    which is the expiry sweep or a competing authorization having got there
    first. Plan sect. 12.3: losing this race must not authorize.
    """

    def __init__(self, reservation_id: str, *, state: ReservationState | None) -> None:
        self.reservation_id = reservation_id
        #: What the reservation is now, if it still exists. `None` means it does
        #: not, which a caller reports the same way and must not treat as a hold.
        self.state = state
        super().__init__(
            f"reservation {reservation_id} is no longer ACTIVE "
            f"(state {state.value if state is not None else 'ABSENT'})"
        )


class IllegalReservationTransitionError(RuntimeError):
    """A caller asked for an edge the frozen state machine does not have."""

    def __init__(self, current: ReservationState, target: ReservationState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"{current.value} -> {target.value} is not a legal reservation transition")


class LineAlreadyAuthorizedError(RuntimeError):
    """The selection names a line an RMA on this case already covers.

    Not a quantity problem, so not a `QUANTITY_UNAVAILABLE`: the associate is
    trying to re-select something Support has already authorized, and the answer
    is that the selection is no longer theirs to edit rather than that the line
    is short. Editing it would also collide with the unique
    `(caseId, orderLineId)` index, which is the storage saying the same thing.
    """

    def __init__(self, case_id: str, order_line_references: Mapping[str, str]) -> None:
        self.case_id = case_id
        #: line reference -> the return record that covers it.
        self.order_line_references = dict(order_line_references)
        lines = ", ".join(sorted(self.order_line_references))
        super().__init__(f"case {case_id} has authorized RMAs covering line(s) {lines}")


@dataclass(frozen=True, slots=True)
class LineSelection:
    """One line the associate is naming, with how many of it and in what state.

    `reason` and `condition` are carried through to the case return item and are
    not validated against a catalogue *here*. Plan sect. 12.4 puts both
    vocabularies in the return configuration, and the route checks them against
    the active release (`selection_vocabulary`) before building this -- which is
    the only layer that holds a release. This module holds no configuration by
    design, so a check written here could only be a check against a literal.
    """

    order_line_reference: str
    quantity: int
    reason: str | None = None
    condition: str | None = None
    package_reference: str | None = None
    #: Resolved by the route from the source order line, never from the request
    #: body. A client that could name the product on a line would be able to
    #: record a return against a product the order does not contain.
    product_reference: str | None = None


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    """What a selection write did, including when it did nothing.

    `changed` is not cosmetic. Plan sect. 6.5 requires the case revision to move
    with a write that changes the projection and, just as firmly, *not* to move
    for one that does not: a re-submitted identical selection that bumped would
    invalidate every client's cache for a screen nobody touched.
    """

    case_id: str
    revision: int
    changed: bool
    items: tuple[Mapping[str, Any], ...]
    reservations: tuple[ReservationView, ...]


class ReservationView:
    """One reservation, as the arithmetic and the lifecycle need it.

    A small read model rather than the raw document: the availability rules ask
    three questions of a reservation -- whose case, which line, how much, and is
    it still held -- and passing dictionaries around would let a rule read
    `reservation["quantity"]` off a document that turned out to be a ledger row.
    """

    __slots__ = (
        "case_id",
        "expires_at",
        "order_line_reference",
        "order_reference",
        "quantity",
        "reservation_id",
        "state",
        "tenant_id",
    )

    def __init__(
        self,
        *,
        reservation_id: str,
        tenant_id: str,
        case_id: str,
        order_reference: str,
        order_line_reference: str,
        quantity: int,
        state: ReservationState,
        expires_at: datetime,
    ) -> None:
        self.reservation_id = reservation_id
        self.tenant_id = tenant_id
        self.case_id = case_id
        self.order_reference = order_reference
        self.order_line_reference = order_line_reference
        self.quantity = quantity
        self.state = state
        self.expires_at = expires_at

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"ReservationView(reservation_id={self.reservation_id!r}, case_id={self.case_id!r}, "
            f"line={self.order_line_reference!r}, quantity={self.quantity}, "
            f"state={self.state.value})"
        )


def is_held(reservation: ReservationView, *, now: datetime) -> bool:
    """Whether this reservation still holds its quantity at `now`.

    `ACTIVE` **and** unexpired. An `ACTIVE` document whose TTL has passed holds
    nothing: the sweep that will mark it `EXPIRED` is a bookkeeping step, and a
    read that waited for it would leak the quantity for as long as the sweep was
    down. Reading the deadline rather than the sweep is what makes the leak
    impossible instead of merely unlikely.
    """
    return reservation.state is ReservationState.ACTIVE and reservation.expires_at > now


def ledger_id(*, tenant_id: str, order_reference: str, order_line_reference: str) -> str:
    """The serialization token's id.

    Tenant-scoped like everything else that touches an order: two tenants whose
    source systems both use order number `CW273354` are two different orders,
    and one token for both would serialize writers who never contend.
    """
    return f"{tenant_id}|{order_reference}|{order_line_reference}"


def read_reservation(document: Mapping[str, Any]) -> ReservationView | None:
    """A stored document as a `ReservationView`, or `None` if it is not one.

    Unreadable documents are dropped rather than defaulted. A reservation whose
    state does not parse is not "probably ACTIVE" -- defaulting it either way
    would either invent a hold or release one, and both are worse than a
    reservation the arithmetic declines to count while an operator looks at it.
    """
    try:
        state = ReservationState(str(document["state"]))
    except (KeyError, ValueError):
        return None
    expires_at = document.get("expiresAt")
    if not isinstance(expires_at, datetime):
        return None
    quantity = document.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        return None
    for key in ("reservationId", "tenantId", "caseId", "orderReference", "orderLineReference"):
        if not isinstance(document.get(key), str) or not str(document[key]).strip():
            return None
    return ReservationView(
        reservation_id=str(document["reservationId"]),
        tenant_id=str(document["tenantId"]),
        case_id=str(document["caseId"]),
        order_reference=str(document["orderReference"]),
        order_line_reference=str(document["orderLineReference"]),
        quantity=quantity,
        state=state,
        # BSON has no timezone, so a deadline written as aware UTC reads back
        # naive. Normalized here, at the one place a stored reservation becomes
        # a `ReservationView`, because `is_held` compares it against an aware
        # `now` and a naive value does not raise a wrong answer -- it raises
        # `TypeError`, in the middle of an availability read.
        expires_at=normalize_utc_datetime(expires_at),
    )


def new_reservation_document(
    *,
    reservation_id: str,
    tenant_id: str,
    case_id: str,
    order_reference: str,
    order_line_reference: str,
    quantity: int,
    now: datetime,
    ttl_seconds: int,
) -> dict[str, Any]:
    """A fresh `ACTIVE` hold.

    `expiresAt` is computed from the TTL the caller was handed, which comes from
    `return_case.item_reservation_ttl_seconds` in the active release. The
    deadline is stamped on the document rather than derived on read for the same
    reason the workflow keeps its own timings: a release that shortens the TTL
    must not retroactively expire holds an associate is still working on.
    """
    return {
        "_id": reservation_id,
        "reservationId": reservation_id,
        "tenantId": tenant_id,
        "caseId": case_id,
        "orderReference": order_reference,
        "orderLineReference": order_line_reference,
        "quantity": quantity,
        "state": ReservationState.ACTIVE.value,
        "expiresAt": now + timedelta(seconds=ttl_seconds),
        "consumedByReturnRecordId": None,
        "releaseReason": None,
        "createdAt": now,
        "updatedAt": now,
    }


async def ensure_order_line_reservation_indexes(
    database: AsyncDatabase[dict[str, object]],
) -> None:
    """The four indexes the lifecycle depends on.

    Called from `OperationalRepository.ensure_indexes` so index creation stays
    in one place, and defined here so each constraint sits beside the rule it
    encodes -- the arrangement `ensure_support_event_indexes` already uses.

    The unique partial index is the load-bearing one. **One `ACTIVE` hold per
    (case, line)**, which is what makes "a case editing its own reservation from
    1 to 2" a well-defined operation: there is exactly one document to exclude
    from the availability sum and exactly one to release. Partial rather than
    plain unique, because a case that has released and re-reserved a line five
    times has five settled documents on it and every one of them is a legitimate
    part of the audit trail.
    """
    reservations = database[ORDER_LINE_RESERVATIONS_COLLECTION]
    await reservations.create_index("reservationId", unique=True)
    await reservations.create_index(
        [("caseId", ASCENDING), ("orderLineReference", ASCENDING)],
        unique=True,
        name="reservation_active_case_line_unique",
        partialFilterExpression={"state": ReservationState.ACTIVE.value},
    )
    # The availability read: every hold on one line of one order, in one tenant.
    await reservations.create_index(
        [
            ("tenantId", ASCENDING),
            ("orderReference", ASCENDING),
            ("orderLineReference", ASCENDING),
            ("state", ASCENDING),
        ],
        name="reservation_line_availability",
    )
    # The expiry sweep's predicate. State first: the sweep asks for `ACTIVE`
    # holds past their deadline, and the overwhelming majority of documents in a
    # mature collection are settled ones it must not have to look at.
    await reservations.create_index(
        [("state", ASCENDING), ("expiresAt", ASCENDING)],
        name="reservation_expiry_sweep",
    )
