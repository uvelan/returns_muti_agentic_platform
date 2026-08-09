"""Untrusted-input containment: patterns that mark an attempt to escape the task.

These fire on *any* string reachable in a payload, because a prompt injection does
not have to arrive in a field the platform thinks of as free-form -- a product
description carried out of a source system is just as good a carrier as a customer
question. Detection is deterministic and pattern-based on purpose: a model asked to
judge whether it is being attacked is itself part of the attack surface.
"""

from __future__ import annotations

import re

__all__ = ["scan_injection"]

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "IGNORE_INSTRUCTIONS",
        re.compile(
            r"\b(ignore|disregard|forget)\b.{0,40}\b(instruction|prompt|policy|rule)s?\b",
            re.I | re.S,
        ),
    ),
    (
        "SYSTEM_PROMPT_REQUEST",
        re.compile(
            r"\b(show|reveal|print|repeat|leak)\b.{0,50}\b(system|developer|hidden)\s+(prompt|message|instruction)s?\b",
            re.I | re.S,
        ),
    ),
    (
        "ROLE_OVERRIDE",
        re.compile(
            r"\b(you are now|act as|pretend to be|switch role|developer mode|jailbreak)\b", re.I
        ),
    ),
    (
        "SECRET_REQUEST",
        re.compile(
            r"\b(api[-_ ]?key|password|secret|credential|access[-_ ]?token|private key)\b", re.I
        ),
    ),
    ("ENCODED_OVERRIDE", re.compile(r"\b(base64|rot13|decode this|encoded instruction)\b", re.I)),
)


def scan_injection(text: str) -> tuple[str, ...]:
    """Return the injection signal codes present in ``text``."""

    return tuple(code for code, pattern in _INJECTION_PATTERNS if pattern.search(text))
