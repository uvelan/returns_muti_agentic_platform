"""The Analyzer Agent's shape of the shared AI execution path.

The analyzer never imports `ai`. `bootstrap/adapters/analyzer_agent_adapter.py`
wraps the same gateway the rest of the platform uses and publishes it under this
Protocol, so routing, failover, rate limits, circuit breakers, interception,
replay and metrics stay shared.

`AgentAnswer` is a *typed* result rather than free text, for the same reason
`SchemaProposal` is: a port that cannot carry a SQL or Cypher string is the
cheapest way to guarantee the agent never returns one. `operations` is a closed
set of system-graph edits, and every one of them names `SYSTEM_GRAPH` as its
target -- there is no field in which a source-side action could be expressed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentAnswer",
    "AgentReasoningPort",
    "ProposedGraphOperation",
]

#: The complete set of edits the agent may propose.
#:
#: Closed on purpose. An open string would let a model name an operation nobody
#: implemented -- and the natural way to express one is a statement to run, which
#: is exactly what must never reach this system.
GraphOperationType = Literal[
    "ADD_SYSTEM_GRAPH_INDEX",
    "REMOVE_SYSTEM_GRAPH_INDEX",
    "SET_SYSTEM_GRAPH_IDENTIFIER",
    "RENAME_SYSTEM_GRAPH_ENTITY",
    "RENAME_SYSTEM_GRAPH_RELATIONSHIP",
]


class ProposedGraphOperation(BaseModel):
    """One reviewable edit to the *proposed* system graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: GraphOperationType
    #: The proposed-graph entity, relationship or property the edit applies to.
    objectId: str = Field(min_length=1, max_length=512)
    #: Only ever the literal `SYSTEM_GRAPH`. Present and constrained rather than
    #: implied, so the review path can assert it instead of assuming it.
    target: Literal["SYSTEM_GRAPH"] = "SYSTEM_GRAPH"
    #: New name, for the rename operations. Ignored by the others.
    value: str | None = Field(default=None, max_length=200)


class AgentAnswer(BaseModel):
    """What the agent said, and at most one change it wants reviewed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    #: Absent when the operator asked a question rather than for a change.
    summary: str | None = Field(default=None, max_length=300)
    rationale: str | None = Field(default=None, max_length=2000)
    operations: tuple[ProposedGraphOperation, ...] = ()


@runtime_checkable
class AgentReasoningPort(Protocol):
    """Resolved from application state; implemented in bootstrap/adapters/."""

    async def answer(
        self,
        *,
        conversation_id: str,
        prompt_blocks: Sequence[Mapping[str, Any]],
    ) -> AgentAnswer:
        """`prompt_blocks` carries the same six-block untrusted-input framing the
        proposal path uses, so source content reaches the model as data."""
