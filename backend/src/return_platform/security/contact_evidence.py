"""Non-reversible contact lookup evidence derived with a Vault-managed HMAC key."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Literal

ContactKind = Literal["PHONE", "EMAIL"]


def normalize_contact(value: str, kind: ContactKind) -> str:
    normalized = value.strip().lower()
    if kind == "PHONE":
        normalized = re.sub(r"\D", "", normalized)
    return normalized


def contact_lookup_digest(value: str, kind: ContactKind, key: str) -> str:
    normalized = normalize_contact(value, kind)
    if not normalized:
        raise ValueError("Contact value must not be blank")
    key_bytes = key.encode("utf-8")
    if len(key_bytes) < 32:
        raise ValueError("Contact lookup HMAC key must contain at least 32 bytes")
    payload = f"return-platform/contact-lookup/v1/{kind}:{normalized}".encode()
    return hmac.new(key_bytes, payload, hashlib.sha256).hexdigest()
