import hashlib
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

OPERATIONAL_GENERATION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_OID, "operational.returns.platform")


def generate_stable_uuid(seed: int, asset_id: str, record_index: int, role: str) -> uuid.UUID:
    name = f"{seed}:{asset_id}:{record_index}:{role}"
    return uuid.uuid5(OPERATIONAL_GENERATION_NAMESPACE, name)


def generate_stable_string(
    seed: int, asset_id: str, record_index: int, role: str, length: int = 16
) -> str:
    name = f"{seed}:{asset_id}:{record_index}:{role}"
    hash_obj = hashlib.sha256(name.encode("utf-8"))
    return hash_obj.hexdigest()[:length].upper()


def deterministic_random(seed: int, asset_id: str, record_index: int, role: str) -> random.Random:
    name = f"{seed}:{asset_id}:{record_index}:{role}"
    hash_val = int(hashlib.sha256(name.encode("utf-8")).hexdigest(), 16)
    return random.Random(hash_val)


def generate_stable_date(rng: random.Random, date_from: datetime, date_to: datetime) -> datetime:
    delta = date_to - date_from
    random_seconds = rng.randint(0, int(delta.total_seconds()))
    return date_from + timedelta(seconds=random_seconds)


def get_synthetic_email(stable_id: str) -> str:
    return f"generated+{stable_id}@example.invalid"


def get_synthetic_phone(rng: random.Random) -> str:
    # Reserved non-routable number format, like 555-01XX
    suffix = rng.randint(100, 999)
    return f"555-01{suffix}"


def get_synthetic_name(stable_number: int) -> str:
    return f"Synthetic Customer {stable_number}"


def calculate_monetary_values(lines: list[dict[str, Any]]) -> dict[str, float]:
    # line subtotal = quantity * unit price
    order_subtotal = sum(line.get("quantity", 1) * line.get("unit_price", 0.0) for line in lines)
    tax_rate = 0.08  # deterministic configured calculation
    tax = order_subtotal * tax_rate
    shipping = 10.0 if order_subtotal > 0 else 0.0
    discounts = 0.0
    order_total = order_subtotal + tax + shipping - discounts
    return {
        "subtotal": round(order_subtotal, 2),
        "tax": round(tax, 2),
        "shipping": round(shipping, 2),
        "discounts": round(discounts, 2),
        "total": round(order_total, 2),
    }
