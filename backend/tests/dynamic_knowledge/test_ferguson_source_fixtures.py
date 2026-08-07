"""Path-existence contract tests against real (scrubbed) Ferguson source documents.

These fixtures are scrubbed copies of masked production samples for `salesInv`
(header document only -- no real `docType: "line"` sample has ever been
provided, so line-level field paths remain UNVERIFIED_SOURCE_CONTRACT until
one is), `customerOutboundCDM`, and `lkpSearchProduct`. Every path asserted
here is a path some later step of the source-to-graph alignment plan will
configure in `active-schema.return-order.yaml`; a test failing here means the
plan's documented path is wrong, not that the fixture is wrong.

`shipmentInfo` has no real sample at all and is UNVERIFIED_SOURCE_CONTRACT for
that reason -- there is intentionally no fixture file for it yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "ferguson_source_samples"


def _load(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def _resolve(document: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = document
    for segment in path:
        assert isinstance(current, dict), f"expected an object at {path[: path.index(segment)]!r}"
        assert segment in current, f"missing path segment {segment!r} in {path!r}"
        current = current[segment]
    return current


SALES_INV_HEADER_PATHS: tuple[tuple[str, ...], ...] = (
    ("_id",),
    ("salesInvEventMeta", "lastUpdateTs"),
    ("salesInvEventData", "accountId"),
    ("salesInvEventData", "orderId"),
    ("salesInvEventData", "docType"),
    ("salesInv", "salesInvData", "custId"),
    ("salesInv", "salesInvData", "custName"),
    ("salesInv", "salesInvData", "custPONumber"),
    ("salesInv", "salesInvData", "jobName"),
    ("salesInv", "salesInvData", "orderDate"),
    ("salesInv", "salesInvData", "srcCode", "srcOrderStatus"),
    # Shipping method fields are siblings of "address", not nested inside it.
    ("salesInv", "salesInvData", "shipping", "shipTo", "shipViaCode"),
    ("salesInv", "salesInvData", "shipping", "shipTo", "shipViaDesc"),
    ("salesInv", "salesInvData", "shipping", "shipTo", "address", "address1"),
    ("salesInv", "salesInvData", "linesInfo"),
)

CUSTOMER_OUTBOUND_CDM_PATHS: tuple[tuple[str, ...], ...] = (
    ("_id",),
    ("partyId",),
    ("type",),
    ("party",),
)

LKP_SEARCH_PRODUCT_PATHS: tuple[tuple[str, ...], ...] = (
    ("_id",),
    ("eventMeta", "lastUpdateTS"),
    ("masterProduct", "productDesc"),
    ("masterProduct", "prodLongDesc"),
    ("masterProduct", "vendorProdCode"),
    ("masterProduct", "altCodes", "alt1Code1"),
    ("masterProduct", "freightCode"),
    ("fld", "shippingClassification"),
    ("fld", "shippingMethod"),
    ("whseProducts",),
)

# Protected payment paths that must never become a configured physical_path,
# record_path, where-selector, derive.source_field, discriminator, cursor,
# search-index, source-index, ownership-identity, or key_resolution field
# once source_security_policies (Step 3) exists to enforce this at schema
# activation time. Asserting they resolve against the real fixture here
# proves the denylist targets paths that actually exist in production data,
# not hypothetical ones.
PROTECTED_SALES_INV_PATHS: tuple[tuple[str, ...], ...] = (
    ("salesInv", "salesInvData", "lineAssocs", "pmts", "paidWithNum"),
    ("salesInv", "salesInvData", "lineAssocs", "pmts", "pmtTokenKey"),
    ("salesInv", "salesInvData", "lineAssocs", "pmts", "ccName"),
    ("salesInv", "salesInvData", "lineAssocs", "pmts", "ccAddr1"),
    ("salesInv", "salesInvData", "lineAssocs", "pmts", "ccCity"),
    ("salesInv", "salesInvData", "lineAssocs", "pmts", "ccState"),
    ("salesInv", "salesInvData", "lineAssocs", "pmts", "ccZip"),
    ("salesInv", "salesInvData", "lineAssocs", "deposits", "ccName"),
    ("salesInv", "salesInvData", "lineAssocs", "deposits", "ccAddr1"),
)


@pytest.mark.parametrize("path", SALES_INV_HEADER_PATHS)
def test_sales_inv_header_path_exists(path: tuple[str, ...]) -> None:
    _resolve(_load("sales_inv_header.json"), path)


@pytest.mark.parametrize("path", CUSTOMER_OUTBOUND_CDM_PATHS)
def test_customer_outbound_cdm_path_exists(path: tuple[str, ...]) -> None:
    _resolve(_load("customer_outbound_cdm.json"), path)


@pytest.mark.parametrize("path", LKP_SEARCH_PRODUCT_PATHS)
def test_lkp_search_product_path_exists(path: tuple[str, ...]) -> None:
    _resolve(_load("lkp_search_product.json"), path)


@pytest.mark.parametrize("path", PROTECTED_SALES_INV_PATHS)
def test_protected_payment_path_exists_in_real_shape(path: tuple[str, ...]) -> None:
    """A protected path that doesn't exist can't prove anything about denylist enforcement."""
    _resolve(_load("sales_inv_header.json"), path)


def test_sales_inv_header_id_is_logon_scoped_composite_key() -> None:
    document = _load("sales_inv_header.json")
    assert document["_id"] == "DALLAS*WE130468*H"
    assert document["_id"].split("*")[0] == document["salesInvEventData"]["accountId"]
    assert document["_id"].split("*")[1] == document["salesInvEventData"]["orderId"]


def test_lkp_search_product_id_is_string_not_number() -> None:
    assert isinstance(_load("lkp_search_product.json")["_id"], str)


def test_lkp_search_product_vendor_code_is_not_the_same_field_as_product_id() -> None:
    document = _load("lkp_search_product.json")
    assert document["_id"] != document["masterProduct"]["vendorProdCode"]


def test_lkp_search_product_has_multiple_warehouse_rows() -> None:
    """ProductWarehouse is deferred (Step 12) -- this fixture exists to prove that
    deferral decision is tested against a product with more than one warehouse,
    not a coincidentally single-warehouse sample."""
    assert len(_load("lkp_search_product.json")["whseProducts"]) > 1


def test_customer_outbound_cdm_has_duplicate_party_main_cust_entry() -> None:
    """Regression fixture for replace-child-set/ProjectionOwnership dedup (Step 9)."""
    party = _load("customer_outbound_cdm.json")["party"][0]
    main_custs = [entry["mainCusts"] for entry in party["partyMainCusts"]]
    assert len(main_custs) != len(set(main_custs))


def test_customer_outbound_cdm_has_both_phone_and_fax_contact_points() -> None:
    party = _load("customer_outbound_cdm.json")["party"][0]
    contact_types = {entry["contactPointType"] for entry in party["customerContactPoints"]}
    assert contact_types == {"PHONE", "FAX"}
