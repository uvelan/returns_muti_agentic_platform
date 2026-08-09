"""The analyzer's shape of the single AI execution path.

The analyzer never imports `ai`. `bootstrap/adapters/analyzer_ai_adapter.py` wraps
the same `AIGateway` the agents use and publishes it under this Protocol, so
routing, failover, rate limits, circuit breakers, interception, replay, safety,
and metrics stay shared -- there is still exactly one AI execution path, and this
port is only its analyzer-facing projection.

`SchemaProposal` is intentionally a *typed* result rather than free text. The model
proposes graph structure; it never emits executable DDL/DML for a source system
(design doc C3.3), and a port that cannot carry a SQL string is the cheapest way
to guarantee that.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

__all__ = ["ProposedNode", "ProposedRelationship", "SchemaProposal", "SchemaReasoningPort"]


class ProposedNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    properties: tuple[Mapping[str, Any], ...]
    source_dataset: str
    rationale: str


class ProposedRelationship(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_type: str
    from_label: str
    to_label: str
    rationale: str


class SchemaProposal(BaseModel):
    """One model-produced graph schema proposal, grounded in a named snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_content_hash: str
    nodes: tuple[ProposedNode, ...]
    relationships: tuple[ProposedRelationship, ...]
    open_questions: tuple[str, ...] = ()


@runtime_checkable
class SchemaReasoningPort(Protocol):
    """Resolved from the capability registry; implemented in bootstrap/adapters/."""

    async def propose_schema(
        self,
        *,
        analysis_id: str,
        snapshot_content_hash: str,
        prompt_blocks: Sequence[Mapping[str, Any]],
    ) -> SchemaProposal:
        """`prompt_blocks` carries the six-block untrusted-input framing built by
        `application/prompt_context.py`; the port takes it pre-framed so no caller
        can hand the gateway an unstructured prompt."""
