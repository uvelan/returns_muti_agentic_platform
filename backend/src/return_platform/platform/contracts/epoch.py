"""Neutral runtime-epoch contract.

Declared here in Phase 1A; allocated and consumed by bootstrap starting in Phase 1B.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RuntimeEpoch(Protocol):
    """One replica-local generation of runtime state. Monotonic; exactly one is current.

    Declared as read-only properties, not plain attributes: an epoch identity is
    immutable, and a plain `x: int` Protocol member means "readable AND writable" to
    mypy, which a frozen dataclass (the natural implementation) cannot satisfy.
    """

    @property
    def epoch(self) -> int: ...
    @property
    def release_id(self) -> str: ...
