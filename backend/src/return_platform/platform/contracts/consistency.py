"""Neutral read-consistency contract.

graph.lifecycle.handles.GenerationHandle satisfies this without platform ever naming a
graph type (design doc section 13.1).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ConsistencyHandle(Protocol):
    """A read-consistency token acquired at an operation boundary and threaded through it."""

    token: str

    def assert_current(self) -> None:
        """Raise ConsistencyChanged if the underlying resource moved since acquisition."""

    async def release(self) -> None: ...


class ConsistencyChanged(RuntimeError):
    """The consistency token was superseded mid-operation. Callers restart, never continue."""
