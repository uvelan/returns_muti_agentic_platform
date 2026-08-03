"""Contracts shared by the five production return agents."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentExecutionMode(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI_ASSISTED = "AI_ASSISTED"


class OrderSource(StrEnum):
    FERGUSONHOME_WEB = "FERGUSONHOME_WEB"
    FERGUSON_SHOWROOM = "FERGUSON_SHOWROOM"
    FERGUSON_BRANCH = "FERGUSON_BRANCH"
    OTHER_DIGITAL_BRAND = "OTHER_DIGITAL_BRAND"
    UNKNOWN = "UNKNOWN"


class ProductPresence(StrEnum):
    PRESENT_AT_BRANCH = "PRESENT_AT_BRANCH"
    OFFSITE_CUSTOMER_RESIDENCE = "OFFSITE_CUSTOMER_RESIDENCE"
    OFFSITE_CUSTOMER_JOBSITE = "OFFSITE_CUSTOMER_JOBSITE"
    OFFSITE_COMMERCIAL_SITE = "OFFSITE_COMMERCIAL_SITE"
    OFFSITE_VENDOR_SITE = "OFFSITE_VENDOR_SITE"
    OFFSITE_OTHER = "OFFSITE_OTHER"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class NormalizedReturnMethod(StrEnum):
    PREPAID_PARCEL = "PREPAID_PARCEL"
    BRANCH_UPS = "BRANCH_UPS"
    BRANCH_LTL = "BRANCH_LTL"
    OFFSITE_PARCEL = "OFFSITE_PARCEL"
    OFFSITE_LTL = "OFFSITE_LTL"
    DIRECT_VENDOR = "DIRECT_VENDOR"
    FIELD_SCRAP = "FIELD_SCRAP"
    NO_PHYSICAL_RETURN = "NO_PHYSICAL_RETURN"
    CUSTOMER_KEEP = "CUSTOMER_KEEP"
    UNKNOWN = "UNKNOWN"


class AgentDecisionView(AgentModel):
    agent: str
    agentVersion: str
    configurationVersion: str
    decisionType: str
    decision: str
    explanation: str
    confidenceMillionths: int = Field(ge=0, le=1_000_000)
    evidenceReferences: tuple[str, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()
    executionMode: AgentExecutionMode = AgentExecutionMode.DETERMINISTIC
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveryCandidateInput(AgentModel):
    candidateId: str
    orderReference: str
    customerReference: str
    orderSource: OrderSource = OrderSource.UNKNOWN
    matchedAnchors: tuple[str, ...] = ()
    conflictingAnchors: tuple[str, ...] = ()
    evidenceReferences: tuple[str, ...] = Field(min_length=1)


class DiscoveryAssessmentRequest(AgentModel):
    suppliedEvidence: dict[str, str]
    candidates: tuple[DiscoveryCandidateInput, ...]


class RankedDiscoveryCandidate(AgentModel):
    candidateId: str
    scoreMillionths: int = Field(ge=0, le=1_000_000)
    explanation: str
    evidenceReferences: tuple[str, ...]


class DiscoveryAssessment(AgentModel):
    orderSource: OrderSource
    rankedCandidates: tuple[RankedDiscoveryCandidate, ...]
    confirmationRequired: bool
    ambiguous: bool
    nextQuestion: str | None
    decision: AgentDecisionView


class OrderAnalysisRequest(AgentModel):
    sessionId: str
    candidates: tuple[DiscoveryCandidateInput, ...]
    suppliedEvidence: dict[str, str]


class OrderAnalysisAssessment(AgentModel):
    smartQuestion: str | None
    analysisExplanation: str
    decision: AgentDecisionView


class ReturnItemInput(AgentModel):
    orderLineId: str
    productId: str
    requestedQuantity: int = Field(ge=1)
    shippedQuantity: int | None = Field(default=None, ge=0)
    reasonCode: str
    condition: str | None = None
    attachmentIds: tuple[str, ...] = ()


class ReturnWorkflowAssessmentRequest(AgentModel):
    sessionId: str
    orderSource: OrderSource
    productPresence: ProductPresence | None = None
    proposedReturnMethod: NormalizedReturnMethod = NormalizedReturnMethod.UNKNOWN
    branchId: str | None = None
    associateId: str | None = None
    items: tuple[ReturnItemInput, ...] = Field(min_length=1)
    pickupAssessment: dict[str, Any] | None = None


class ReturnWorkflowAssessment(AgentModel):
    complete: bool
    missingFields: tuple[str, ...]
    photoEvidenceRequired: bool
    recommendedReturnMethod: NormalizedReturnMethod
    supportDraft: str
    decision: AgentDecisionView


class FulfillmentFact(AgentModel):
    factType: str
    status: str
    reference: str | None = None
    evidenceReference: str


class FulfillmentAssessmentRequest(AgentModel):
    returnVersion: str
    rawReturnStatus: str | None = None
    rawCustomerResolution: str | None = None
    rawProductResolution: str | None = None
    facts: tuple[FulfillmentFact, ...] = ()


class FulfillmentAssessment(AgentModel):
    normalizedReturnStatus: str
    customerResolutionComplete: bool
    physicalReturnComplete: bool
    warehouseProcessingComplete: bool
    vendorRecoveryComplete: bool
    nextExpectedEvent: str | None
    decision: AgentDecisionView


class BayCandidateInput(AgentModel):
    bayId: str
    bayType: str
    active: bool = True
    capacityAvailable: int = Field(ge=0)
    supportsOversized: bool = False
    supportsHazardous: bool = False
    supportedReturnMethods: tuple[NormalizedReturnMethod, ...] = ()


class BayAssessmentRequest(AgentModel):
    physicalStatus: str
    returnMethod: NormalizedReturnMethod
    requiredCapacity: int = Field(ge=1)
    oversized: bool = False
    hazardous: bool = False
    candidates: tuple[BayCandidateInput, ...]


class BayAssessment(AgentModel):
    recommendedBayId: str | None
    eligibleBayIds: tuple[str, ...]
    excludedReasons: dict[str, tuple[str, ...]]
    decision: AgentDecisionView


class FeedbackAssessmentRequest(AgentModel):
    sessionId: str
    metrics: dict[str, int | float | bool]
    evidenceReferences: tuple[str, ...] = Field(min_length=1)


class FeedbackAssessment(AgentModel):
    recommendations: tuple[str, ...]
    reviewRequired: bool
    decision: AgentDecisionView
