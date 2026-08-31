"""What proves an order was delivered, and what only looks like it does.

`delivery_date` reached the evaluator as a stated fact and nothing wrote one, so
a delivery claim arrived undated and its reporting window had no basis --
`return_case_activities` says as much: "A delivery claim whose `delivery_date`
the platform never learned has no reporting deadline." The proof was on the
order the whole time.

These run against the committed reference extract rather than hand-written
documents, because the reading being guarded against is one the real data
invites: every proof-of-delivery signature in the original extract sat on a
counter pick-up, where it is the customer signing at the counter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.return_configuration import DeliveryProofConfiguration
from return_platform.workflows.case_policy_facts import (
    CONFIRMED_ORDER_DELIVERY_DATE,
    assemble_policy_evaluation_input,
    delivery_date_from_confirmed_order,
)

_DATASET = Path(__file__).resolve().parents[2] / "fixtures" / "reference_dataset" / "salesInv1.json"

#: The bindings the release states. Repeated as literals so this module tests
#: the resolver rather than the release -- `test_window_policy_is_configuration`
#: is where the packaged values are asserted.
PROOF = DeliveryProofConfiguration(
    order_code_paths=("salesHdrEventData.orderCode",),
    invoice_order_codes=("IO", "ID"),
    file_paths=("salesHdrEventData.trilogieFile",),
    order_file_value="ORDER",
    ship_via_paths=(
        "salesHdr.salesHdrData.shipping.shipViaCode",
        "salesHdrEventData.shipViaCode",
    ),
    collected_ship_via_codes=("CPU", "WCL", "BO"),
    route_status_paths=("salesHdr.salesHdrData.shipping.fleetwiseStatus",),
    route_completed_value="Completed",
    signature_paths=("salesHdr.salesHdrData.shipping.podSigTd",),
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def orders() -> list[dict[str, Any]]:
    return json.loads(_DATASET.read_text(encoding="utf-8"))


def _shipping(order: dict[str, Any]) -> dict[str, Any]:
    return order["salesHdr"]["salesHdrData"]["shipping"]


def _delivered(orders: list[dict[str, Any]]) -> dict[str, Any]:
    return next(order for order in orders if _shipping(order).get("fleetwiseStatus") == "Completed")


# ---------------------------------------------------------------------------
# The five conditions, against the real extract
# ---------------------------------------------------------------------------


def test_a_delivered_order_is_dated_from_its_signature(orders: list[dict[str, Any]]) -> None:
    order = _delivered(orders)

    resolved = delivery_date_from_confirmed_order(order, proof=PROOF)

    assert resolved is not None
    assert resolved.tzinfo is not None
    # The signature is the date, not merely the proof.
    assert str(resolved.year) in str(_shipping(order)["podSigTd"])


def test_the_extract_holds_delivered_orders_at_all(orders: list[dict[str, Any]]) -> None:
    """Guards the fixture, not the resolver.

    A corpus in which nothing is delivered passes every test below by accident,
    and that is the state the extract was in: `fleetwiseStatus` did not exist on
    any document. `scripts/backfill_delivery_proof.py` is what keeps this true.
    """
    dated = [order for order in orders if delivery_date_from_confirmed_order(order, proof=PROOF)]
    assert dated
    assert len(dated) < len(orders)


def test_a_counter_signature_is_not_a_delivery(orders: list[dict[str, Any]]) -> None:
    """The reading this whole rule exists to refuse.

    Every `podSigTd` in the original extract sat on a `CPU`/`WCL` order. Taking
    the signature alone called all fourteen of them delivered.
    """
    collected = [
        order
        for order in orders
        if _shipping(order).get("shipViaCode") in {"CPU", "WCL"}
        and _shipping(order).get("podSigTd") is not None
    ]
    assert collected, "the extract no longer holds a signed pick-up to guard against"
    assert all(
        delivery_date_from_confirmed_order(order, proof=PROOF) is None for order in collected
    )


def test_a_route_still_running_is_not_a_delivery(orders: list[dict[str, Any]]) -> None:
    shipped = [order for order in orders if _shipping(order).get("fleetwiseStatus") == "InRoute"]
    assert shipped
    assert all(delivery_date_from_confirmed_order(order, proof=PROOF) is None for order in shipped)


def test_an_order_still_working_is_not_a_delivery(orders: list[dict[str, Any]]) -> None:
    """An invoice code is a condition: `OO` is an order nobody has billed."""
    order = dict(_delivered(orders))
    order["salesHdrEventData"] = {**order["salesHdrEventData"], "orderCode": "OO"}

    assert delivery_date_from_confirmed_order(order, proof=PROOF) is None


def test_an_unparseable_signature_is_refused_rather_than_coerced(
    orders: list[dict[str, Any]],
) -> None:
    """A deadline computed from a string nobody could read is worse than none."""
    order = json.loads(json.dumps(_delivered(orders)))
    order["salesHdr"]["salesHdrData"]["shipping"]["podSigTd"] = "sometime last week"

    assert delivery_date_from_confirmed_order(order, proof=PROOF) is None


def test_an_unbound_deployment_answers_nothing(orders: list[dict[str, Any]]) -> None:
    """Absent bindings mean no delivery date -- not "not delivered"."""
    assert (
        delivery_date_from_confirmed_order(_delivered(orders), proof=DeliveryProofConfiguration())
        is None
    )


# ---------------------------------------------------------------------------
# How it reaches the evaluator
# ---------------------------------------------------------------------------


def test_the_order_outranks_a_stated_delivery_date() -> None:
    """The carrier's proof beats what anyone remembered, as the purchase date does."""
    stated = datetime(2026, 1, 5, tzinfo=UTC)
    proven = datetime(2026, 2, 9, 14, 30, tzinfo=UTC)
    log = [
        {
            "factId": "f1",
            "factName": "delivery_date",
            "value": stated.isoformat(),
            # Admissible, or the fact never enters the evaluation at all and the
            # test proves nothing about precedence.
            "acquisitionMethod": "STATED",
            "recordedAt": datetime(2026, 8, 1, tzinfo=UTC),
        }
    ]

    assembled = assemble_policy_evaluation_input(
        log, request_date=NOW, confirmed_order_delivery_date=proven
    )

    assert assembled.facts.delivery_date == proven
    assert CONFIRMED_ORDER_DELIVERY_DATE in assembled.admitted
    assert ("delivery_date", "SUPERSEDED_BY_CONFIRMED_ORDER") in assembled.excluded


def test_no_delivery_date_leaves_the_log_standing() -> None:
    """Absent, the behaviour is exactly what it was before this existed."""
    stated = datetime(2026, 1, 5, tzinfo=UTC)
    log = [
        {
            "factId": "f1",
            "factName": "delivery_date",
            "value": stated.isoformat(),
            # Admissible, or the fact never enters the evaluation at all and the
            # test proves nothing about precedence.
            "acquisitionMethod": "STATED",
            "recordedAt": datetime(2026, 8, 1, tzinfo=UTC),
        }
    ]

    assembled = assemble_policy_evaluation_input(log, request_date=NOW)

    assert assembled.facts.delivery_date == stated
    assert CONFIRMED_ORDER_DELIVERY_DATE not in assembled.admitted
