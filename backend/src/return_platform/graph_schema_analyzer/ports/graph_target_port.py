"""Compile / validate / build against the graph target.

Every method here targets the **graph**, never a source system: indexes and
constraints are applied to the graph target only (design doc C3.3). Build is
delegated rather than performed -- the graph module owns generation lifecycle
(C4), and the analyzer only triggers it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

__all__ = ["BuildHandle", "GraphTargetPort"]


class BuildHandle(BaseModel):
    """A build the graph module accepted. The analyzer tracks it, never drives it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    generation_id: str
    accepted: bool
    detail: str | None = None


@runtime_checkable
class GraphTargetPort(Protocol):
    """Resolved from the capability registry; implemented in bootstrap/adapters/."""

    async def compile_schema(self, *, draft: Mapping[str, Any]) -> Sequence[str]:
        """Compile a draft into graph-side statements for review. Returns the
        statements rather than executing them."""

    async def validate_schema(self, *, draft: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        """Graph-side feasibility findings (a draft can be internally consistent
        and still be unbuildable against this target)."""

    async def request_build(self, *, schema_id: str, activate: bool) -> BuildHandle:
        """Delegate to the graph module's RebuildTrigger."""

    async def publish_release(
        self,
        *,
        draft: Mapping[str, Any],
        draft_id: str,
        approver: str,
        activate: bool,
    ) -> BuildHandle:
        """Compile an approved shape into a runtime release and store it.

        The step that was missing: the analyzer could approve a schema and
        nothing consumed it. Crossing the boundary here rather than importing
        the runtime keeps the analyzer's independence intact -- the shape goes
        out as plain data and a release id comes back.

        `activate` decides whether the runtime starts reading it. Publishing
        without activating is the ordinary path for a change someone wants
        staged; the two are separate because "recorded" and "live" are
        different decisions and conflating them makes the second unreviewable.
        """
