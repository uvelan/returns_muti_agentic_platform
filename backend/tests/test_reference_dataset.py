"""The committed reference dataset must stay de-identified and joinable.

These guard the two properties the dataset is committed for. It carries no
personal data -- it is a scrub of a production extract, and a regenerated or
hand-edited file could quietly reintroduce a name. And its three collections
still join, because a dataset whose joins do not resolve produces an empty
graph and a copilot that finds nothing, which is the exact failure this whole
dataset exists to prevent.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATASET = BACKEND_ROOT / "fixtures" / "reference_dataset"


def _module(name: str) -> Any:
    specification = importlib.util.spec_from_file_location(
        name, BACKEND_ROOT / "scripts" / f"{name}.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def orders() -> list[dict[str, Any]]:
    return json.loads((DATASET / "salesInv1.json").read_text(encoding="utf-8"))


def test_the_dataset_is_the_expected_size(orders: list[dict[str, Any]]) -> None:
    assert len(orders) == 100
    assert sum(len(order.get("salesLines", [])) for order in orders) > 0


def test_no_contact_details_survive(orders: list[dict[str, Any]]) -> None:
    """No routable phone number or email anywhere in the file.

    Asserted over the serialized document rather than by walking known keys,
    because the fields that leaked during de-identification were the ones
    nobody thought to walk -- `custPONumber` holding a site address, an audit
    string with an employee's name inside it.
    """
    serialized = json.dumps(orders)
    # The domain must contain a dot, or product specifications like "3.5@208V"
    # are read as email addresses and the assertion fails on the data being
    # correct.
    # The property is that the domain can never resolve, NOT that it is one
    # particular string. Pinning the literal `@example.invalid` conflated the two
    # and made `generated+96919241@example.invalid` -- an address that reads as a
    # defect rather than as a customer -- the only compliant form. Every TLD
    # listed here is reserved by RFC 2606 / RFC 6761 and cannot be registered, so
    # a readable `taylor.solberg@bluefinutilities.example` is exactly as
    # undeliverable and passes for the reason that actually matters.
    deidentify = _module("deidentify_reference_dataset")
    emails = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", serialized))
    unroutable = tuple(deidentify.RESERVED_EMAIL_TLDS)
    assert all(address.endswith(unroutable) for address in emails), sorted(emails)

    # Any 3-3-4 or 10-digit run that is not the reserved 555-01xx test range.
    telephones = set(re.findall(r"\b(?!555-01)\d{3}-\d{3}-\d{4}\b", serialized))
    assert telephones == set(), sorted(telephones)


def test_identities_come_from_the_synthetic_vocabulary(orders: list[dict[str, Any]]) -> None:
    deidentify = _module("deidentify_reference_dataset")
    allowed = set(deidentify.BUSINESS_NAMES) | set(deidentify.PERSON_NAMES)
    names = {order["salesHdr"]["salesHdrData"]["custName"] for order in orders}
    assert names <= allowed, sorted(names - allowed)


def test_addresses_are_internally_consistent(orders: list[dict[str, Any]]) -> None:
    """A city, state and postcode that contradict each other are a trap.

    The copilot can be searched by city, so seed data pairing a Texas city with
    an Illinois postcode teaches the wrong thing about what a match means.
    """
    deidentify = _module("deidentify_reference_dataset")
    known = {city: (state, postcode) for city, state, postcode in deidentify.CITIES}
    for order in orders:
        address = order["salesHdr"]["salesHdrData"]["shipping"]["shipTo"]["address"]
        city = address.get("city")
        if not city:
            continue
        assert city in known, city
        state, postcode = known[city]
        assert address.get("state") == state, city
        # Absent stays absent: some records carry no postcode, and inventing
        # one would make the fixture claim something the extract did not.
        if address.get("zipCode"):
            assert address["zipCode"] == postcode, city


def test_business_vocabulary_is_still_real(orders: list[dict[str, Any]]) -> None:
    """Order numbers, statuses and product text are untouched by the scrub.

    They are what makes the copilot's answers worth reading, and none of them
    identifies anyone.
    """
    numbers = {order["salesHdrEventData"]["orderId"] for order in orders}
    assert len(numbers) == len(orders)
    assert all(re.fullmatch(r"[A-Z]{2}\d+(-\d+)?", number) for number in numbers), sorted(numbers)[
        :5
    ]

    descriptions = {
        line["lineData"].get("productDesc")
        for order in orders
        for line in order.get("salesLines", [])
        if line.get("lineData", {}).get("productDesc")
    }
    assert len(descriptions) > 50


def test_delivery_can_be_told_from_pick_up(orders: list[dict[str, Any]]) -> None:
    """The five conditions `salesInv` decides DELIVERED on all resolve.

    The extract answered two of them -- `shipViaCode` and `podSigTd` -- and
    every `podSigTd` in it sat on a `CPU`/`WCL` order, where the signature is
    the customer signing at the counter. A corpus in that state cannot tell
    delivered from collected, and a reader that took the signature alone would
    have called all fourteen pick-ups delivered.
    `scripts/backfill_delivery_proof.py` is what keeps this true.
    """
    pickup = {"CPU", "WCL", "BO"}

    def shipping(order: dict[str, Any]) -> dict[str, Any]:
        return order["salesHdr"]["salesHdrData"]["shipping"]

    delivered = [
        order
        for order in orders
        if order["salesHdrEventData"].get("orderCode") in {"IO", "ID"}
        and order["salesHdrEventData"].get("trilogieFile") == "ORDER"
        and shipping(order).get("shipViaCode") not in pickup
        and shipping(order).get("fleetwiseStatus") == "Completed"
        and shipping(order).get("podSigTd") is not None
    ]
    assert delivered, "no order in the corpus reads as delivered"

    # Both halves of the discrimination, not just the positive one: a corpus
    # where everything is delivered tests the rule no better than one where
    # nothing is.
    assert len(delivered) < len(orders)
    assert all(order["salesHdrEventData"].get("orderCode") for order in orders)
    assert all(order["salesHdrEventData"].get("trilogieFile") == "ORDER" for order in orders)

    # A route belongs to an order that is driven somewhere. A pick-up carrying
    # a FleetWise status is the state that lets a signature at the counter read
    # as proof of delivery.
    assert not [
        order
        for order in orders
        if shipping(order).get("shipViaCode") in pickup
        and shipping(order).get("fleetwiseStatus") is not None
    ]


def test_every_order_line_can_reach_a_product(orders: list[dict[str, Any]]) -> None:
    """`line_references_product` matches lineData.masterProductId against the
    product document's `_id`, so a line whose master id derives no product is an
    edge the graph can never form."""
    load = _module("load_reference_dataset")
    template = json.loads((DATASET / "lkpSearchProduct.json").read_text(encoding="utf-8"))
    product_ids = {product["_id"] for product in load._products(orders, template)}
    referenced = {
        str(line["lineData"]["masterProductId"])
        for order in orders
        for line in order.get("salesLines", [])
        if line.get("lineData", {}).get("masterProductId") is not None
    }
    assert referenced <= product_ids
    assert referenced


def test_every_order_can_reach_a_customer(orders: list[dict[str, Any]]) -> None:
    """The real CDM bridge: `custId` on the order is the half of
    `party[].partyMainCusts[].mainCusts` after the `*`.

    This used to read `party[].custAccts[].additionalCustomerInfo[].customerId`,
    which is the *field specification's* path and not the data's. No real CDM
    document has a `custAccts` array at any level -- verified against
    `MASTER:900781` -- so the fixture passed only because
    `load_reference_dataset` built the shape the assertion expected. Both have
    been moved onto the path the schema now declares and the real document
    carries (D41 / D48).
    """
    load = _module("load_reference_dataset")
    template = json.loads((DATASET / "customerOutboundCDM.json").read_text(encoding="utf-8"))
    customers = load._customers(orders, template)
    assert not any("custAccts" in party for customer in customers for party in customer["party"]), (
        "the fabricated custAccts shape is back; no real CDM document has one"
    )
    bridged = {
        str(bridge["mainCusts"]).partition("*")[2]
        for customer in customers
        for party in customer["party"]
        for bridge in party["partyMainCusts"]
    }
    referenced = {str(order["salesHdr"]["salesHdrData"]["custId"]) for order in orders}
    assert referenced <= bridged
    assert referenced


def test_every_product_records_a_colour_the_description_agrees_with(
    orders: list[dict[str, Any]],
) -> None:
    """`product.colour_finish` is a searchable signal, so every seed product carries one.

    The template is one real product with `eco.colorFinish: ["White"]`, and
    copying it put white on all of them. The finish the line states wins where
    it states one -- `... PEX-B POT WHIT` is white and `... P TRAP BN` is
    brushed nickel -- and the loader and the large generator resolve the rest
    through the same tiers (`seed_ferguson_idiom.resolve_colour`).
    """
    load = _module("load_reference_dataset")
    idiom = _module("seed_ferguson_idiom")
    template = json.loads((DATASET / "lkpSearchProduct.json").read_text(encoding="utf-8"))
    products = load._products(orders, template)

    colours = {product["_id"]: product["eco"]["colorFinish"] for product in products}
    assert all(
        isinstance(value, list) and len(value) == 1 and value[0] for value in colours.values()
    )
    assert len({value[0] for value in colours.values()}) > 1, "every product read the template's"

    by_description = {
        product["_id"]: product["masterProduct"]["productDesc"] for product in products
    }
    for product_id, description in by_description.items():
        tokens = description.upper().split()
        stated = next(
            (
                idiom.FINISH_TOKENS[token]
                for token in reversed(tokens)
                if token in idiom.FINISH_TOKENS
            ),
            None,
        )
        if stated is not None:
            assert colours[product_id] == [stated], (product_id, description)
        assert colours[product_id] == [idiom.resolve_colour(description, product_id=product_id)]


def test_resolve_colour_reads_the_finish_last_and_never_a_reducer() -> None:
    idiom = _module("seed_ferguson_idiom")
    assert idiom.resolve_colour("1-1/4 17GA BRS SJ P TRAP BN") == "Brushed Nickel"
    assert idiom.resolve_colour("3/4X1/2 CU RED COUP") == "Copper"
    assert idiom.resolve_colour("1/2X20 STRT LGTH PEX-B POT WHIT") == "White"
    assert idiom.resolve_colour("16X25 SILV FLEX AIR DUCT R8.0") == "Silver"
    assert (
        idiom.resolve_colour("12X5 FT 30GA SNLK RND PIPE", category_key="duct_fittings")
        == "Galvanized"
    )
    # Tier three is a function of the id alone, so the corpus stays reproducible.
    assert idiom.resolve_colour("3T 14 SEER2 H/P", product_id="4000123") == idiom.resolve_colour(
        "3T 14 SEER2 H/P", product_id="4000123"
    )
    assert all(
        category.key in idiom.CATEGORY_COLOURS
        for category in idiom.CATEGORIES
        if not category.finishes
    ), "a generated category with no finish and no material colour would ship uncoloured"


def test_dates_move_to_the_anchor_and_keep_their_gaps() -> None:
    """The extract is from October 2025 and the loader lands its newest order on
    the anchor day. One offset for the whole corpus: the gap between an order
    and its commit date, and the proof-of-delivery signature the ERP writes as
    text, move by exactly the same number of days."""
    from datetime import date

    from bson import json_util

    load = _module("load_reference_dataset")
    orders = json_util.loads((DATASET / "salesInv1.json").read_text(encoding="utf-8"))
    header = lambda order: order["salesHdr"]["salesHdrData"]  # noqa: E731
    before = [
        (
            header(o)["orderDate"],
            header(o)["shipping"]["commitDate"],
            header(o)["shipping"].get("podSigTd"),
        )
        for o in orders
    ]
    latest_before = max(order_date for order_date, _, _ in before)

    anchor = date(2026, 9, 7)
    offset = load.shift_order_dates(orders, anchor)

    assert offset.days == (anchor - latest_before.date()).days
    after = [
        (
            header(o)["orderDate"],
            header(o)["shipping"]["commitDate"],
            header(o)["shipping"].get("podSigTd"),
        )
        for o in orders
    ]
    assert max(order_date for order_date, _, _ in after).date() == anchor
    for (od0, cd0, sig0), (od1, cd1, sig1) in zip(before, after, strict=True):
        assert od1 - od0 == offset
        assert cd1 - cd0 == offset
        assert (sig1 is None) == (sig0 is None)
        if sig0 is not None:
            parsed0 = load.datetime.strptime(sig0.title(), load._TEXT_TIMESTAMP_FORMAT)
            parsed1 = load.datetime.strptime(sig1.title(), load._TEXT_TIMESTAMP_FORMAT)
            assert parsed1 - parsed0 == offset
            assert sig1 == sig1.upper()
    # The extract itself holds signatures after the newest order -- two of them
    # months after -- so they land after the anchor. The loader names them rather
    # than moving them, and a week past the anchor is still the extract's own gap.
    late = load.signatures_after(orders, anchor)
    assert {"PLYMOUTH*CR780953-1", "CHARLOTTE*CK363351"} <= late
    assert not load.signatures_after(orders, anchor + load.timedelta(days=200))
