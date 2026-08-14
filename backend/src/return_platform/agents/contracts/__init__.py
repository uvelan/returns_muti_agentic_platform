"""Contracts shared by the six production return agents. See ../README.md.

dto.py holds the per-agent request/response pydantic models (unchanged from the
former flat agents/contracts.py), alongside context.py and ports.py. Everything
below stays importable as `return_platform.agents.contracts.<name>`.

There is no `AgentPlugin` protocol and no `AgentDescriptor` here any more
(AGT-02). Both existed only to support agent_id-keyed dynamic dispatch that
nothing ever dispatched through: every production caller holds a concrete agent
and calls its own typed method, which is also the only way `OrderAnalysisAgent`
can be called at all -- its generic `execute()` could never be implemented,
because it needs an `AIGatewayService` the context does not carry.
"""

from return_platform.agents.contracts.context import AgentExecutionContext
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
from return_platform.agents.contracts.ports import AgentAiPort, KnowledgePort

__all__ = [
    "AgentAiPort",
    "AgentDecisionView",
    "AgentExecutionContext",
    "AgentExecutionMode",
    "AgentModel",
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
