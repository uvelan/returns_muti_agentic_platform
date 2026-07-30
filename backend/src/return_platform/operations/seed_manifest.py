"""Deterministic, non-PII demo seed manifest for E2E return scenarios."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, overload


def _seed_manifest_path() -> Path:
    source_file = globals().get("__file__")
    candidates = [
        (
            Path(str(source_file)).resolve().parents[3]
            / "config"
            / "seed"
            / "e2e_seed_manifest.json"
            if source_file
            else None
        ),
        Path.cwd() / "backend" / "config" / "seed" / "e2e_seed_manifest.json",
        Path.cwd() / "config" / "seed" / "e2e_seed_manifest.json",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise FileNotFoundError("The configured JSON seed manifest could not be located.")


SEED_MANIFEST_PATH: Final = _seed_manifest_path()


def _load_seed_configuration() -> dict[str, Any]:
    with SEED_MANIFEST_PATH.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or value.get("schemaVersion") != "1.0":
        raise ValueError("Seed JSON manifest has an unsupported schema.")
    return value


_SEED_CONFIGURATION: Final = _load_seed_configuration()
_COUNTS = _SEED_CONFIGURATION["counts"]
_RUNTIME_OPTIONS = _SEED_CONFIGURATION.get("runtimeOptions", {})
_CUSTOMER_CATALOG = _SEED_CONFIGURATION["customerCatalog"]
_PRODUCT_CATALOG = _SEED_CONFIGURATION["productCatalog"]


def _effective_counts() -> dict[str, int]:
    configured = {name: int(_COUNTS[name]) for name in ("customers", "products", "orders")}
    environment_variable = str(
        _RUNTIME_OPTIONS.get("recordLimitEnvironmentVariable", "")
    ).strip()
    if not environment_variable:
        return configured
    raw_limit = os.getenv(environment_variable, "").strip()
    if not raw_limit:
        return configured
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise ValueError(f"{environment_variable} must be an integer.") from error
    minimum = int(_RUNTIME_OPTIONS.get("minimumRecordLimit", 1))
    if limit < minimum:
        raise ValueError(f"{environment_variable} must be at least {minimum}.")
    return {name: min(count, limit) for name, count in configured.items()}


_EFFECTIVE_COUNTS: Final = _effective_counts()
SEED_CUSTOMER_COUNT: Final = _EFFECTIVE_COUNTS["customers"]
SEED_PRODUCT_COUNT: Final = _EFFECTIVE_COUNTS["products"]
SEED_ORDER_COUNT: Final = _EFFECTIVE_COUNTS["orders"]
MINIMUM_SEED_RECORD_LIMIT: Final = int(_RUNTIME_OPTIONS.get("minimumRecordLimit", 1))
MAXIMUM_SEED_RECORD_LIMIT: Final = max(int(value) for value in _COUNTS.values())
_FIRST_NAMES: Final = tuple(str(value) for value in _CUSTOMER_CATALOG["firstNames"])
_LAST_NAMES: Final = tuple(str(value) for value in _CUSTOMER_CATALOG["lastNames"])
_REGIONS: Final = tuple(str(value) for value in _CUSTOMER_CATALOG["regions"])
_TIERS: Final = tuple(str(value) for value in _CUSTOMER_CATALOG["tiers"])
_PRODUCT_NOUNS: Final = tuple(str(value) for value in _PRODUCT_CATALOG["nouns"])
_PRODUCT_QUALIFIERS: Final = tuple(str(value) for value in _PRODUCT_CATALOG["qualifiers"])
_PRODUCT_CATEGORIES: Final = tuple(str(value) for value in _PRODUCT_CATALOG["categories"])
_RETURN_WINDOWS: Final = tuple(int(value) for value in _PRODUCT_CATALOG["returnWindowDays"])


def _customers() -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for index in range(SEED_CUSTOMER_COUNT):
        ordinal = index + 1001
        first_name = _FIRST_NAMES[index % len(_FIRST_NAMES)]
        last_name = _LAST_NAMES[(index // len(_FIRST_NAMES) + index) % len(_LAST_NAMES)]
        rows.append(
            {
                "_id": f"CUST-{ordinal}",
                "name": f"{first_name} {last_name}",
                "tier": _TIERS[index % len(_TIERS)],
                "region": _REGIONS[index % len(_REGIONS)],
            }
        )
    return tuple(rows)


def _products() -> tuple[dict[str, object], ...]:
    fixed: list[dict[str, object]] = [
        dict(value) for value in _PRODUCT_CATALOG["fixedProducts"] if isinstance(value, dict)
    ][:SEED_PRODUCT_COUNT]
    for index in range(len(fixed) + 1, SEED_PRODUCT_COUNT + 1):
        noun = _PRODUCT_NOUNS[index % len(_PRODUCT_NOUNS)]
        qualifier = _PRODUCT_QUALIFIERS[(index // len(_PRODUCT_NOUNS)) % len(_PRODUCT_QUALIFIERS)]
        fixed.append(
            {
                "_id": f"SKU-{index:04d}",
                "name": f"{qualifier} {noun} {index:04d}",
                "returnWindowDays": _RETURN_WINDOWS[index % len(_RETURN_WINDOWS)],
                "category": _PRODUCT_CATEGORIES[index % len(_PRODUCT_CATEGORIES)],
            }
        )
    return tuple(fixed)


SEED_CUSTOMERS: Final[tuple[dict[str, object], ...]] = _customers()
SEED_PRODUCTS: Final[tuple[dict[str, object], ...]] = _products()

SEED_SCENARIOS: Final[tuple[dict[str, object], ...]] = tuple(
    dict(value) for value in _SEED_CONFIGURATION["scenarios"] if isinstance(value, dict)
)


class GeneratedSequence[T](Sequence[T]):
    """Indexable deterministic records generated only when a batch is requested."""

    def __init__(self, count: int, builder: Callable[[int], T]) -> None:
        self._count = count
        self._builder = builder

    def __len__(self) -> int:
        return self._count

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> T | list[T]:
        if isinstance(index, slice):
            return [self._builder(position) for position in range(*index.indices(self._count))]
        normalized = index + self._count if index < 0 else index
        if normalized < 0 or normalized >= self._count:
            raise IndexError(index)
        return self._builder(normalized)

    def __iter__(self) -> Iterator[T]:
        return (self._builder(index) for index in range(self._count))


def _order(index: int) -> dict[str, object]:
    if index < len(SEED_SCENARIOS):
        return dict(SEED_SCENARIOS[index])
    position = index + 1
    return {
        "id": f"DATA-{position:07d}",
        "orderReference": f"SO-2026-{position:07d}",
        "customerReference": str(SEED_CUSTOMERS[index % SEED_CUSTOMER_COUNT]["_id"]),
        "orderStatus": "DELIVERED",
        "daysSinceDelivery": 1 + (position % 28),
        "reasonCode": "DAMAGED",
        "expectedDecision": "APPROVE",
        "sku": str(SEED_PRODUCTS[index % SEED_PRODUCT_COUNT]["_id"]),
        "additionalSku": (
            str(SEED_PRODUCTS[position % SEED_PRODUCT_COUNT]["_id"]) if position % 4 == 0 else None
        ),
    }


SEED_ORDERS: Final[Sequence[dict[str, object]]] = GeneratedSequence(
    SEED_ORDER_COUNT,
    _order,
)


def effective_seed_counts(record_limit: int | None = None) -> dict[str, int]:
    """Return configured counts capped by a validated per-operation limit."""
    if record_limit is None:
        return dict(_EFFECTIVE_COUNTS)
    if record_limit < MINIMUM_SEED_RECORD_LIMIT:
        raise ValueError(
            f"recordLimit must be at least {MINIMUM_SEED_RECORD_LIMIT}."
        )
    if record_limit > MAXIMUM_SEED_RECORD_LIMIT:
        raise ValueError(
            f"recordLimit must not exceed {MAXIMUM_SEED_RECORD_LIMIT}."
        )
    return {
        name: min(configured, record_limit)
        for name, configured in _EFFECTIVE_COUNTS.items()
    }


def manifest_payload(
    seed_version: str,
    record_limit: int | None = None,
) -> dict[str, object]:
    """Return the immutable manifest used for stable evidence calculation."""
    return {
        "seedVersion": seed_version,
        "configuration": _SEED_CONFIGURATION,
        "effectiveCounts": effective_seed_counts(record_limit),
    }


@lru_cache(maxsize=64)
def manifest_digest(
    seed_version: str,
    evidence_hmac_key: str,
    record_limit: int | None = None,
) -> str:
    """Return keyed evidence identity without persisting or exposing the runtime key."""
    canonical = json.dumps(
        manifest_payload(seed_version, record_limit),
        separators=(",", ":"),
        sort_keys=True,
    )
    return hmac.new(
        evidence_hmac_key.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _order_items(order: dict[str, object]) -> list[dict[str, object]]:
    skus = [str(order["sku"])]
    additional_sku = order.get("additionalSku")
    if additional_sku:
        skus.append(str(additional_sku))
    return [
        {
            "itemReference": f"LINE-{position}",
            "sku": sku,
            "quantity": 1 + ((position + len(str(order["orderReference"]))) % 3),
            "unitPrice": float(50 + (position * 25)),
        }
        for position, sku in enumerate(skus, start=1)
    ]


def seed_order_line_count(record_limit: int | None = None) -> int:
    order_count = effective_seed_counts(record_limit)["orders"]
    scenario_count = min(order_count, len(SEED_SCENARIOS))
    scenario_extra_lines = sum(
        1 for item in SEED_SCENARIOS[:scenario_count] if item.get("additionalSku")
    )
    generated_extra_lines = max(
        0,
        (order_count // 4) - (len(SEED_SCENARIOS) // 4),
    )
    return order_count + scenario_extra_lines + generated_extra_lines


def materialize_seed(
    seed_version: str,
    applied_at: datetime,
    evidence_hmac_key: str,
    record_limit: int | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    Sequence[dict[str, Any]],
]:
    """Materialize source documents while keeping the manifest evidence stable."""
    counts = effective_seed_counts(record_limit)
    digest = manifest_digest(seed_version, evidence_hmac_key, record_limit)
    customers = [
        {**item, "seedVersion": seed_version, "seedDigest": digest}
        for item in SEED_CUSTOMERS[: counts["customers"]]
    ]
    products = [
        {**item, "seedVersion": seed_version, "seedDigest": digest}
        for item in SEED_PRODUCTS[: counts["products"]]
    ]

    def build_order(index: int) -> dict[str, Any]:
        order = SEED_ORDERS[index]
        days = order["daysSinceDelivery"]
        delivered_at = applied_at - timedelta(days=int(days)) if isinstance(days, int) else None
        return {
            "_id": order["orderReference"],
            "customerReference": order["customerReference"],
            "status": order["orderStatus"],
            "deliveredAt": delivered_at,
            "items": _order_items(order),
            "scenarioId": order["id"],
            "expectedReasonCode": order["reasonCode"],
            "expectedDecision": order["expectedDecision"],
            "seedVersion": seed_version,
            "seedDigest": digest,
        }

    orders: Sequence[dict[str, Any]] = GeneratedSequence(counts["orders"], build_order)
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
    evidence_hmac_key: str,
    record_limit: int | None = None,
) -> dict[str, Sequence[dict[str, Any]]]:
    """Materialize the HLD source collections with coherent cross-collection keys."""
    counts = effective_seed_counts(record_limit)
    digest = manifest_digest(seed_version, evidence_hmac_key, record_limit)
    selected_customers = SEED_CUSTOMERS[: counts["customers"]]
    selected_products = SEED_PRODUCTS[: counts["products"]]
    customer_by_id = {str(item["_id"]): item for item in selected_customers}
    product_by_id = {str(item["_id"]): item for item in selected_products}
    customers: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []

    for customer in selected_customers:
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

    for product in selected_products:
        sku = str(product["_id"])
        products.append(
            {
                "_id": sku,
                "productId": sku,
                "sku": sku,
                "masterProductId": f"MASTER-{sku}",
                "productDescription": product["name"],
                "productType": ("BULKY" if product["category"] == "INDUSTRIAL" else "STANDARD"),
                "category": product["category"],
                "returnWindowDays": product["returnWindowDays"],
                "seedVersion": seed_version,
                "seedDigest": digest,
                "updatedAt": applied_at,
            }
        )

    def build_sales_inventory(index: int) -> dict[str, Any]:
        order = SEED_ORDERS[index]
        ordinal = index + 1
        order_reference = str(order["orderReference"])
        customer_reference = str(order["customerReference"])
        customer = customer_by_id[customer_reference]
        days = order["daysSinceDelivery"]
        delivered_at = applied_at - timedelta(days=int(days)) if isinstance(days, int) else None
        line_documents: list[dict[str, Any]] = []
        for position, item in enumerate(_order_items(order), start=1):
            sku = str(item["sku"])
            product = product_by_id[sku]
            line_documents.append(
                {
                    "lineData": {
                        "orderLineId": f"{order_reference}:LINE:{position}",
                        "productId": sku,
                        "masterProductId": f"MASTER-{sku}",
                        "sku": sku,
                        "productDesc": product["name"],
                        "productType": (
                            "BULKY" if product["category"] == "INDUSTRIAL" else "STANDARD"
                        ),
                        "orderQty": item["quantity"],
                        "shipQty": item["quantity"],
                    }
                }
            )
        return {
            "_id": f"SANDBOX*{order_reference}",
            "salesHdrEventData": {
                "orderId": order_reference,
                "srcErp": "OMC",
                "srcSysCode": "SANDBOX",
                "orderStatus": order["orderStatus"],
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
            "salesLines": line_documents,
            "deliveredAt": delivered_at,
            "scenarioId": order["id"],
            "expectedReasonCode": order["reasonCode"],
            "expectedDecision": order["expectedDecision"],
            "seedVersion": seed_version,
            "seedDigest": digest,
            "updatedAt": applied_at,
            "ordinal": ordinal,
        }

    def build_shipment(index: int) -> dict[str, Any]:
        order = SEED_ORDERS[index]
        ordinal = index + 1
        days = order["daysSinceDelivery"]
        delivered_at = applied_at - timedelta(days=int(days)) if isinstance(days, int) else None
        tracking_reference = f"1Z99{ordinal:014d}"
        return {
            "_id": tracking_reference,
            "shipmentInfoEventData": {
                "trkNum": tracking_reference,
                "trilOrdNum": str(order["orderReference"]),
                "carrierCode": "UPS",
                "shippedAt": (
                    delivered_at - timedelta(days=2) if delivered_at is not None else applied_at
                ),
            },
            "seedVersion": seed_version,
            "seedDigest": digest,
            "updatedAt": applied_at,
            "ordinal": ordinal,
        }

    sales_inventory: Sequence[dict[str, Any]] = GeneratedSequence(
        counts["orders"],
        build_sales_inventory,
    )
    shipments: Sequence[dict[str, Any]] = GeneratedSequence(
        counts["orders"],
        build_shipment,
    )

    return {
        "customerOutboundCDM": customers,
        "lkpSearchProduct": products,
        "salesInv": sales_inventory,
        "shipmentInfo": shipments,
    }
