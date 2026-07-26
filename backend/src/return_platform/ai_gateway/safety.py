"""Deterministic domain and prompt-injection policy for AI task inputs and outputs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SafetyStatus(StrEnum):
    SAFE = "SAFE"
    PROMPT_INJECTION_SUSPECTED = "PROMPT_INJECTION_SUSPECTED"
    OUT_OF_DOMAIN_REQUEST = "OUT_OF_DOMAIN_REQUEST"
    UNAUTHORIZED_ACTION_REQUEST = "UNAUTHORIZED_ACTION_REQUEST"


@dataclass(frozen=True, slots=True)
class SafetyInspection:
    status: SafetyStatus
    signals: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.status is SafetyStatus.SAFE


_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("IGNORE_INSTRUCTIONS", re.compile(r"\b(ignore|disregard|forget)\b.{0,40}\b(instruction|prompt|policy|rule)s?\b", re.I | re.S)),
    ("SYSTEM_PROMPT_REQUEST", re.compile(r"\b(show|reveal|print|repeat|leak)\b.{0,50}\b(system|developer|hidden)\s+(prompt|message|instruction)s?\b", re.I | re.S)),
    ("ROLE_OVERRIDE", re.compile(r"\b(you are now|act as|pretend to be|switch role|developer mode|jailbreak)\b", re.I)),
    ("SECRET_REQUEST", re.compile(r"\b(api[-_ ]?key|password|secret|credential|access[-_ ]?token|private key)\b", re.I)),
    ("ENCODED_OVERRIDE", re.compile(r"\b(base64|rot13|decode this|encoded instruction)\b", re.I)),
)

_UNAUTHORIZED_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CREATE_AUTHORITATIVE_FACT", re.compile(r"\b(create|issue|generate|approve|confirm|mark)\b.{0,60}\b(rma|rga|refund|pickup|receipt|license plate|vendor credit)\b", re.I | re.S)),
    ("DIRECT_SQL", re.compile(r"\b(insert|update|delete|drop|alter|execute)\b.{0,40}\b(sql|table|database|omc)\b", re.I | re.S)),
    ("BYPASS_HUMAN", re.compile(r"\b(bypass|skip|avoid|override)\b.{0,50}\b(human|associate|support|approval|confirmation)\b", re.I | re.S)),
)

# These patterns are evaluated only for explicit free-form request fields, not for every
# product description or operational note.
_OUT_OF_DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MEDICAL", re.compile(r"\b(diagnose|medicine|doctor|symptom|prescription)\b", re.I)),
    ("FINANCIAL_ADVICE", re.compile(r"\b(stock tip|investment advice|crypto trade|loan advice)\b", re.I)),
    ("POLITICAL", re.compile(r"\b(vote for|political party|election campaign)\b", re.I)),
    ("GENERAL_CODING", re.compile(r"\b(write me code|debug my code|programming tutorial)\b", re.I)),
)

_FREE_FORM_KEYS = {
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


def _walk_strings(value: Any, *, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _walk_strings(item, path=child)


def inspect_input(payload: dict[str, Any]) -> SafetyInspection:
    signals: list[str] = []
    unauthorized: list[str] = []
    out_of_domain: list[str] = []

    for path, text in _walk_strings(payload):
        bounded = text[:8_000]
        for code, pattern in _INJECTION_PATTERNS:
            if pattern.search(bounded):
                signals.append(f"{code}:{path or '$'}")
        for code, pattern in _UNAUTHORIZED_ACTION_PATTERNS:
            if pattern.search(bounded):
                unauthorized.append(f"{code}:{path or '$'}")
        leaf = path.rsplit(".", maxsplit=1)[-1].replace("_", "").replace("-", "").lower()
        if leaf in _FREE_FORM_KEYS:
            for code, pattern in _OUT_OF_DOMAIN_PATTERNS:
                if pattern.search(bounded):
                    out_of_domain.append(f"{code}:{path or '$'}")

    if signals:
        return SafetyInspection(SafetyStatus.PROMPT_INJECTION_SUSPECTED, tuple(sorted(set(signals))))
    if unauthorized:
        return SafetyInspection(SafetyStatus.UNAUTHORIZED_ACTION_REQUEST, tuple(sorted(set(unauthorized))))
    if out_of_domain:
        return SafetyInspection(SafetyStatus.OUT_OF_DOMAIN_REQUEST, tuple(sorted(set(out_of_domain))))
    return SafetyInspection(SafetyStatus.SAFE)


def inspect_output(text: str) -> SafetyInspection:
    """Reject model responses that try to escape the registered task contract."""
    bounded = text[:16_000]
    signals: list[str] = []
    for code, pattern in _INJECTION_PATTERNS:
        if pattern.search(bounded):
            signals.append(code)
    if signals:
        return SafetyInspection(SafetyStatus.PROMPT_INJECTION_SUSPECTED, tuple(sorted(set(signals))))
    return SafetyInspection(SafetyStatus.SAFE)
