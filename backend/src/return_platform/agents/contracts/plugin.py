"""AgentPlugin: the one shape every one of the six business agents implements.

Generic over each agent's own strongly-typed request/response pair (e.g.
DiscoveryAssessmentRequest -> DiscoveryAssessment) rather than a shared untyped
envelope -- callers that already know which agent they are invoking keep full static
type checking; only agent_id-keyed dynamic dispatch (introduced by Temporal
orchestration in a later phase) narrows to AgentPlugin[Any, Any].
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from return_platform.agents.contracts.context import AgentExecutionContext
from return_platform.agents.contracts.descriptor import AgentDescriptor

RequestT = TypeVar("RequestT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


@runtime_checkable
class AgentPlugin(Protocol[RequestT, ResultT]):
    """This agent does not directly invoke another agent."""

    @property
    def descriptor(self) -> AgentDescriptor: ...

    async def execute(self, request: RequestT, context: AgentExecutionContext) -> ResultT: ...
