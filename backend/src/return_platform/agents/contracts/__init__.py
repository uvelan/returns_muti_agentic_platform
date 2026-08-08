"""Contracts shared by the six production return agents. See ../README.md.

dto.py holds the per-agent request/response pydantic models (unchanged from the
former flat agents/contracts.py -- this package split only adds descriptor.py,
context.py, plugin.py, and ports.py alongside it). Everything below stays importable
as `return_platform.agents.contracts.<name>` exactly as before the split.
"""

from return_platform.agents.contracts.context import AgentExecutionContext
from return_platform.agents.contracts.descriptor import AgentDescriptor
from return_platform.agents.contracts.dto import (
    AgentDecisionView,
    AgentExecutionMode,
    AgentModel,
    BayAssessment,
    BayAssessmentRequest,
    BayCandidateInput,
    DiscoveryAssessment,
    DiscoveryAssessmentRequest,
    DiscoveryCandidateInput,
    FeedbackAssessment,
    FeedbackAssessmentRequest,
    FulfillmentAssessment,
    FulfillmentAssessmentRequest,
    FulfillmentFact,
    NormalizedReturnMethod,
    OrderAnalysisAssessment,
    OrderAnalysisRequest,
    OrderSource,
    ProductPresence,
    RankedDiscoveryCandidate,
    ReturnItemInput,
    ReturnWorkflowAssessment,
    ReturnWorkflowAssessmentRequest,
)
from return_platform.agents.contracts.plugin import AgentPlugin
from return_platform.agents.contracts.ports import AgentAiPort, KnowledgePort

__all__ = [
    "AgentAiPort",
    "AgentDecisionView",
    "AgentDescriptor",
    "AgentExecutionContext",
    "AgentExecutionMode",
    "AgentModel",
    "AgentPlugin",
    "BayAssessment",
    "BayAssessmentRequest",
    "BayCandidateInput",
    "DiscoveryAssessment",
    "DiscoveryAssessmentRequest",
    "DiscoveryCandidateInput",
    "FeedbackAssessment",
    "FeedbackAssessmentRequest",
    "FulfillmentAssessment",
    "FulfillmentAssessmentRequest",
    "FulfillmentFact",
    "KnowledgePort",
    "NormalizedReturnMethod",
    "OrderAnalysisAssessment",
    "OrderAnalysisRequest",
    "OrderSource",
    "ProductPresence",
    "RankedDiscoveryCandidate",
    "ReturnItemInput",
    "ReturnWorkflowAssessment",
    "ReturnWorkflowAssessmentRequest",
]
