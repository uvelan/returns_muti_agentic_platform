"""Platform-neutral request/operation correlation contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CorrelationContext(Protocol):
    """Identifies one logical operation across module and process boundaries."""

    correlation_id: str
