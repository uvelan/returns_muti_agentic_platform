"""Shapes an agent may resolve from AgentExecutionContext.capabilities.

An agent declares the narrowest port it needs here (or in its own package's ports.py,
for a port only that agent uses -- see agents/order_discovery/ports.py once Order
Discovery gains generation-aware reads) and resolves it via
`context.capabilities.resolve(CapabilityName.X, PortType)`. No agent package imports
`ai_gateway` or `dynamic_knowledge`/`data_platform.graph` directly.

Not yet wired end-to-end: no adapter under bootstrap/adapters/ publishes these ports
today, so a resolve() call against a live CapabilityRegistry would raise
CapabilityNotPublished. Declaring the shape now is Phase 5's job; binding a concrete
provider to it is a later phase's (the six agents keep calling ai_gateway/db access the
way they do today -- via explicit constructor/method parameters -- until that binding
exists).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from return_platform.platform.contracts.consistency import ConsistencyHandle


class AiOutcome(Protocol):
    """Whatever shape an AI invocation returns; a port narrows this per consumer."""


@runtime_checkable
class AgentAiPort(Protocol):
    async def invoke(self, task_id: str, inputs: Mapping[str, object]) -> AiOutcome: ...


class KnowledgeRequest(Protocol):
    """Whatever shape a knowledge query takes; a port narrows this per consumer."""


class KnowledgeResult(Protocol):
    """Whatever shape a knowledge query returns; a port narrows this per consumer."""


@runtime_checkable
class KnowledgePort(Protocol):
    async def query(
        self, request: KnowledgeRequest, consistency: ConsistencyHandle
    ) -> KnowledgeResult: ...
