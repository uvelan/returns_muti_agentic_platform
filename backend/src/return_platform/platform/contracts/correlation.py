"""Platform-neutral request/operation correlation contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CorrelationContext(Protocol):
    """Identifies one logical operation across module and process boundaries.

    Read-only property, not a plain attribute: a plain `x: str` Protocol member means
    "readable AND writable" to mypy, which the natural frozen implementation cannot
    satisfy.
    """

    @property
    def correlation_id(self) -> str: ...
