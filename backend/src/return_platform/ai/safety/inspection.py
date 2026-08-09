"""Deterministic domain and prompt-injection policy for AI task inputs and outputs.

This module composes the guards; the patterns themselves live in `injection_guard.py`
and `scope_guard.py`. Precedence is deliberate and unchanged: injection outranks an
unauthorized action, which outranks an out-of-domain request, because the injection
signal is the one that says the payload is trying to change what the model is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from return_platform.ai.safety.injection_guard import scan_injection
from return_platform.ai.safety.scope_guard import (
    FREE_FORM_KEYS,
    scan_out_of_domain,
    scan_unauthorized_action,
)


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
        signals.extend(f"{code}:{path or '$'}" for code in scan_injection(bounded))
        unauthorized.extend(f"{code}:{path or '$'}" for code in scan_unauthorized_action(bounded))
        leaf = path.rsplit(".", maxsplit=1)[-1].replace("_", "").replace("-", "").lower()
        if leaf in FREE_FORM_KEYS:
            out_of_domain.extend(f"{code}:{path or '$'}" for code in scan_out_of_domain(bounded))

    if signals:
        return SafetyInspection(
            SafetyStatus.PROMPT_INJECTION_SUSPECTED, tuple(sorted(set(signals)))
        )
    if unauthorized:
        return SafetyInspection(
            SafetyStatus.UNAUTHORIZED_ACTION_REQUEST, tuple(sorted(set(unauthorized)))
        )
    if out_of_domain:
        return SafetyInspection(
            SafetyStatus.OUT_OF_DOMAIN_REQUEST, tuple(sorted(set(out_of_domain)))
        )
    return SafetyInspection(SafetyStatus.SAFE)


def inspect_output(text: str) -> SafetyInspection:
    """Reject model responses that try to escape the registered task contract."""

    signals = scan_injection(text[:16_000])
    if signals:
        return SafetyInspection(
            SafetyStatus.PROMPT_INJECTION_SUSPECTED, tuple(sorted(set(signals)))
        )
    return SafetyInspection(SafetyStatus.SAFE)
