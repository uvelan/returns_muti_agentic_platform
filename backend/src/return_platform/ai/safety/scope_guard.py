"""Business-scope enforcement: what the platform is allowed to be asked to do.

Two different questions live here.

``scan_unauthorized_action`` runs over every string, because "issue the refund
yourself" is a scope violation wherever it appears. The platform's AI never creates
an authoritative fact -- an RMA, a refund, a receipt -- and never emits SQL against a
source system; those are the property of the orchestrator and of humans.

``scan_out_of_domain`` runs only over the free-form request fields listed in
``FREE_FORM_KEYS``. Running it everywhere would be actively wrong: a plumbing
catalogue legitimately contains the word "prescription" in a product description,
and blocking a return because of it is a worse failure than the one being prevented.
"""

from __future__ import annotations

import re

__all__ = ["FREE_FORM_KEYS", "scan_out_of_domain", "scan_unauthorized_action"]

_UNAUTHORIZED_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "CREATE_AUTHORITATIVE_FACT",
        re.compile(
            r"\b(create|issue|generate|approve|confirm|mark)\b.{0,60}"
            r"\b(rma|rga|refund|pickup|receipt|license plate|vendor credit)\b",
            re.I | re.S,
        ),
    ),
    (
        "DIRECT_SQL",
        re.compile(
            r"\b(insert|update|delete|drop|alter|execute)\b.{0,40}\b(sql|table|database|omc)\b",
            re.I | re.S,
        ),
    ),
    (
        "BYPASS_HUMAN",
        re.compile(
            r"\b(bypass|skip|avoid|override)\b.{0,50}\b(human|associate|support|approval|confirmation)\b",
            re.I | re.S,
        ),
    ),
)

_OUT_OF_DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MEDICAL", re.compile(r"\b(diagnose|medicine|doctor|symptom|prescription)\b", re.I)),
    (
        "FINANCIAL_ADVICE",
        re.compile(r"\b(stock tip|investment advice|crypto trade|loan advice)\b", re.I),
    ),
    ("POLITICAL", re.compile(r"\b(vote for|political party|election campaign)\b", re.I)),
    ("GENERAL_CODING", re.compile(r"\b(write me code|debug my code|programming tutorial)\b", re.I)),
)

FREE_FORM_KEYS = frozenset(
    {
        "question",
        "query",
        "message",
        "instruction",
        "usertext",
        "customertext",
        "notes",
        "reasontext",
        "description",
    }
)


def scan_unauthorized_action(text: str) -> tuple[str, ...]:
    """Return the unauthorized-action signal codes present in ``text``."""

    return tuple(code for code, pattern in _UNAUTHORIZED_ACTION_PATTERNS if pattern.search(text))


def scan_out_of_domain(text: str) -> tuple[str, ...]:
    """Return the out-of-domain signal codes present in ``text``."""

    return tuple(code for code, pattern in _OUT_OF_DOMAIN_PATTERNS if pattern.search(text))
