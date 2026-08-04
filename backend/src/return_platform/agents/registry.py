"""Registry for the five bounded production return agents."""

from dataclasses import dataclass

from return_platform.agents.bay_assignment import BayAssignmentAgent
from return_platform.agents.feedback import FeedbackLearningAgent
from return_platform.agents.fulfillment import ReturnFulfillmentAgent
from return_platform.agents.order_analysis import OrderAnalysisAgent
from return_platform.agents.order_discovery import OrderDiscoveryAgent
from return_platform.agents.return_workflow import ReturnWorkflowAgent
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


@dataclass(frozen=True, slots=True)
class ReturnAgentRegistry:
    order_discovery: OrderDiscoveryAgent
    order_analysis: OrderAnalysisAgent
    return_workflow: ReturnWorkflowAgent
    return_fulfillment: ReturnFulfillmentAgent
    bay_assignment: BayAssignmentAgent
    feedback_learning: FeedbackLearningAgent

    @classmethod
    def build(cls, configuration: ReturnPlatformConfiguration) -> "ReturnAgentRegistry":
        return cls(
            order_discovery=OrderDiscoveryAgent(configuration),
            order_analysis=OrderAnalysisAgent(configuration),
            return_workflow=ReturnWorkflowAgent(configuration),
            return_fulfillment=ReturnFulfillmentAgent(configuration),
            bay_assignment=BayAssignmentAgent(configuration),
            feedback_learning=FeedbackLearningAgent(configuration),
        )
