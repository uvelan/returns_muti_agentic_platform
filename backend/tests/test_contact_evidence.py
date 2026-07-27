"""Contact evidence must be normalized, keyed, and domain-separated."""

from __future__ import annotations

import pytest

from return_platform.security.contact_evidence import contact_lookup_digest

KEY = "a-production-grade-contact-evidence-key-with-more-than-32-bytes"


def test_phone_evidence_normalizes_formatting() -> None:
    assert contact_lookup_digest("+1 (214) 555-0100", "PHONE", KEY) == (
        contact_lookup_digest("12145550100", "PHONE", KEY)
    )


def test_email_evidence_normalizes_case() -> None:
    assert contact_lookup_digest(" Person@Example.COM ", "EMAIL", KEY) == (
        contact_lookup_digest("person@example.com", "EMAIL", KEY)
    )


def test_contact_evidence_is_domain_separated() -> None:
    value = "12145550100"
    assert contact_lookup_digest(value, "PHONE", KEY) != contact_lookup_digest(
        value,
        "EMAIL",
        KEY,
    )


def test_contact_evidence_rejects_weak_key() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        contact_lookup_digest("person@example.com", "EMAIL", "weak")
