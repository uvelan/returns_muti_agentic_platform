"""Case-bound order lines, returnable quantity, and the reservations that hold it.

Three modules, split by what can be tested without a database:

* `source.py` -- the confirmed order's lines, projected from the source document
  through the active schema's own `order_line` declaration. One indexed read.
* `availability.py` -- pure arithmetic. The four terms of plan sect. 12.2 and
  the single place the boundary between them is defined.
* `reservations.py` -- the frozen four-state lifecycle, its legal transitions,
  its document shape and its indexes. No IO.

The transactional writers that apply all three live on `CaseRepository`: a
reservation write shares a transaction with the case return item it holds
quantity for and with the revision bump both of them owe (plan sect. 6.5), and
`_in_transaction` / `bump_case_revision` are over there.
"""

from __future__ import annotations

from return_platform.operations.order_lines.availability import (
    CaseLineHolding,
    DataInconsistency,
    HoldingKind,
    LineAvailability,
    case_line_holdings,
    compute_line_availability,
    compute_order_line_availability,
)
from return_platform.operations.order_lines.product_attributes import resolve_product_colours
from return_platform.operations.order_lines.reservations import (
    ORDER_LINE_RESERVATIONS_COLLECTION,
    RESERVATION_LEDGER_COLLECTION,
    IllegalReservationTransitionError,
    LineAlreadyAuthorizedError,
    LineSelection,
    QuantityReservationExpiredError,
    QuantityUnavailableError,
    ReservationRelease,
    ReservationState,
    ReservationView,
    SelectionOutcome,
    ensure_order_line_reservation_indexes,
    is_held,
    ledger_id,
    new_reservation_document,
    read_reservation,
    reservation_transition_is_legal,
)
from return_platform.operations.order_lines.source import (
    ORDER_LINE_ENTITY_ID,
    OrderLineSchemaUnavailableError,
    SourceOrderLine,
    load_source_order_lines,
    project_source_order_lines,
)

__all__ = [
    "ORDER_LINE_ENTITY_ID",
    "ORDER_LINE_RESERVATIONS_COLLECTION",
    "RESERVATION_LEDGER_COLLECTION",
    "CaseLineHolding",
    "DataInconsistency",
    "HoldingKind",
    "IllegalReservationTransitionError",
    "LineAlreadyAuthorizedError",
    "LineAvailability",
    "LineSelection",
    "OrderLineSchemaUnavailableError",
    "QuantityReservationExpiredError",
    "QuantityUnavailableError",
    "ReservationRelease",
    "ReservationState",
    "ReservationView",
    "SelectionOutcome",
    "SourceOrderLine",
    "case_line_holdings",
    "compute_line_availability",
    "compute_order_line_availability",
    "ensure_order_line_reservation_indexes",
    "is_held",
    "ledger_id",
    "load_source_order_lines",
    "new_reservation_document",
    "project_source_order_lines",
    "read_reservation",
    "reservation_transition_is_legal",
    "resolve_product_colours",
]
