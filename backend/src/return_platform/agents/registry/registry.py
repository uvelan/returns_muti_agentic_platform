"""AgentRegistry: the single place that constructs and resolves the six agents.

Replaces two prior registries: agents.registry.ReturnAgentRegistry (a frozen
dataclass with no configuration metadata) and
dynamic_knowledge.agents.registry.IndependentAgentRegistry (descriptor-only, never
constructed anywhere, and never resolved to anything executable).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from return_platform.agents.bay_assignment import BayAssignmentAgent
from return_platform.agents.contracts.descriptor import AgentDescriptor
from return_platform.agents.contracts.plugin import AgentPlugin
from return_platform.agents.feedback import FeedbackLearningAgent
from return_platform.agents.fulfillment import ReturnFulfillmentAgent
from return_platform.agents.order_analysis import OrderAnalysisAgent
from return_platform.agents.order_discovery import OrderDiscoveryAgent
from return_platform.agents.return_workflow import ReturnWorkflowAgent
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


class UnknownAgentId(KeyError):
    """No agent is configured under this agent_id."""


@dataclass(frozen=True, slots=True)
class AgentRegistry:
    """Owns construction of every configured agent and resolves agent_id -> plugin.

    Typed attributes (`.order_discovery`, `.order_analysis`, ...) stay for call sites
    that already know which agent they need and want static type checking on the
    result; `resolve()`/`descriptor()` add agent_id-keyed dynamic dispatch for callers
    that don't (Temporal orchestration, in a later phase).
    """

    order_discovery: OrderDiscoveryAgent
    order_analysis: OrderAnalysisAgent
    return_workflow: ReturnWorkflowAgent
    return_fulfillment: ReturnFulfillmentAgent
    bay_assignment: BayAssignmentAgent
    feedback_learning: FeedbackLearningAgent

    @classmethod
    def build(cls, configuration: ReturnPlatformConfiguration) -> AgentRegistry:
        return cls(
            order_discovery=OrderDiscoveryAgent(configuration),
            order_analysis=OrderAnalysisAgent(configuration),
            return_workflow=ReturnWorkflowAgent(configuration),
            return_fulfillment=ReturnFulfillmentAgent(configuration),
            bay_assignment=BayAssignmentAgent(configuration),
            feedback_learning=FeedbackLearningAgent(configuration),
        )

    def _plugins(self) -> dict[str, AgentPlugin[Any, Any]]:
        return {
            "order_discovery": self.order_discovery,
            "order_analysis": self.order_analysis,
            "return_workflow": self.return_workflow,
            "return_fulfillment": self.return_fulfillment,
            "bay_assignment": self.bay_assignment,
            "feedback_learning": self.feedback_learning,
        }

    def resolve(self, agent_id: str) -> AgentPlugin[Any, Any]:
        plugins = self._plugins()
        if agent_id not in plugins:
            raise UnknownAgentId(agent_id)
        return plugins[agent_id]

    def descriptor(self, agent_id: str) -> AgentDescriptor:
        return self.resolve(agent_id).descriptor

    def all_descriptors(self) -> tuple[AgentDescriptor, ...]:
        return tuple(plugin.descriptor for plugin in self._plugins().values())
