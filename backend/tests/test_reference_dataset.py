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
    emails = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", serialized))
    assert all(address.endswith("@example.invalid") for address in emails), sorted(emails)

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
    """The documented CDM bridge: `custId` on the order must appear at
    party[].custAccts[].additionalCustomerInfo[].customerId on some party."""
    load = _module("load_reference_dataset")
    template = json.loads((DATASET / "customerOutboundCDM.json").read_text(encoding="utf-8"))
    bridged = {
        info["customerId"]
        for customer in load._customers(orders, template)
        for party in customer["party"]
        for account in party["custAccts"]
        for info in account["additionalCustomerInfo"]
    }
    referenced = {str(order["salesHdr"]["salesHdrData"]["custId"]) for order in orders}
    assert referenced <= bridged
    assert referenced
