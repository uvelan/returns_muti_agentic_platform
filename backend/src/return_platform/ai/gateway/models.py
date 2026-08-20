"""Public and internal contracts for AI routing, metrics, and safety."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from return_platform.ai.pricing import AIPricingStatus
from return_platform.ai.routing.selection import CircuitState
from return_platform.ai.routing.tasks import FallbackStrategy, ModelTier
from return_platform.ai.safety import SafetyStatus


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AIRouteHealthView(Contract):
    routeId: str
    provider: str
    model: str
    credentialId: str
    tier: ModelTier
    configured: bool
    circuitState: CircuitState
    activeRequests: int = Field(ge=0)
    requestsThisMinute: int = Field(ge=0)
    tokensThisMinute: int = Field(ge=0)
    lastError: str | None = None
    lastSuccessAtEpochMs: int | None = Field(default=None, ge=0)
    lastFailureAtEpochMs: int | None = Field(default=None, ge=0)


class AITaskView(Contract):
    taskId: str
    tier: ModelTier
    promptVersion: str
    fallbackStrategy: FallbackStrategy
    fallbackTemplate: str
    maximumOutputTokens: int | None
    maximumInputTokens: int
    allowTierEscalation: bool
    allowedProviders: tuple[str, ...]
    allowedInputKeys: tuple[str, ...]


class AIUsageAttemptView(Contract):
    id: str
    traceId: str
    sessionId: str | None = None
    # W4.12. Which piece of business work this call served. Platform ids only --
    # see `ai/gateway/telemetry.py` for why nothing customer-identifying may be
    # added here. Optional throughout because a schema-analysis call has no
    # conversation and a turn before CONFIRM_ORDER has no case; absent is
    # recorded as absent rather than as a placeholder.
    correlationId: str | None = None
    caseId: str | None = None
    conversationId: str | None = None
    agentId: str | None = None
    promptVersion: str | None = None
    taskId: str
    configuredTier: ModelTier
    selectedTier: ModelTier | None = None
    provider: str | None = None
    model: str | None = None
    credentialId: str | None = None
    routeId: str | None = None
    attemptNumber: int = Field(ge=0)
    selectionReason: str
    status: str
    fallbackUsed: bool = False
    # Why the fallback happened, not just that it did. "Fallbacks: 214" tells an
    # operator nothing actionable; "214, all TIMEOUT on one provider" does.
    fallbackReason: str | None = None
    safetyStatus: SafetyStatus
    latencyMs: int = Field(ge=0)
    rateLimitWaitMs: int = Field(ge=0)
    inputTokens: int = Field(ge=0)
    # The part of the prompt the provider served from its cache, billed at the
    # cached rate. `None` where the provider says nothing about caching, which
    # is not the same claim as zero.
    cachedInputTokens: int | None = Field(default=None, ge=0)
    outputTokens: int = Field(ge=0)
    totalTokens: int = Field(ge=0)
    # Not `...Microusd` any more, and not `0` any more. The currency is a
    # property of the pricing entry that priced this attempt, so a field name
    # asserting USD was a claim the configuration is free to contradict; and a
    # cost of exactly 0 for "we hold no price for this model" is the defect
    # W4.11 exists to remove -- it sums into totals as though the call were free.
    estimatedCostMicros: int | None = Field(default=None, ge=0)
    pricingCurrency: str | None = None
    pricingStatus: AIPricingStatus = AIPricingStatus.UNKNOWN
    pricingVersion: str | None = None
    errorCode: str | None = None
    requestDigest: str
    responseDigest: str | None = None
    createdAt: datetime


class AIUsageSummaryView(Contract):
    attempts: int = Field(ge=0)
    successes: int = Field(ge=0)
    failures: int = Field(ge=0)
    fallbacks: int = Field(ge=0)
    blockedBySafety: int = Field(ge=0)
    inputTokens: int = Field(ge=0)
    cachedInputTokens: int = Field(default=0, ge=0)
    outputTokens: int = Field(ge=0)
    totalTokens: int = Field(ge=0)
    # The total over *priced* attempts only, with the ones it could not price
    # counted beside it rather than folded in at zero. A single number that
    # silently omits a provider is how "our AI spend" becomes a figure nobody
    # can reconcile against an invoice.
    estimatedCostMicros: int = Field(default=0, ge=0)
    pricingCurrency: str | None = None
    unpricedAttempts: int = Field(default=0, ge=0)
    byProvider: dict[str, int]
    byModel: dict[str, int]
    byTask: dict[str, int]
    byTier: dict[str, int]


class AISafetyTestRequest(Contract):
    taskId: str = Field(default="RETURN_CLARIFICATION_FIELD_V2", min_length=1, max_length=128)
    payload: dict[str, Any]


class AISafetyTestResponse(Contract):
    taskId: str
    status: SafetyStatus
    signals: tuple[str, ...]
    allowed: bool
    deterministicResponse: dict[str, Any]
