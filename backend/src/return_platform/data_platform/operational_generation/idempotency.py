import hashlib


def generate_idempotency_key(proposal_checksum: str, plan_salt: str) -> str:
    raw = f"{proposal_checksum}::{plan_salt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
