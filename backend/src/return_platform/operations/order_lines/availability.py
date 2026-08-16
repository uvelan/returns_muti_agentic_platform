"""Returnable quantity, computed once and honestly (plan sect. 12.2).

```text
returnableQuantity = orderedQuantity
                   - completedReturnQuantity
                   - openAuthorizedQuantity
                   - activeReservationQuantityFromOtherCases
```

**The boundary between the three subtrahends is defined here and nowhere else.**
The plan requires `completedReturnQuantity` and `openAuthorizedQuantity` to be
mutually exclusive; this module goes further and makes all three exclusive, by
partitioning on the *case* rather than on the record type:

> A case contributes to one order line through exactly one of two records -- the
> return item an RMA has been attached to, or the ACTIVE hold that stands in for
> it before one has. Where both exist the item wins, because an authorized
> quantity is gone whatever the hold says.

That is a partition of `(case, line)` pairs, and both halves of it are enforced
by a unique index: `(caseId, orderLineId)` on `operational_return_items`, and
the partial `(caseId, orderLineReference)` on `ACTIVE` reservations. So a case
cannot appear twice on one line, and double counting is not a rule that has to
be remembered -- it is a shape the storage will not hold.

Splitting the item half into *completed* and *open* then reads the case's
status through the frozen `ReturnCaseStatus` vocabulary, whose terminal sets are
already declared as a partition (`SUCCESSFUL_TERMINAL_STATUSES` and
`UNSUCCESSFUL_TERMINAL_STATUSES`) and whose mapping raises rather than defaults
for an unknown value. Classifying on `return_records.status` instead was the
obvious alternative and was rejected: that field is a free string written as
`DRAFT` by the repository and `ISSUED` by the workflow, with no declared
vocabulary and nothing that would fail if a new value appeared.

**A cancelled case gives the quantity back.** `UNSUCCESSFUL_TERMINAL_STATUSES`
-- rejected, cancelled, expired -- contributes to neither total, because the
line was never returned and nobody is waiting to return it.

**A negative result is never exposed and never silently clamped.** It surfaces
as `0` *with* a `DataInconsistency`, so the arithmetic being wrong is a thing an
operator can see rather than a `max(0, ...)` nobody ever reads.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from return_platform.operations.case_projection.status_mapping import (
    UnmappedCaseStatusError,
    project_case_status,
)
from return_platform.operations.case_projection.vocabulary import (
    SUCCESSFUL_TERMINAL_STATUSES,
    UNSUCCESSFUL_TERMINAL_STATUSES,
)
from return_platform.operations.order_lines.reservations import ReservationView, is_held

__all__ = [
    "CaseLineHolding",
    "DataInconsistency",
    "HoldingKind",
    "LineAvailability",
    "case_line_holdings",
    "compute_line_availability",
    "compute_order_line_availability",
]


class HoldingKind(StrEnum):
    """What one case is doing to one order line. Exactly one per (case, line)."""

    #: An RMA covers the quantity and the case finished successfully.
    COMPLETED_RETURN = "COMPLETED_RETURN"
    #: An RMA covers the quantity and the case is still running.
    OPEN_AUTHORIZED = "OPEN_AUTHORIZED"
    #: No RMA yet; an unexpired `ACTIVE` reservation holds the quantity.
    ACTIVE_RESERVATION = "ACTIVE_RESERVATION"
    #: An RMA covered the quantity and the case ended without returning it.
    #: Subtracted from nothing -- the units are back on the shelf.
    RELINQUISHED = "RELINQUISHED"


class DataInconsistency(StrEnum):
    """Why a line's arithmetic could not be trusted. Reported, never swallowed."""

    #: The source carries no ordered quantity for the line, so there is no
    #: total to subtract from. Nothing is returnable until somebody explains it.
    ORDERED_QUANTITY_UNKNOWN = "ORDERED_QUANTITY_UNKNOWN"
    #: More units are committed than were ever ordered. The plan's named case:
    #: exposed as `0` and flagged, never as a negative.
    COMMITMENTS_EXCEED_ORDERED_QUANTITY = "COMMITMENTS_EXCEED_ORDERED_QUANTITY"
    #: A stored item or reservation carries a quantity that cannot be read as a
    #: whole number. Counted as one unit -- the smallest a selected line can be
    #: -- so the count errs towards refusing rather than overselling, and
    #: flagged so the document gets looked at.
    HOLDING_QUANTITY_UNREADABLE = "HOLDING_QUANTITY_UNREADABLE"
    #: A case's persisted status has no projection. The case cannot be
    #: classified as finished or running, so its quantity is treated as still
    #: committed -- again the conservative direction -- and flagged.
    CASE_STATUS_UNREADABLE = "CASE_STATUS_UNREADABLE"


@dataclass(frozen=True, slots=True)
class CaseLineHolding:
    """One case's single contribution to one order line."""

    case_id: str
    quantity: int
    kind: HoldingKind
    #: Present when the holding could only be classified by erring towards
    #: "still committed". Propagated onto the line so the flag reaches the wire.
    inconsistency: DataInconsistency | None = None


@dataclass(frozen=True, slots=True)
class LineAvailability:
    """The four terms of plan sect. 12.2, and the answer they produce."""

    line_reference: str
    ordered_quantity: int | None
    completed_return_quantity: int
    open_authorized_quantity: int
    #: Held by *other* cases. This case's own hold is excluded, which is what
    #: lets a case edit its reservation from 1 to 2 without rejecting itself.
    active_reservation_quantity: int
    #: This case's own unexpired hold, reported rather than folded away so a
    #: client can render "2 of your 2 are already held by you".
    self_reserved_quantity: int
    returnable_quantity: int
    data_inconsistency: DataInconsistency | None


def _whole(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _holding_quantity(raw: Any) -> tuple[int, DataInconsistency | None]:
    quantity = _whole(raw)
    if quantity is None or quantity < 1:
        return 1, DataInconsistency.HOLDING_QUANTITY_UNREADABLE
    return quantity, None


def _classify_authorized(case_status: Any) -> tuple[HoldingKind, DataInconsistency | None]:
    """Where an RMA-covered quantity belongs, from the case's persisted status.

    `settlement=None` on purpose. The split it drives -- `COMPLETED` versus
    `COMPLETED_EXTERNAL_SETTLEMENT` -- is about *who produced the credit*, and
    both members are in `SUCCESSFUL_TERMINAL_STATUSES`, so the quantity lands in
    the same bucket either way. Loading a settlement projection to reach an
    answer that cannot change would make this read depend on a block no producer
    writes.
    """
    try:
        projected = project_case_status(case_status)
    except UnmappedCaseStatusError:
        return HoldingKind.OPEN_AUTHORIZED, DataInconsistency.CASE_STATUS_UNREADABLE
    if projected in SUCCESSFUL_TERMINAL_STATUSES:
        return HoldingKind.COMPLETED_RETURN, None
    if projected in UNSUCCESSFUL_TERMINAL_STATUSES:
        return HoldingKind.RELINQUISHED, None
    return HoldingKind.OPEN_AUTHORIZED, None


def case_line_holdings(
    *,
    line_reference: str,
    items: Sequence[Mapping[str, Any]],
    case_status_by_id: Mapping[str, Any],
    reservations: Sequence[ReservationView],
    now: datetime,
) -> tuple[CaseLineHolding, ...]:
    """The per-case partition, applied to one line.

    `items` are `operational_return_items` rows; only those carrying a `caseId`
    are considered, because a session-scoped item belongs to the legacy path and
    to no case. An item with no `returnRecordId` is **not** counted here: it has
    been named but no RMA covers it, so the quantity it stands for is held by
    the reservation beside it and counting both would be the double count this
    module exists to make impossible.

    Ordering is deterministic (by case id) so two equal inputs produce two equal
    outputs and a diff of the flags is readable.
    """
    authorized: dict[str, Mapping[str, Any]] = {}
    for item in items:
        case_id = item.get("caseId")
        if not isinstance(case_id, str) or not case_id.strip():
            continue
        if str(item.get("orderLineId") or item.get("orderLineReference") or "") != line_reference:
            continue
        record_id = item.get("returnRecordId")
        if not isinstance(record_id, str) or not record_id.strip():
            continue
        authorized[case_id] = item

    holdings: dict[str, CaseLineHolding] = {}
    for case_id, item in authorized.items():
        quantity, unreadable = _holding_quantity(item.get("quantity"))
        kind, unclassified = _classify_authorized(case_status_by_id.get(case_id))
        holdings[case_id] = CaseLineHolding(
            case_id=case_id,
            quantity=quantity,
            kind=kind,
            inconsistency=unreadable or unclassified,
        )

    for reservation in reservations:
        if reservation.order_line_reference != line_reference:
            continue
        if reservation.case_id in holdings:
            # The item wins. An authorized quantity is gone whatever the hold
            # says, and the hold should already be CONSUMED -- counting it again
            # would subtract the same units twice.
            continue
        if not is_held(reservation, now=now):
            continue
        quantity, unreadable = _holding_quantity(reservation.quantity)
        holdings[reservation.case_id] = CaseLineHolding(
            case_id=reservation.case_id,
            quantity=quantity,
            kind=HoldingKind.ACTIVE_RESERVATION,
            inconsistency=unreadable,
        )

    return tuple(holdings[case_id] for case_id in sorted(holdings))


def compute_line_availability(
    *,
    line_reference: str,
    ordered_quantity: int | None,
    holdings: Iterable[CaseLineHolding],
    viewing_case_id: str | None,
) -> LineAvailability:
    """The four terms and the answer, for one line.

    `viewing_case_id` is the case asking. Its own `ACTIVE_RESERVATION` is
    excluded from the subtraction and reported as `self_reserved_quantity`
    instead -- plan sect. 12.3's self-reservation exclusion, and the reason a
    case raising its own hold from 1 to 2 does not reject itself. Its own
    *authorized* quantity is not excluded: once an RMA covers a unit, that unit
    is gone from every case including the one that took it.
    """
    completed = 0
    open_authorized = 0
    other_reserved = 0
    self_reserved = 0
    flags: list[DataInconsistency] = []
    for holding in holdings:
        if holding.inconsistency is not None:
            flags.append(holding.inconsistency)
        if holding.kind is HoldingKind.COMPLETED_RETURN:
            completed += holding.quantity
        elif holding.kind is HoldingKind.OPEN_AUTHORIZED:
            open_authorized += holding.quantity
        elif holding.kind is HoldingKind.ACTIVE_RESERVATION:
            if viewing_case_id is not None and holding.case_id == viewing_case_id:
                self_reserved += holding.quantity
            else:
                other_reserved += holding.quantity
        # RELINQUISHED contributes to nothing, by definition.

    if ordered_quantity is None:
        return LineAvailability(
            line_reference=line_reference,
            ordered_quantity=None,
            completed_return_quantity=completed,
            open_authorized_quantity=open_authorized,
            active_reservation_quantity=other_reserved,
            self_reserved_quantity=self_reserved,
            returnable_quantity=0,
            data_inconsistency=DataInconsistency.ORDERED_QUANTITY_UNKNOWN,
        )

    remaining = ordered_quantity - completed - open_authorized - other_reserved
    inconsistency: DataInconsistency | None = None
    if remaining < 0:
        # Exposed as zero *and* flagged. A silent clamp here is the failure the
        # plan names by hand: the number would look ordinary and the impossible
        # state that produced it would never be investigated.
        inconsistency = DataInconsistency.COMMITMENTS_EXCEED_ORDERED_QUANTITY
        remaining = 0
    elif flags:
        # The line computed, but at least one holding had to be read
        # conservatively. The first flag is reported: they are ordered by the
        # deterministic holding order, so the same data always names the same one.
        inconsistency = flags[0]

    return LineAvailability(
        line_reference=line_reference,
        ordered_quantity=ordered_quantity,
        completed_return_quantity=completed,
        open_authorized_quantity=open_authorized,
        active_reservation_quantity=other_reserved,
        self_reserved_quantity=self_reserved,
        returnable_quantity=remaining,
        data_inconsistency=inconsistency,
    )


def compute_order_line_availability(
    *,
    ordered_by_line: Mapping[str, int | None],
    items: Sequence[Mapping[str, Any]],
    case_status_by_id: Mapping[str, Any],
    reservations: Sequence[ReservationView],
    now: datetime,
    viewing_case_id: str | None,
) -> dict[str, LineAvailability]:
    """Every line of one order, keyed by line reference.

    The one composition of the two functions above, so the read route and the
    writer's re-evaluation cannot come to compute availability differently. That
    is not a stylistic preference: the writer's whole purpose is to refuse what
    the read said was possible, and two implementations of "possible" would make
    the refusal arbitrary.

    `ordered_by_line` comes from the source projection and decides which lines
    exist. A commitment recorded against a line the order does not have is not
    invented into an extra row -- the order is the authority on its own lines,
    and a line nobody can point at in the source is not something an associate
    can be shown or sold.
    """
    return {
        line_reference: compute_line_availability(
            line_reference=line_reference,
            ordered_quantity=ordered_quantity,
            holdings=case_line_holdings(
                line_reference=line_reference,
                items=items,
                case_status_by_id=case_status_by_id,
                reservations=reservations,
                now=now,
            ),
            viewing_case_id=viewing_case_id,
        )
        for line_reference, ordered_quantity in ordered_by_line.items()
    }
