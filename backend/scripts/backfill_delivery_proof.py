"""Give the committed fixture the fields that prove an order was delivered.

`salesInv` decides delivery from five conditions, and the extract in
`fixtures/reference_dataset/` could answer only two of them:

    orderCode       IO (invoice open) or ID (invoice direct)   -- absent
    Trilogie file   ORDER                                      -- absent
    shipViaCode     not CPU / WCL / BO                         -- present
    fleetwiseStatus Completed                                  -- absent
    podSigTd        a proof-of-delivery signature timestamp    -- present

So nothing downstream could tell DELIVERED from SHIPPED, and the two fields
that *were* present said something misleading on their own: all fourteen
`podSigTd` values in the extract sat on `CPU`/`WCL` orders, which are counter
signatures at pick-up rather than proof of delivery. A reader taking `podSigTd`
alone as "delivered" would have called every one of them delivered.

**What this writes, and where.** The three missing fields go beside the ones
they belong with -- `orderCode` and `trilogieFile` on `salesHdrEventData`, next
to `orderStatus` and `docType`; `fleetwiseStatus` under
`salesHdrData.shipping`, next to `podSigTd` and `shipViaCode`.

    orderCode        IO when the order is invoiced, OO when it is still open.
                     ID is never written: the extract carries no direct-order
                     flag (`cmDirectFlag`, `directAutoInvFlag` and
                     `directFrtFlag` are false on all 100 orders), so choosing
                     which orders are direct would be invention rather than
                     de-identification.
    trilogieFile     ORDER on every document. They are all order headers; an
                     invoice-file record would be a different extract.
    fleetwiseStatus  Completed on the delivered cohort, InRoute on the rest of
                     the delivery-capable orders, and absent on pick-ups --
                     a counter pick-up has no DispatchTrack route to complete.
    podSigTd         Stamped on the delivered cohort from the order's own
                     commit date, in the extract's own
                     `HH:MM:SS MON DD YYYY` spelling. Existing values are never
                     overwritten.

**The cohort, and the one liberty this takes.** Delivery requires an invoice
code *and* a shipped ship-via, and the extract has exactly two orders that are
both (`OT` + `INVOICED`). Two is too thin to exercise a return window, so
`--delivered-target` converts invoiced **pick-up** orders to `OT`/`OUR TRUCK`
until the cohort reaches its size. Only invoiced orders are ever converted --
a `CALLCSR` order has not been delivered by anyone's reading, and rewriting one
would put a delivery signature on an order still waiting for a customer call.
Pass `--delivered-target 2` to take no liberty at all and keep the extract's
observed mix.

Deterministic: the cohort is chosen by a stable hash of the order id, so the
same file in produces the same file out, and a re-run is a no-op.

    python backend/scripts/backfill_delivery_proof.py --check
    python backend/scripts/backfill_delivery_proof.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATASET = BACKEND_ROOT / "fixtures" / "reference_dataset" / "salesInv1.json"

#: Ship-via codes that are collected rather than delivered. Straight from the
#: business rule: CPU is counter pick-up, WCL will-call, BO backorder.
PICKUP_CODES: frozenset[str] = frozenset({"CPU", "WCL", "BO"})

#: What a converted pick-up becomes. `OT` is already the extract's own delivery
#: ship-via, so the corpus gains no code it did not have.
DELIVERY_SHIP_VIA: tuple[str, str] = ("OT", "OUR TRUCK")

#: How many orders should end up DELIVERED. See the module docstring.
DEFAULT_DELIVERED_TARGET = 15

_MONTHS = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)


def _stable(order_id: str) -> int:
    """A repeatable draw for one order. Never `hash()`, which is salted."""
    return int(hashlib.sha256(order_id.encode("utf-8")).hexdigest()[:8], 16)


def _instant(value: Any) -> datetime | None:
    """A `{"$date": ...}` extended-JSON value as a datetime, or nothing."""
    if isinstance(value, dict):
        value = value.get("$date")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _signature_stamp(moment: datetime, order_id: str) -> str:
    """A POD timestamp in the extract's own spelling: `15:11:19 OCT 15 2025`.

    The clock time is drawn from the order id rather than fixed, so a corpus of
    signatures does not all read 09:00, and bounded to a working day: a proof of
    delivery at 03:00 is a data error everywhere except in a fixture nobody
    looked at.
    """
    draw = _stable(order_id)
    hour = 7 + draw % 11
    minute = (draw // 11) % 60
    second = (draw // 660) % 60
    return (
        f"{hour:02d}:{minute:02d}:{second:02d} "
        f"{_MONTHS[moment.month - 1]} {moment.day} {moment.year}"
    )


def _is_invoiced(order: dict[str, Any]) -> bool:
    event = order["salesHdrEventData"]
    if bool(event.get("invoiced")):
        return True
    return str(event.get("orderStatus", "")).startswith("INVOICE")


def _shipping(order: dict[str, Any]) -> dict[str, Any]:
    return order["salesHdr"]["salesHdrData"]["shipping"]


def _delivery_date(order: dict[str, Any]) -> datetime | None:
    """When the order would have been delivered, from its own dates."""
    shipping = _shipping(order)
    return (
        _instant(shipping.get("commitDate"))
        or _instant(order["salesHdr"]["salesHdrData"].get("invoiceDate"))
        or _instant(shipping.get("reqrdShipDate"))
    )


def _delivered_cohort(orders: list[dict[str, Any]], target: int) -> set[str]:
    """Which order ids end up delivered, and in a stable order.

    Orders already shipped on a delivery ship-via come first and are never
    excluded -- they are the extract's own answer. Invoiced pick-ups fill the
    rest of the target, taken in hash order so the choice does not depend on
    the file's ordering.
    """
    natural = [
        str(order["salesHdrEventData"]["orderId"])
        for order in orders
        if _is_invoiced(order) and _shipping(order).get("shipViaCode") not in PICKUP_CODES
    ]
    cohort = set(natural)
    if len(cohort) >= target:
        return cohort
    convertible = sorted(
        (
            str(order["salesHdrEventData"]["orderId"])
            for order in orders
            if _is_invoiced(order) and _shipping(order).get("shipViaCode") in PICKUP_CODES
        ),
        key=lambda order_id: (_stable(order_id), order_id),
    )
    for order_id in convertible:
        if len(cohort) >= target:
            break
        cohort.add(order_id)
    return cohort


def backfill(orders: list[dict[str, Any]], *, target: int) -> list[str]:
    """Apply the backfill in place. Returns one line per order it changed."""
    cohort = _delivered_cohort(orders, target)
    changes: list[str] = []
    for order in orders:
        event = order["salesHdrEventData"]
        shipping = _shipping(order)
        order_id = str(event["orderId"])
        before = json.dumps(order, sort_keys=True, default=str)

        event["orderCode"] = "IO" if _is_invoiced(order) else "OO"
        event["trilogieFile"] = "ORDER"

        if order_id in cohort:
            if shipping.get("shipViaCode") in PICKUP_CODES:
                shipping["shipViaCode"], shipping["shipViaDesc"] = DELIVERY_SHIP_VIA
                # The event header carries its own copy, and a reader that took
                # the two as interchangeable would see a pick-up and a delivery
                # on one order.
                event["shipViaCode"] = DELIVERY_SHIP_VIA[0]
            shipping["fleetwiseStatus"] = "Completed"
            if shipping.get("podSigTd") is None:
                moment = _delivery_date(order)
                if moment is not None:
                    shipping["podSigTd"] = _signature_stamp(moment, order_id)
        elif shipping.get("shipViaCode") not in PICKUP_CODES:
            # Shipped, not delivered: a route exists and has not completed.
            shipping["fleetwiseStatus"] = "InRoute"
        else:
            # A pick-up has no DispatchTrack route at all. Absent, not null:
            # the field belongs to orders that are driven somewhere.
            shipping.pop("fleetwiseStatus", None)

        if json.dumps(order, sort_keys=True, default=str) != before:
            changes.append(
                f"{order_id}: orderCode={event['orderCode']} "
                f"shipVia={shipping.get('shipViaCode')} "
                f"fleetwise={shipping.get('fleetwiseStatus')} "
                f"pod={'yes' if shipping.get('podSigTd') else 'no'}"
            )
    return changes


def delivered_count(orders: list[dict[str, Any]]) -> int:
    """Orders that satisfy the whole rule, evaluated exactly as stated."""
    total = 0
    for order in orders:
        event = order["salesHdrEventData"]
        shipping = _shipping(order)
        if (
            event.get("orderCode") in {"IO", "ID"}
            and event.get("trilogieFile") == "ORDER"
            and shipping.get("shipViaCode") not in PICKUP_CODES
            and shipping.get("fleetwiseStatus") == "Completed"
            and shipping.get("podSigTd") is not None
        ):
            total += 1
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="report, write nothing")
    group.add_argument("--apply", action="store_true", help="rewrite the fixture")
    parser.add_argument(
        "--delivered-target",
        type=int,
        default=DEFAULT_DELIVERED_TARGET,
        help=(
            "how many orders should end up DELIVERED "
            f"(default {DEFAULT_DELIVERED_TARGET}; 2 keeps the extract's observed mix)"
        ),
    )
    parser.add_argument("--dataset", type=Path, default=DATASET)
    arguments = parser.parse_args()

    orders = json.loads(arguments.dataset.read_text(encoding="utf-8"))
    changes = backfill(orders, target=arguments.delivered_target)
    delivered = delivered_count(orders)

    print(f"orders={len(orders)} changed={len(changes)} delivered={delivered}")
    for line in changes[:10]:
        print(f"  {line}")
    if len(changes) > 10:
        print(f"  ... {len(changes) - 10} more")

    if arguments.apply:
        # The fixture's own serialization, byte for byte: one-space indent,
        # escaped non-ASCII, trailing newline. A reformat would bury four
        # fields per order in a 2 MB diff nobody can review.
        arguments.dataset.write_text(
            json.dumps(orders, indent=1) + "\n", encoding="utf-8"
        )
        print(f"written {arguments.dataset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
