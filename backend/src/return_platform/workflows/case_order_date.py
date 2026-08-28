"""The confirmed order's own date, read for the policy gate (3A.7 step 1).

`case_policy_facts` is pure by design -- it resolves the evaluator's input from
a list of dictionaries and performs no IO, which is what makes the admission
rule testable without a platform. Reading the sales document is IO, so it lives
here, on the activity side of the same boundary `business_calendar.py` draws
between resolving a value and using one.

**Why the order and not the associate.** The conversation captures
`approximate_purchase_date`, and `case_policy_facts` refuses to read it as
`purchase_date`: a window boundary decided from a date somebody described as
approximate is a boundary nobody can defend. The order does not have that
problem. It is the authoritative record of when the purchase happened, there is
nothing for the associate to state, and every order in the reference extract
carries the field. `CQ363350` is dated 2025-10-14 in the source and the window
is counted from there.

**Which field.** Whichever ones the active release binds in
`source_resolution.order_date_paths` -- no physical path is written in this
module, for the reason `operations/order_lines/source.py` gives for the same
decision. The shipped release binds `salesHdr.salesHdrData.orderDate` and
nothing else; `invoiceDate` and `entryDate` were examined against the extract
and rejected, and the reasoning is recorded beside the binding in
`config/returns/production.yaml`.

**Nothing here fails an evaluation.** Every unreadable case answers `None`, and
`None` means the window has no basis, which the evaluator reports as
`PURCHASE_DATE_UNKNOWN` and routes to a human. An order this cannot date must
never become an evaluation error -- that would turn a data gap into an
operational failure -- and it must never become a guessed date.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pymongo.asynchronous.collection import AsyncCollection

from return_platform.operations.seed_manifest import SOURCE_SALES_DATASET
from return_platform.workflows.case_policy_facts import (
    delivery_date_from_confirmed_order,
    purchase_date_from_confirmed_order,
)

__all__ = [
    "CaseOrderDateSource",
    "resolve_confirmed_order_dates",
    "resolve_confirmed_order_purchase_date",
]

logger = logging.getLogger("return_platform.workflows.case_order_date")

#: How the sales collection is keyed by order number. The same filter
#: `OperationalRepository.source_order` already uses, and the field the
#: collection is uniquely indexed on.
_ORDER_NUMBER_FILTER = "salesHdrEventData.orderId"


class CaseOrderDateSource(Protocol):
    """The two repository reads this needs, and nothing more.

    Structural so the resolution is exercisable without a Mongo client, and so
    that widening it later is a visible change to this protocol rather than an
    activity quietly acquiring a new dependency.
    """

    async def get_case(self, case_id: str) -> dict[str, Any] | None: ...

    async def source_dataset(self, dataset: str) -> AsyncCollection[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class ConfirmedOrderDates:
    """The two instants the window rules read, from one read of the order.

    Together rather than through two calls, because they come from the same
    document and a second read would double the query on the hot path of every
    evaluation -- and could answer from a different revision of the row.
    """

    purchase_date: datetime | None = None
    delivery_date: datetime | None = None


async def _confirmed_order_document(
    repository: CaseOrderDateSource, *, case_id: str
) -> Mapping[str, Any] | None:
    """The sales document for a case's confirmed order, or nothing.

    Every failure answers `None` and is logged, for the reason the module
    docstring gives: an order this cannot read must not become an evaluation
    error, and must never become a guessed value.
    """
    try:
        case = await repository.get_case(case_id)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("policy_case_unreadable", extra={"case_id": case_id}, exc_info=True)
        return None
    reference = None if case is None else case.get("confirmedOrderReference")
    if not isinstance(reference, str) or not reference.strip():
        # Discovery, not confirmation. There is no order to read yet.
        return None

    try:
        sales = await repository.source_dataset(SOURCE_SALES_DATASET)
        document = await sales.find_one({_ORDER_NUMBER_FILTER: reference.strip()})
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning(
            "policy_order_unreadable",
            extra={"case_id": case_id, "order_reference": reference},
            exc_info=True,
        )
        return None

    if not isinstance(document, Mapping):
        logger.info(
            "policy_order_not_in_source",
            extra={"case_id": case_id, "order_reference": reference},
        )
        return None
    return document


async def resolve_confirmed_order_dates(
    repository: CaseOrderDateSource,
    *,
    case_id: str,
    order_date_paths: Sequence[str],
    delivery_proof: Any,
) -> ConfirmedOrderDates:
    """The purchase and delivery instants for one case's confirmed order.

    The delivery half is what the platform could not answer at all before: the
    return-claim window read `delivery_date` off the fact log, nothing wrote it
    there, and so a delivery claim had no reporting deadline. The proof is on
    the order all along -- a completed carrier route and a captured signature,
    on an order that was driven rather than collected -- and
    `delivery_date_from_confirmed_order` is the pure half of reading it.
    """
    document = await _confirmed_order_document(repository, case_id=case_id)
    if document is None:
        return ConfirmedOrderDates()
    purchase = (
        purchase_date_from_confirmed_order(document, paths=order_date_paths)
        if order_date_paths
        else None
    )
    if purchase is None:
        logger.info("policy_order_carries_no_date", extra={"case_id": case_id})
    delivery = delivery_date_from_confirmed_order(document, proof=delivery_proof)
    return ConfirmedOrderDates(purchase_date=purchase, delivery_date=delivery)


async def resolve_confirmed_order_purchase_date(
    repository: CaseOrderDateSource,
    *,
    case_id: str,
    order_date_paths: Sequence[str],
) -> datetime | None:
    """The purchase date for one case's confirmed order, or `None`.

    `None` for every reason a date might not be available -- the release binds
    no path, the case confirmed no order, the source holds no such order, the
    order carries no date, or the read failed. They are different situations and
    they are logged differently, but they reach the evaluator identically,
    because "we cannot date this purchase" is one answer however it arose and
    the evaluator's response to it is to ask a human.

    A failed read is logged and swallowed rather than raised. The alternative is
    an `EVALUATION_FAILED` on a case whose only defect is that its order is not
    in the extract, which parks the return for a supervisor with nothing for
    them to do.
    """
    if not order_date_paths:
        logger.debug("policy_order_date_unbound", extra={"case_id": case_id})
        return None

    try:
        case = await repository.get_case(case_id)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("policy_case_unreadable", extra={"case_id": case_id}, exc_info=True)
        return None
    reference = None if case is None else case.get("confirmedOrderReference")
    if not isinstance(reference, str) or not reference.strip():
        # Discovery, not confirmation. There is no order to date yet.
        return None

    try:
        sales = await repository.source_dataset(SOURCE_SALES_DATASET)
        document = await sales.find_one({_ORDER_NUMBER_FILTER: reference.strip()})
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning(
            "policy_order_date_unreadable",
            extra={"case_id": case_id, "order_reference": reference},
            exc_info=True,
        )
        return None

    if not isinstance(document, Mapping):
        logger.info(
            "policy_order_not_in_source",
            extra={"case_id": case_id, "order_reference": reference},
        )
        return None

    resolved = purchase_date_from_confirmed_order(document, paths=order_date_paths)
    if resolved is None:
        logger.info(
            "policy_order_carries_no_date",
            extra={"case_id": case_id, "order_reference": reference},
        )
    return resolved
