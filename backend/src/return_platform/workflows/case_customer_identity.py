"""Who the confirmed order belongs to, read from the source that states it.

## Why this exists

`project_customer` builds the case's customer block from two facts,
`customer_name` and `customer_id`. Until now their only writer was the reasoning
model, through `observed_facts` -- and the model **cannot see either of them**.
`redact_payload` masks `customer_name`, `ship_to_name` and `ship_to_phone` before
a candidate row reaches a prompt, which is exactly right and is why the whole
mechanism was empty: the platform resolved the order, knew whose it was, and
recorded nothing, so every confirmed case reported `customer: null`.

The associate cannot see it either, then, and neither can Support -- which is a
handoff about a customer that does not name the customer.

## Where it comes from

The confirmed order's own sales document, through the paths the active release
binds in `source_resolution.customer_name_paths` and `customer_id_paths`. No
physical path is written here, for the reason `case_order_date.py` gives for the
same decision: a literal would be correct only until the next release re-binds
the field, and the failure would be silent.

## How it is recorded

As case facts, with `FactChannel.SYSTEM` and `FactAcquisition.OBSERVED`. Both
halves matter and neither is cosmetic:

* it is **not** `CHANNEL_A` -- no associate said it, and attributing it to one
  would make a source read look like something a human vouched for;
* it is **not** `STATED` -- `OBSERVED` is precisely "a source system reported
  this", which is what happened.

So a reader of the fact log can tell a customer name the platform read from one
an associate typed, which is the distinction that matters when the two disagree.

## Failure

Every unreadable case answers "nothing", the same way the order-date resolver
does. A case whose order is not in the extract must not become a workflow
failure: the return is still real, Support can still be asked, and the handoff
says the customer is unavailable rather than inventing one.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pymongo.asynchronous.collection import AsyncCollection

from return_platform.operations.seed_manifest import SOURCE_SALES_DATASET

__all__ = [
    "CaseCustomerIdentity",
    "CaseCustomerSource",
    "resolve_confirmed_order_customer",
]

logger = logging.getLogger("return_platform.workflows.case_customer_identity")

#: How the sales collection is keyed by order number -- the same filter
#: `case_order_date` uses, and the field the collection is uniquely indexed on.
_ORDER_NUMBER_FILTER = "salesHdrEventData.orderId"

_MISSING = object()


@dataclass(frozen=True, slots=True)
class CaseCustomerIdentity:
    """What the source says about the customer on one confirmed order.

    Both fields nullable and neither defaulted. An order whose document carries
    no customer name is reported as carrying none; substituting the account id,
    or the ship-to name, would put a different thing under the same label.
    """

    customer_name: str | None
    customer_id: str | None
    #: The account the order sits on, and how to reach the customer on it.
    #:
    #: Read from the same document as the name, because a Support request that
    #: names a customer nobody can telephone is a request whose first action is
    #: to look them up. The order carries all of it -- `customer.address[]` is
    #: what `contact_point` is exploded from -- so this is a read the platform
    #: was already making and throwing away.
    #:
    #: Each independently nullable. An order with a phone and no email says so,
    #: and no field is ever filled from a neighbour.
    account: str | None = None
    phone: str | None = None
    email: str | None = None

    @property
    def empty(self) -> bool:
        return all(
            value is None
            for value in (
                self.customer_name,
                self.customer_id,
                self.account,
                self.phone,
                self.email,
            )
        )


class CaseCustomerSource(Protocol):
    """The two repository reads this needs, and nothing more."""

    async def get_case(self, case_id: str) -> dict[str, Any] | None: ...

    async def source_dataset(self, dataset: str) -> AsyncCollection[dict[str, object]]: ...


def _resolve(record: Any, path: str) -> Any:
    """The value at a dotted path, walking arrays as alternatives.

    A list is searched rather than indexed: `customer.address` holds one row per
    contact channel, and the phone is on whichever of them carries one. Taking
    `[0]` would report "no phone" for a customer whose first address row happens
    to be the one without it -- which, in the reference corpus, is most of them.
    The first row that answers wins, which is the same preference rule the path
    list itself expresses.
    """
    current: Any = record
    for index, segment in enumerate(path.split(".")):
        if isinstance(current, list):
            remaining = ".".join(path.split(".")[index:])
            for item in current:
                found = _resolve(item, remaining)
                if found is not _MISSING and found is not None:
                    return found
            return _MISSING
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _first_text(document: Mapping[str, Any], paths: Sequence[str]) -> str | None:
    """The first bound path that actually carries a value.

    Preference order, not a merge: the paths are alternatives for one field, and
    a release lists them because different source systems put it in different
    places.
    """
    for path in paths:
        value = _resolve(document, path)
        if value is _MISSING or value is None or isinstance(value, bool):
            continue
        rendered = str(value).strip()
        if rendered:
            return rendered
    return None


async def resolve_confirmed_order_customer(
    repository: CaseCustomerSource,
    *,
    case_id: str,
    customer_name_paths: Sequence[str],
    customer_id_paths: Sequence[str],
    account_paths: Sequence[str] = (),
    phone_paths: Sequence[str] = (),
    email_paths: Sequence[str] = (),
) -> CaseCustomerIdentity:
    """The customer on one case's confirmed order, or an empty identity.

    Empty for every reason the answer might not be available -- no bound paths,
    no confirmed order, no such order in the source, no customer on the document,
    or a failed read. They are logged apart and they mean the same thing to the
    caller, which is that this case cannot name its customer.
    """
    if not customer_name_paths and not customer_id_paths and not account_paths:
        logger.debug("case_customer_unbound", extra={"case_id": case_id})
        return CaseCustomerIdentity(None, None)

    try:
        case = await repository.get_case(case_id)
    except Exception:  # noqa: BLE001 - see the module docstring
        logger.warning("case_customer_case_unreadable", extra={"case_id": case_id}, exc_info=True)
        return CaseCustomerIdentity(None, None)

    reference = None if case is None else case.get("confirmedOrderReference")
    if not isinstance(reference, str) or not reference.strip():
        # Discovery, not confirmation. There is no order to read a customer off.
        return CaseCustomerIdentity(None, None)

    try:
        sales = await repository.source_dataset(SOURCE_SALES_DATASET)
        document = await sales.find_one({_ORDER_NUMBER_FILTER: reference.strip()})
    except Exception:  # noqa: BLE001 - see the module docstring
        logger.warning(
            "case_customer_order_unreadable",
            extra={"case_id": case_id, "order_reference": reference},
            exc_info=True,
        )
        return CaseCustomerIdentity(None, None)

    if not isinstance(document, Mapping):
        logger.info(
            "case_customer_order_not_in_source",
            extra={"case_id": case_id, "order_reference": reference},
        )
        return CaseCustomerIdentity(None, None)

    identity = CaseCustomerIdentity(
        customer_name=_first_text(document, customer_name_paths),
        customer_id=_first_text(document, customer_id_paths),
        account=_first_text(document, account_paths),
        phone=_first_text(document, phone_paths),
        email=_first_text(document, email_paths),
    )
    if identity.empty:
        logger.info(
            "case_customer_not_on_order",
            extra={"case_id": case_id, "order_reference": reference},
        )
    return identity
