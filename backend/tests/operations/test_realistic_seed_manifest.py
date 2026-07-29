from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from return_platform.operations.seed_manifest import (
    SEED_CUSTOMERS,
    SEED_ORDER_COUNT,
    SEED_PRODUCTS,
    SEED_SCENARIOS,
    manifest_digest,
    materialize_domain_seed,
    materialize_seed,
    seed_counts,
)


def test_realistic_seed_has_required_counts() -> None:
    counts = seed_counts()

    assert len(SEED_CUSTOMERS) == 400
    assert len(SEED_PRODUCTS) == 500
    assert len(SEED_SCENARIOS) == SEED_ORDER_COUNT == 1250
    assert 2500 <= counts["orderLines"] <= 6250
    assert counts["shipments"] == 1250


def test_realistic_seed_is_deterministic_and_not_demo_data() -> None:
    assert manifest_digest("operational-realistic-v1") == manifest_digest(
        "operational-realistic-v1"
    )
    assert all("demo" not in str(customer["name"]).lower() for customer in SEED_CUSTOMERS)
    assert all("dummy" not in str(customer["name"]).lower() for customer in SEED_CUSTOMERS)
    assert all(str(scenario["orderReference"]).startswith("SO-2026-") for scenario in SEED_SCENARIOS)


def test_realistic_seed_referential_integrity() -> None:
    customer_ids = {str(customer["_id"]) for customer in SEED_CUSTOMERS}
    product_ids = {str(product["_id"]) for product in SEED_PRODUCTS}

    for scenario in SEED_SCENARIOS:
        assert str(scenario["customerReference"]) in customer_ids
        assert set(map(str, scenario["lineSkus"])).issubset(product_ids)


def test_realistic_seed_materializes_source_records_and_safe_identity() -> None:
    applied_at = datetime(2026, 7, 29, 12, tzinfo=UTC)
    customers, products, orders = materialize_seed("operational-realistic-v1", applied_at)
    domain = materialize_domain_seed("operational-realistic-v1", applied_at)

    assert len(customers) == 400
    assert len(products) == 500
    assert len(orders) == 1250
    assert len(domain["customerOutboundCDM"]) == 400
    assert len(domain["lkpSearchProduct"]) == 500
    assert len(domain["salesInv"]) == 1250
    assert len(domain["shipmentInfo"]) == 1250
    assert all(
        str(customer["email"]).endswith("@example.invalid")
        for customer in domain["customerOutboundCDM"]
    )


def test_realistic_seed_order_totals_match_lines() -> None:
    applied_at = datetime(2026, 7, 29, 12, tzinfo=UTC)
    sales = materialize_domain_seed("operational-realistic-v1", applied_at)["salesInv"]

    for order in sales[:50]:
        lines = order["salesLines"]
        header = order["salesHdr"]["salesHdrData"]
        subtotal = sum(Decimal(str(line["lineData"]["extendedPrice"])) for line in lines)
        tax = sum(Decimal(str(line["lineData"]["taxAmount"])) for line in lines)
        discount = sum(Decimal(str(line["lineData"]["discountAmount"])) for line in lines)
        expected_total = subtotal + tax + Decimal(str(header["shippingAmount"])) - discount

        assert Decimal(str(header["subtotal"])) == subtotal.quantize(Decimal("0.01"))
        assert Decimal(str(header["orderTotal"])) == expected_total.quantize(Decimal("0.01"))
