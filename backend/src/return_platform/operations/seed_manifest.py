"""Deterministic, non-PII demo seed manifest for E2E return scenarios."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Final

SEED_CUSTOMERS: Final[tuple[dict[str, object], ...]] = (
    {"_id": "CUST-1001", "name": "Demo Customer 01", "tier": "GOLD", "region": "IN-SOUTH"},
    {"_id": "CUST-1002", "name": "Demo Customer 02", "tier": "STANDARD", "region": "IN-SOUTH"},
    {"_id": "CUST-1003", "name": "Demo Customer 03", "tier": "SILVER", "region": "IN-WEST"},
    {"_id": "CUST-1004", "name": "Demo Customer 04", "tier": "STANDARD", "region": "IN-NORTH"},
    {"_id": "CUST-1005", "name": "Demo Customer 05", "tier": "GOLD", "region": "IN-EAST"},
)

SEED_PRODUCTS: Final[tuple[dict[str, object], ...]] = (
    {"_id": "SKU-100", "name": "Smart Thermostat", "returnWindowDays": 30, "category": "HVAC"},
    {
        "_id": "SKU-200",
        "name": "Water Filter Cartridge",
        "returnWindowDays": 15,
        "category": "PLUMBING",
    },
    {
        "_id": "SKU-300",
        "name": "Industrial Valve",
        "returnWindowDays": 45,
        "category": "INDUSTRIAL",
    },
    {"_id": "SKU-400", "name": "Safety Sensor", "returnWindowDays": 30, "category": "SAFETY"},
    {"_id": "SKU-500", "name": "Pump Controller", "returnWindowDays": 45, "category": "CONTROLS"},
)

# Offsets are materialized relative to apply time, while this immutable manifest is hashed.
# This preserves stable identity without allowing delivered-date scenarios to drift over time.
SEED_SCENARIOS: Final[tuple[dict[str, object], ...]] = (
    {
        "id": "POS-01-DAMAGED-WITHIN-WINDOW",
        "orderReference": "ORD-10001",
        "customerReference": "CUST-1001",
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 5,
        "reasonCode": "DAMAGED",
        "expectedDecision": "APPROVE",
        "sku": "SKU-100",
    },
    {
        "id": "POS-02-WRONG-ITEM",
        "orderReference": "ORD-10004",
        "customerReference": "CUST-1002",
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 1,
        "reasonCode": "WRONG_ITEM",
        "expectedDecision": "APPROVE",
        "sku": "SKU-200",
    },
    {
        "id": "POS-03-DEFECTIVE",
        "orderReference": "ORD-10005",
        "customerReference": "CUST-1003",
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 14,
        "reasonCode": "DEFECTIVE",
        "expectedDecision": "APPROVE",
        "sku": "SKU-300",
    },
    {
        "id": "POS-04-WINDOW-BOUNDARY",
        "orderReference": "ORD-10006",
        "customerReference": "CUST-1004",
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 45,
        "reasonCode": "NOT_AS_DESCRIBED",
        "expectedDecision": "APPROVE",
        "sku": "SKU-500",
    },
    {
        "id": "POS-05-MISSING-PARTS",
        "orderReference": "ORD-10007",
        "customerReference": "CUST-1005",
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 3,
        "reasonCode": "MISSING_PARTS",
        "expectedDecision": "APPROVE",
        "sku": "SKU-400",
    },
    {
        "id": "NEG-01-EXPIRED-WINDOW",
        "orderReference": "ORD-10002",
        "customerReference": "CUST-1002",
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 60,
        "reasonCode": "DAMAGED",
        "expectedDecision": "REJECT",
        "sku": "SKU-300",
    },
    {
        "id": "NEG-02-NOT-DELIVERED",
        "orderReference": "ORD-10003",
        "customerReference": "CUST-1001",
        "orderStatus": "IN_TRANSIT",
        "daysSinceDelivery": None,
        "reasonCode": "DAMAGED",
        "expectedDecision": "REJECT",
        "sku": "SKU-200",
    },
    {
        "id": "NEG-03-CANCELLED-ORDER",
        "orderReference": "ORD-10008",
        "customerReference": "CUST-1003",
        "orderStatus": "CANCELLED",
        "daysSinceDelivery": None,
        "reasonCode": "WRONG_ITEM",
        "expectedDecision": "REJECT",
        "sku": "SKU-100",
    },
    {
        "id": "NEG-04-FRAUD-REVIEW",
        "orderReference": "ORD-10009",
        "customerReference": "CUST-1004",
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 10,
        "reasonCode": "FRAUD_SUSPECTED",
        "expectedDecision": "REVIEW_REQUIRED",
        "sku": "SKU-400",
    },
    {
        "id": "NEG-05-SERIAL-REVIEW",
        "orderReference": "ORD-10010",
        "customerReference": "CUST-1005",
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 2,
        "reasonCode": "SERIAL_MISMATCH",
        "expectedDecision": "REVIEW_REQUIRED",
        "sku": "SKU-500",
    },
)


def manifest_payload(seed_version: str) -> dict[str, object]:
    """Return the immutable manifest used for stable digest calculation."""
    return {
        "seedVersion": seed_version,
        "customers": SEED_CUSTOMERS,
        "products": SEED_PRODUCTS,
        "scenarios": SEED_SCENARIOS,
    }


def manifest_digest(seed_version: str) -> str:
    canonical = json.dumps(manifest_payload(seed_version), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def materialize_seed(
    seed_version: str, applied_at: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Materialize source documents while keeping the manifest digest stable."""
    digest = manifest_digest(seed_version)
    customers = [
        {**item, "seedVersion": seed_version, "seedDigest": digest} for item in SEED_CUSTOMERS
    ]
    products = [
        {**item, "seedVersion": seed_version, "seedDigest": digest} for item in SEED_PRODUCTS
    ]
    orders: list[dict[str, Any]] = []
    for scenario in SEED_SCENARIOS:
        days = scenario["daysSinceDelivery"]
        delivered_at = applied_at - timedelta(days=int(days)) if isinstance(days, int) else None
        orders.append(
            {
                "_id": scenario["orderReference"],
                "customerReference": scenario["customerReference"],
                "status": scenario["orderStatus"],
                "deliveredAt": delivered_at,
                "items": [
                    {
                        "itemReference": "LINE-1",
                        "sku": scenario["sku"],
                        "quantity": 1,
                        "unitPrice": 100.0,
                    }
                ],
                "scenarioId": scenario["id"],
                "expectedReasonCode": scenario["reasonCode"],
                "expectedDecision": scenario["expectedDecision"],
                "seedVersion": seed_version,
                "seedDigest": digest,
            }
        )
    return customers, products, orders


def scenario_counts() -> dict[str, int]:
    decisions = [str(item["expectedDecision"]) for item in SEED_SCENARIOS]
    return {
        "positive": decisions.count("APPROVE"),
        "negative": decisions.count("REJECT"),
        "reviewRequired": decisions.count("REVIEW_REQUIRED"),
        "total": len(decisions),
    }


def materialize_domain_seed(
    seed_version: str,
    applied_at: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Materialize the HLD source collections with coherent cross-collection keys."""
    digest = manifest_digest(seed_version)
    customer_by_id = {str(item["_id"]): item for item in SEED_CUSTOMERS}
    product_by_id = {str(item["_id"]): item for item in SEED_PRODUCTS}
    sales_inventory: list[dict[str, Any]] = []
    shipments: list[dict[str, Any]] = []
    customers: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []

    for customer in SEED_CUSTOMERS:
        customer_id = str(customer["_id"])
        ordinal = int(customer_id.rsplit("-", 1)[-1])
        customers.append(
            {
                "_id": customer_id,
                "customerId": customer_id,
                "customerName": customer["name"],
                "phoneNumber": f"+91-90000-{ordinal:05d}",
                "email": f"{customer_id.lower()}@example.invalid",
                "accounts": [{"accountNumber": customer_id, "status": "ACTIVE"}],
                "region": customer["region"],
                "seedVersion": seed_version,
                "seedDigest": digest,
                "updatedAt": applied_at,
            }
        )

    for product in SEED_PRODUCTS:
        sku = str(product["_id"])
        products.append(
            {
                "_id": sku,
                "productId": sku,
                "sku": sku,
                "masterProductId": f"MASTER-{sku}",
                "productDescription": product["name"],
                "productType": "BULKY" if product["category"] == "INDUSTRIAL" else "STANDARD",
                "category": product["category"],
                "returnWindowDays": product["returnWindowDays"],
                "seedVersion": seed_version,
                "seedDigest": digest,
                "updatedAt": applied_at,
            }
        )

    for index, scenario in enumerate(SEED_SCENARIOS, start=1):
        order_reference = str(scenario["orderReference"])
        customer_reference = str(scenario["customerReference"])
        sku = str(scenario["sku"])
        customer = customer_by_id[customer_reference]
        product = product_by_id[sku]
        days = scenario["daysSinceDelivery"]
        delivered_at = applied_at - timedelta(days=int(days)) if isinstance(days, int) else None
        tracking_reference = f"SHIP-{order_reference}"
        sales_inventory.append(
            {
                "_id": f"SANDBOX*{order_reference}",
                "salesHdrEventData": {
                    "orderId": order_reference,
                    "srcErp": "OMC",
                    "srcSysCode": "SANDBOX",
                    "orderStatus": scenario["orderStatus"],
                    "sellWhseId": "WH-CHENNAI-01",
                    "shipFromWhseId": "WH-CHENNAI-01",
                },
                "salesHdr": {
                    "salesHdrData": {
                        "custId": customer_reference,
                        "custName": customer["name"],
                    },
                    "shipping": {
                        "shipViaCode": "PPL",
                        "shipViaDesc": "Prepaid parcel label",
                    },
                },
                "salesLines": [
                    {
                        "lineData": {
                            "orderLineId": f"{order_reference}:LINE:1",
                            "productId": sku,
                            "masterProductId": f"MASTER-{sku}",
                            "sku": sku,
                            "productDesc": product["name"],
                            "productType": (
                                "BULKY" if product["category"] == "INDUSTRIAL" else "STANDARD"
                            ),
                            "orderQty": 1,
                            "shipQty": 1,
                        }
                    }
                ],
                "deliveredAt": delivered_at,
                "scenarioId": scenario["id"],
                "expectedReasonCode": scenario["reasonCode"],
                "expectedDecision": scenario["expectedDecision"],
                "seedVersion": seed_version,
                "seedDigest": digest,
                "updatedAt": applied_at,
            }
        )
        shipments.append(
            {
                "_id": tracking_reference,
                "shipmentInfoEventData": {
                    "trkNum": tracking_reference,
                    "trilOrdNum": order_reference,
                    "carrierCode": "UPS",
                    "shippedAt": (
                        delivered_at - timedelta(days=2) if delivered_at is not None else applied_at
                    ),
                },
                "seedVersion": seed_version,
                "seedDigest": digest,
                "updatedAt": applied_at,
                "ordinal": index,
            }
        )

    return {
        "customerOutboundCDM": customers,
        "lkpSearchProduct": products,
        "salesInv": sales_inventory,
        "shipmentInfo": shipments,
    }
