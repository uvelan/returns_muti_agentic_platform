"""AgentRegistry: the single place that constructs the six agents.

Replaces two prior registries: agents.registry.ReturnAgentRegistry (a frozen
dataclass with no configuration metadata) and
dynamic_knowledge.agents.registry.IndependentAgentRegistry (descriptor-only, never
constructed anywhere, and never resolved to anything executable).

Construction only. There is no `resolve(agent_id)` and no agent_id-keyed
dispatch (AGT-02) -- see the class docstring.
"""

from __future__ import annotations

from dataclasses import dataclass

from return_platform.agents.bay_assignment import BayAssignmentAgent
from return_platform.agents.feedback import FeedbackLearningAgent
from return_platform.agents.fulfillment import ReturnFulfillmentAgent
from return_platform.agents.order_analysis import OrderAnalysisAgent
from return_platform.agents.order_discovery import OrderDiscoveryAgent
from return_platform.agents.return_workflow import ReturnWorkflowAgent
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


@dataclass(frozen=True, slots=True)
class AgentRegistry:
    """Owns construction of every configured agent.

    Typed attributes only (`.order_discovery`, `.order_analysis`, ...), because
    that is what a caller has: it knows which agent it needs, and it wants static
    type checking on the request and result shapes, which differ per agent.

    `resolve(agent_id)` and the `AgentPlugin` protocol behind it are gone
    (AGT-02). They existed for "Temporal orchestration, in a later phase" that
    never arrived: no production code ever dispatched by agent_id, the canonical
    `ReturnCaseWorkflow` drives named activities instead, and the one agent that
    would have needed the generic path most -- `OrderAnalysisAgent`, the only one
    that talks to a model -- could not implement it at all, because its `execute()`
    had no way to reach an `AIGatewayService`. A dispatch table that cannot carry
    every entry is not a dispatch table.
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
