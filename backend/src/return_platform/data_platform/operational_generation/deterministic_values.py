import hashlib
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

OPERATIONAL_GENERATION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_OID, "operational.returns.platform")

_SYNTHETIC_FIRST_NAMES = (
    "Aarav",
    "Aisha",
    "Amara",
    "Anika",
    "Arjun",
    "Caleb",
    "Camila",
    "Chloe",
    "Daniel",
    "Elena",
    "Ethan",
    "Fatima",
    "Grace",
    "Hana",
    "Isaac",
    "Ishaan",
    "Jasmine",
    "Jonah",
    "Leila",
    "Liam",
    "Maya",
    "Mateo",
    "Nadia",
    "Noah",
    "Olivia",
    "Priya",
    "Ravi",
    "Sofia",
    "Theo",
    "Valeria",
    "Yara",
    "Zain",
)

_SYNTHETIC_LAST_NAMES = (
    "Bennett",
    "Chandra",
    "Chen",
    "Costa",
    "Das",
    "Desai",
    "Diaz",
    "Foster",
    "Garcia",
    "Gupta",
    "Haddad",
    "Hughes",
    "Ibrahim",
    "Johnson",
    "Kapoor",
    "Kim",
    "Kumar",
    "Lopez",
    "Martin",
    "Mehta",
    "Morgan",
    "Nair",
    "Nguyen",
    "Okafor",
    "Patel",
    "Reed",
    "Rivera",
    "Sato",
    "Shah",
    "Singh",
    "Thomas",
    "Wilson",
)


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


def get_synthetic_name(stable_number: int, *, seed: int = 0) -> str:
    """Return a stable, realistic-looking identity assembled from synthetic name pools."""

    digest = hashlib.sha256(f"{seed}:{stable_number}:synthetic-name".encode()).digest()
    first_name = _SYNTHETIC_FIRST_NAMES[int.from_bytes(digest[:4], "big") % len(
        _SYNTHETIC_FIRST_NAMES
    )]
    last_name = _SYNTHETIC_LAST_NAMES[int.from_bytes(digest[4:8], "big") % len(
        _SYNTHETIC_LAST_NAMES
    )]
    return f"{first_name} {last_name}"


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
