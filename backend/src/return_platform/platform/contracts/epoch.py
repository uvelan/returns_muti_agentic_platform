"""Neutral runtime-epoch contract.

Declared here in Phase 1A; allocated and consumed by bootstrap starting in Phase 1B.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RuntimeEpoch(Protocol):
    """One replica-local generation of runtime state. Monotonic; exactly one is current."""

    epoch: int
    release_id: str
