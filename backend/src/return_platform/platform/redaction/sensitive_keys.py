"""Which field names carry personal data. One list, one predicate, one home.

The list itself is not new -- W0.4 wrote it at the provider boundary. What is new
is that it lives in exactly one place. It had already been copied: `ai/gateway/
redaction.py` declared it and claimed in its own docstring to share it with
`AIGatewayService`, while `service.py` carried a byte-identical private copy a
hundred lines away. Two lists that agree today are one commit from disagreeing,
and the failure mode is silent -- the entry point with the shorter list simply
stops recognising a field as sensitive.

It sits under `platform/` rather than under `ai/` because the analyzer needs the
same answer and may not import `ai` (design doc 2.7, enforced by
`tests/graph_schema_analyzer/test_independence.py`). Putting the policy here is
what lets W4.6 mask analyzer samples with the *same* notion of "sensitive" the
provider boundary already uses, instead of a second list that drifts from it.

**Deciding what is sensitive and deciding what to do about it are separate
questions, and only the first one is here.** The provider payload replaces a
sensitive scalar with a constant; an analyzer sample replaces it with a
structure-preserving surrogate (`sample_masking`), because a schema analysis that
cannot see which rows share a value cannot infer a key or a join. Those are
genuinely different answers to "what to do", and forcing them to share one would
break one of the two callers. They share the part that must not differ.
"""

from __future__ import annotations

from typing import Final

__all__ = ["SENSITIVE_KEY_FRAGMENTS", "is_sensitive_key", "normalize_key"]

#: Matched as substrings against a normalized key, so `customer_name`,
#: `customerName` and `CUSTOMER-NAME` are all caught by `name`.
SENSITIVE_KEY_FRAGMENTS: Final[tuple[str, ...]] = (
    "name",
    "email",
    "phone",
    "address",
    "password",
    "secret",
    "token",
    "ssn",
    "aadhaar",
    "pan",
    "card",
    "cvv",
)


def normalize_key(key: str) -> str:
    """Case and separators removed, so one fragment covers every spelling.

    Exposed rather than kept private because `AIGatewayService` normalizes a key
    for its own refusal message; sharing the function is what stops the two
    normalizations from diverging while the fragment list stays common.
    """
    return key.lower().replace("_", "").replace("-", "")


def is_sensitive_key(key: str) -> bool:
    """Whether a field of this name may hold personal data.

    Deliberately over-broad: `name` matches `filename` and `pan` matches
    `company`. A false positive costs a masked value the model could have seen;
    a false negative is customer data at a provider. The asymmetry is the whole
    reason the list is fragments rather than exact names.
    """
    normalized = normalize_key(key)
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)
