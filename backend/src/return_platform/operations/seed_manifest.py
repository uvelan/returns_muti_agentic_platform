"""Deterministic, production-like synthetic seed manifest for return scenarios."""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, cast

SEED_PROFILE: Final = "operational-realistic-1250-v1"
SEED_NUMBER: Final = 20260729
SEED_CUSTOMER_COUNT: Final = 400
SEED_PRODUCT_COUNT: Final = 500
SEED_ORDER_COUNT: Final = 1250
SEED_RUN_ID: Final = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SEED_PROFILE}:{SEED_NUMBER}"))

_FIRST_NAMES: Final = (
    "Avery",
    "Blake",
    "Cameron",
    "Dakota",
    "Elliot",
    "Finley",
    "Harper",
    "Jordan",
    "Kai",
    "Logan",
    "Morgan",
    "Noah",
    "Parker",
    "Quinn",
    "Reese",
    "Riley",
    "Sawyer",
    "Taylor",
    "Emerson",
    "Rowan",
)
_LAST_NAMES: Final = (
    "Bennett",
    "Brooks",
    "Carter",
    "Collins",
    "Cooper",
    "Diaz",
    "Edwards",
    "Foster",
    "Garcia",
    "Hayes",
    "Howard",
    "Jenkins",
    "Kelly",
    "Lee",
    "Mitchell",
    "Morgan",
    "Parker",
    "Reed",
    "Sullivan",
    "Turner",
)
_REGIONS: Final = (
    "NORTHEAST",
    "MID_ATLANTIC",
    "SOUTHEAST",
    "GREAT_LAKES",
    "MIDWEST",
    "SOUTH_CENTRAL",
    "MOUNTAIN",
    "WEST_COAST",
)
_BRANCHES: Final = tuple(f"BR-{index:03d}" for index in range(1, 21))
_WAREHOUSES: Final = tuple(f"WH-{index:03d}" for index in range(1, 9))
_CATEGORIES: Final = (
    "PLUMBING",
    "HVAC",
    "WATERWORKS",
    "INDUSTRIAL",
    "VALVES",
    "PUMPS",
    "SAFETY",
    "ELECTRICAL",
    "CONTROLS",
    "TOOLS",
    "FASTENERS",
    "PIPE_AND_FITTINGS",
    "HEATING",
    "APPLIANCES",
    "BUILDING_SUPPLIES",
)
_PRODUCT_NOUNS: Final = (
    "shower valve trim kit",
    "pressure regulator",
    "water heater control",
    "circulation pump",
    "ball valve",
    "gate valve",
    "pipe coupling",
    "compression fitting",
    "drain assembly",
    "faucet cartridge",
    "thermostat",
    "air handler control",
    "safety sensor",
    "pump controller",
    "backflow preventer",
    "expansion tank",
    "filter housing",
    "industrial actuator",
    "electrical disconnect",
    "temperature probe",
    "flow meter",
    "check valve",
    "branch connector",
    "service wrench",
    "mounting bracket",
)
_PRODUCT_ADJECTIVES: Final = (
    "Commercial",
    "Professional",
    "High-flow",
    "Pressure-balanced",
    "Corrosion-resistant",
    "Heavy-duty",
    "Compact",
    "Premium",
    "Low-lead",
    "Energy-efficient",
    "Smart",
    "Universal",
    "Precision",
    "Industrial",
    "Weather-resistant",
    "High-temperature",
    "Stainless-steel",
    "Quick-connect",
    "Service-grade",
    "Contractor",
)
_BRANDS: Final = (
    "Northstar",
    "Flowline",
    "ApexWorks",
    "BlueRidge",
    "IronGate",
    "ClearFlow",
    "ProSource",
    "Vertex",
    "Summit",
    "Reliant",
)
_REASONS_APPROVE: Final = (
    "DAMAGED",
    "DEFECTIVE",
    "WRONG_ITEM",
    "MISSING_PARTS",
    "NOT_AS_DESCRIBED",
)
_REASONS_REJECT: Final = ("EXPIRED_WINDOW", "NOT_DELIVERED", "CUSTOMER_REMORSE")
_REASONS_REVIEW: Final = (
    "FRAUD_SUSPECTED",
    "SERIAL_MISMATCH",
    "DIRECT_VENDOR_REVIEW",
    "HAZMAT_REVIEW",
)


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _product_type(index: int) -> str:
    bucket = index % 100
    if bucket < 72:
        return "STANDARD"
    if bucket < 87:
        return "BULKY"
    if bucket < 95:
        return "LTL_HEAVY"
    return "DIRECT_VENDOR"


def _build_customers() -> tuple[dict[str, object], ...]:
    customers: list[dict[str, object]] = []
    tiers = ("STANDARD", "SILVER", "GOLD", "PLATINUM")
    customer_types = ("COMMERCIAL", "CONTRACTOR", "INSTITUTIONAL", "RETAIL")
    for index in range(SEED_CUSTOMER_COUNT):
        ordinal = index + 1
        first = _FIRST_NAMES[index // len(_LAST_NAMES)]
        last = _LAST_NAMES[index % len(_LAST_NAMES)]
        customers.append(
            {
                "_id": f"CUST-{ordinal:06d}",
                "name": f"{first} {last}",
                "tier": tiers[index % len(tiers)],
                "region": _REGIONS[index % len(_REGIONS)],
                "branchId": _BRANCHES[index % len(_BRANCHES)],
                "customerType": customer_types[index % len(customer_types)],
                "accountStatus": "INACTIVE" if ordinal % 37 == 0 else "ACTIVE",
            }
        )
    return tuple(customers)


def _build_products() -> tuple[dict[str, object], ...]:
    products: list[dict[str, object]] = []
    windows = (15, 30, 45, 60, 90)
    for index in range(SEED_PRODUCT_COUNT):
        ordinal = index + 1
        noun = _PRODUCT_NOUNS[index % len(_PRODUCT_NOUNS)]
        adjective = _PRODUCT_ADJECTIVES[(index // len(_PRODUCT_NOUNS)) % len(_PRODUCT_ADJECTIVES)]
        category = _CATEGORIES[index % len(_CATEGORIES)]
        product_type = _product_type(index)
        base_price = Decimal("2.50") + Decimal((ordinal * 7919) % 1249750) / Decimal("100")
        weight = Decimal("0.25") + Decimal((ordinal * 97) % 24000) / Decimal("100")
        products.append(
            {
                "_id": f"SKU-{ordinal:06d}",
                "name": f"{adjective} {noun}",
                "returnWindowDays": windows[index % len(windows)],
                "category": category,
                "productType": product_type,
                "brand": _BRANDS[index % len(_BRANDS)],
                "unitPrice": _money(base_price),
                "unitWeightLb": _money(weight),
                "hazmat": ordinal % 113 == 0,
                "serialControlled": ordinal % 29 == 0,
            }
        )
    return tuple(products)


def _weighted_choice(rng: random.Random, choices: tuple[str, ...], weights: tuple[int, ...]) -> str:
    return str(rng.choices(choices, weights=weights, k=1)[0])


def _build_scenarios() -> tuple[dict[str, object], ...]:
    rng = random.Random(SEED_NUMBER)
    scenarios: list[dict[str, object]] = []
    statuses = (
        "DELIVERED",
        "IN_TRANSIT",
        "PARTIALLY_SHIPPED",
        "READY_FOR_PICKUP",
        "CANCELLED",
        "PROCESSING",
    )
    status_weights = (72, 8, 6, 5, 4, 5)
    decisions = ("APPROVE", "REJECT", "REVIEW_REQUIRED")
    decision_weights = (55, 25, 20)
    for index in range(SEED_ORDER_COUNT):
        ordinal = index + 1
        status = _weighted_choice(rng, statuses, status_weights)
        age_bucket = _weighted_choice(rng, ("RECENT", "MID", "OLDER"), (70, 20, 10))
        age_ranges = {"RECENT": (1, 90), "MID": (91, 180), "OLDER": (181, 365)}
        age_min, age_max = age_ranges[age_bucket]
        age_days = rng.randint(age_min, age_max)
        decision = _weighted_choice(rng, decisions, decision_weights)
        if status in {
            "IN_TRANSIT",
            "PARTIALLY_SHIPPED",
            "READY_FOR_PICKUP",
            "CANCELLED",
            "PROCESSING",
        }:
            decision = "REJECT"
        if decision == "APPROVE":
            reason = str(rng.choice(_REASONS_APPROVE))
        elif decision == "REVIEW_REQUIRED":
            reason = str(rng.choice(_REASONS_REVIEW))
        else:
            reason = "NOT_DELIVERED" if status != "DELIVERED" else str(rng.choice(_REASONS_REJECT))
        line_count = rng.randint(1, 5)
        line_skus = tuple(
            f"SKU-{product_index:06d}"
            for product_index in rng.sample(range(1, SEED_PRODUCT_COUNT + 1), k=line_count)
        )
        product_types = [
            str(SEED_PRODUCTS[int(sku.rsplit("-", 1)[-1]) - 1]["productType"]) for sku in line_skus
        ]
        if "DIRECT_VENDOR" in product_types:
            fulfillment_path = "DIRECT_VENDOR"
        elif "LTL_HEAVY" in product_types:
            fulfillment_path = "OFFSITE_HEAVY" if ordinal % 2 else "BRANCH_LTL"
        else:
            fulfillment_path = "OFFSITE_PARCEL" if ordinal % 3 else "BRANCH_PARCEL"
        scenarios.append(
            {
                "id": f"SCENARIO-{ordinal:06d}",
                "orderReference": f"SO-2026-{ordinal:06d}",
                "customerReference": f"CUST-{rng.randint(1, SEED_CUSTOMER_COUNT):06d}",
                "orderStatus": status,
                "daysSinceDelivery": age_days if status == "DELIVERED" else None,
                "orderAgeDays": age_days,
                "reasonCode": reason,
                "expectedDecision": decision,
                "sku": line_skus[0],
                "lineSkus": line_skus,
                "fulfillmentPath": fulfillment_path,
                "sellingBranch": _BRANCHES[index % len(_BRANCHES)],
                "sellingWarehouse": _WAREHOUSES[index % len(_WAREHOUSES)],
            }
        )
    return tuple(scenarios)


SEED_CUSTOMERS: Final[tuple[dict[str, object], ...]] = _build_customers()
SEED_PRODUCTS: Final[tuple[dict[str, object], ...]] = _build_products()
SEED_SCENARIOS: Final[tuple[dict[str, object], ...]] = _build_scenarios()


def manifest_payload(seed_version: str) -> dict[str, object]:
    return {
        "seedVersion": seed_version,
        "seedProfile": SEED_PROFILE,
        "seedNumber": SEED_NUMBER,
        "customers": SEED_CUSTOMERS,
        "products": SEED_PRODUCTS,
        "scenarios": SEED_SCENARIOS,
    }


def manifest_digest(seed_version: str) -> str:
    canonical = json.dumps(manifest_payload(seed_version), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _seed_metadata(
    seed_version: str, digest: str, ordinal: int, applied_at: datetime
) -> dict[str, object]:
    return {
        "seedProfile": SEED_PROFILE,
        "seedRunId": SEED_RUN_ID,
        "seedVersion": seed_version,
        "seedDigest": digest,
        "seedOrdinal": ordinal,
        "generatedAt": applied_at,
    }


def _materialize_lines(scenario: dict[str, object], order_ordinal: int) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    line_skus = cast(tuple[str, ...], scenario["lineSkus"])
    for line_index, sku_value in enumerate(line_skus, start=1):
        sku = str(sku_value)
        product = SEED_PRODUCTS[int(sku.rsplit("-", 1)[-1]) - 1]
        quantity = 1 + ((order_ordinal + line_index) % 6)
        unit_price = Decimal(str(product["unitPrice"]))
        extended = unit_price * Decimal(quantity)
        discount = (
            extended * Decimal("0.05") if (order_ordinal + line_index) % 11 == 0 else Decimal("0")
        )
        tax = (extended - discount) * Decimal("0.075")
        lines.append(
            {
                "itemReference": f"{scenario['orderReference']}:LINE:{line_index}",
                "sku": sku,
                "productReference": sku,
                "productDescription": product["name"],
                "productType": product["productType"],
                "quantity": quantity,
                "unitPrice": _money(unit_price),
                "extendedPrice": _money(extended),
                "discount": _money(discount),
                "tax": _money(tax),
                "lineTotal": _money(extended - discount + tax),
            }
        )
    return lines


def materialize_seed(
    seed_version: str, applied_at: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    digest = manifest_digest(seed_version)
    customers = [
        {**item, **_seed_metadata(seed_version, digest, index, applied_at)}
        for index, item in enumerate(SEED_CUSTOMERS, start=1)
    ]
    products = [
        {**item, **_seed_metadata(seed_version, digest, index, applied_at)}
        for index, item in enumerate(SEED_PRODUCTS, start=1)
    ]
    orders: list[dict[str, Any]] = []
    for index, scenario in enumerate(SEED_SCENARIOS, start=1):
        order_age_days = cast(int, scenario["orderAgeDays"])
        order_created_at = applied_at - timedelta(days=order_age_days)
        days = scenario["daysSinceDelivery"]
        delivered_at = applied_at - timedelta(days=int(days)) if isinstance(days, int) else None
        lines = _materialize_lines(scenario, index)
        orders.append(
            {
                "_id": scenario["orderReference"],
                "customerReference": scenario["customerReference"],
                "status": scenario["orderStatus"],
                "orderCreatedAt": order_created_at,
                "deliveredAt": delivered_at,
                "items": lines,
                "scenarioId": scenario["id"],
                "expectedReasonCode": scenario["reasonCode"],
                "expectedDecision": scenario["expectedDecision"],
                "fulfillmentPath": scenario["fulfillmentPath"],
                **_seed_metadata(seed_version, digest, index, applied_at),
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


def seed_counts() -> dict[str, int]:
    return {
        "customers": len(SEED_CUSTOMERS),
        "products": len(SEED_PRODUCTS),
        "orders": len(SEED_SCENARIOS),
        "orderLines": sum(len(cast(tuple[str, ...], item["lineSkus"])) for item in SEED_SCENARIOS),
        "shipments": len(SEED_SCENARIOS),
    }


def materialize_domain_seed(
    seed_version: str,
    applied_at: datetime,
) -> dict[str, list[dict[str, Any]]]:
    digest = manifest_digest(seed_version)
    customer_by_id = {str(item["_id"]): item for item in SEED_CUSTOMERS}
    sales_inventory: list[dict[str, Any]] = []
    shipments: list[dict[str, Any]] = []
    customers: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    for index, customer in enumerate(SEED_CUSTOMERS, start=1):
        customer_id = str(customer["_id"])
        area_codes = (202, 212, 312, 415)
        area_code = area_codes[(index - 1) // 100]
        phone_suffix = 100 + ((index - 1) % 100)
        account = {"accountNumber": f"ACCT-{index:06d}", "status": customer["accountStatus"]}
        customers.append(
            {
                "_id": customer_id,
                "partyId": f"PARTY-{index:06d}",
                "customerId": customer_id,
                "customerName": customer["name"],
                "phoneNumber": f"+1-{area_code}-555-{phone_suffix:04d}",
                "email": f"customer.{index:06d}@example.invalid",
                "accounts": [account],
                "custAccts": [account],
                "customerType": customer["customerType"],
                "tier": customer["tier"],
                "region": customer["region"],
                "branchId": customer["branchId"],
                "postalCode": f"{30000 + (index * 37) % 69999:05d}",
                "updatedAt": applied_at,
                **_seed_metadata(seed_version, digest, index, applied_at),
            }
        )
    for index, product in enumerate(SEED_PRODUCTS, start=1):
        sku = str(product["_id"])
        products.append(
            {
                "_id": sku,
                "productId": sku,
                "sku": sku,
                "masterProductId": f"MASTER-{index:06d}",
                "productDescription": product["name"],
                "productType": product["productType"],
                "category": product["category"],
                "brand": product["brand"],
                "returnWindowDays": product["returnWindowDays"],
                "unitPrice": product["unitPrice"],
                "unitWeightLb": product["unitWeightLb"],
                "hazmat": product["hazmat"],
                "serialControlled": product["serialControlled"],
                "updatedAt": applied_at,
                **_seed_metadata(seed_version, digest, index, applied_at),
            }
        )
    for index, scenario in enumerate(SEED_SCENARIOS, start=1):
        order_reference = str(scenario["orderReference"])
        customer_reference = str(scenario["customerReference"])
        customer = customer_by_id[customer_reference]
        order_age_days = cast(int, scenario["orderAgeDays"])
        order_created_at = applied_at - timedelta(days=order_age_days)
        days = scenario["daysSinceDelivery"]
        delivered_at = applied_at - timedelta(days=int(days)) if isinstance(days, int) else None
        lines = _materialize_lines(scenario, index)
        sales_lines = [
            {
                "lineData": {
                    "orderLineId": line["itemReference"],
                    "productId": line["productReference"],
                    "masterProductId": f"MASTER-{int(str(line['sku']).rsplit('-', 1)[-1]):06d}",
                    "sku": line["sku"],
                    "productDesc": line["productDescription"],
                    "productType": line["productType"],
                    "orderQty": line["quantity"],
                    "shipQty": line["quantity"]
                    if scenario["orderStatus"] in {"DELIVERED", "IN_TRANSIT", "PARTIALLY_SHIPPED"}
                    else 0,
                    "unitPrice": line["unitPrice"],
                    "extendedPrice": line["extendedPrice"],
                    "discountAmount": line["discount"],
                    "taxAmount": line["tax"],
                    "lineTotal": line["lineTotal"],
                }
            }
            for line in lines
        ]
        subtotal = _money(
            sum(
                (Decimal(str(line["extendedPrice"])) for line in lines),
                Decimal("0"),
            )
        )
        tax = _money(
            sum(
                (Decimal(str(line["tax"])) for line in lines),
                Decimal("0"),
            )
        )
        discount = _money(
            sum(
                (Decimal(str(line["discount"])) for line in lines),
                Decimal("0"),
            )
        )
        shipping = 0.0 if subtotal >= 500 else _money(Decimal("12.50") + Decimal(index % 15))
        total = _money(
            Decimal(str(subtotal))
            + Decimal(str(tax))
            + Decimal(str(shipping))
            - Decimal(str(discount))
        )
        fulfillment_path = str(scenario["fulfillmentPath"])
        ship_code = "LTL" if "LTL" in fulfillment_path or "HEAVY" in fulfillment_path else "PPL"
        sales_inventory.append(
            {
                "_id": f"SYNTHETIC*{order_reference}",
                "salesHdrEventData": {
                    "orderId": order_reference,
                    "srcErp": "OMC",
                    "srcSysCode": "SYNTHETIC_OPERATIONAL",
                    "orderStatus": scenario["orderStatus"],
                    "sellWhseId": scenario["sellingBranch"],
                    "shipFromWhseId": scenario["sellingWarehouse"],
                    "orderCreatedAt": order_created_at,
                },
                "salesHdr": {
                    "salesHdrData": {
                        "custId": customer_reference,
                        "custName": customer["name"],
                        "subtotal": subtotal,
                        "taxAmount": tax,
                        "shippingAmount": shipping,
                        "discountAmount": discount,
                        "orderTotal": total,
                    },
                    "shipping": {
                        "shipViaCode": ship_code,
                        "shipViaDesc": fulfillment_path.replace("_", " ").title(),
                    },
                },
                "salesLines": sales_lines,
                "deliveredAt": delivered_at,
                "scenarioId": scenario["id"],
                "expectedReasonCode": scenario["reasonCode"],
                "expectedDecision": scenario["expectedDecision"],
                "fulfillmentPath": fulfillment_path,
                "updatedAt": applied_at,
                **_seed_metadata(seed_version, digest, index, applied_at),
            }
        )
        carrier = "LTL" if ship_code == "LTL" else ("UPS" if index % 2 else "FEDEX")
        tracking = (
            f"LTL-2026-{index:06d}"
            if carrier == "LTL"
            else (f"1Z999AA1{index:010d}" if carrier == "UPS" else f"FDX{index:012d}")
        )
        shipped_at = (
            (delivered_at - timedelta(days=2))
            if delivered_at is not None
            else order_created_at + timedelta(days=1)
        )
        shipments.append(
            {
                "_id": f"SHIP-{order_reference}",
                "shipmentInfoEventData": {
                    "trkNum": tracking,
                    "trilOrdNum": order_reference,
                    "carrierCode": carrier,
                    "shippedAt": shipped_at,
                    "shipmentStatus": scenario["orderStatus"],
                },
                "updatedAt": applied_at,
                **_seed_metadata(seed_version, digest, index, applied_at),
            }
        )
    return {
        "customerOutboundCDM": customers,
        "lkpSearchProduct": products,
        "salesInv": sales_inventory,
        "shipmentInfo": shipments,
    }
