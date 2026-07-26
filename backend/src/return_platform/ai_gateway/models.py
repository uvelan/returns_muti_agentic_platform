"""Public and internal contracts for AI routing, metrics, and safety."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from return_platform.ai_gateway.configuration import FallbackStrategy, ModelTier
from return_platform.ai_gateway.routing import CircuitState
from return_platform.ai_gateway.safety import SafetyStatus


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
    maximumOutputTokens: int
    maximumInputTokens: int
    allowTierEscalation: bool
    allowedProviders: tuple[str, ...]
    allowedInputKeys: tuple[str, ...]


class AIUsageAttemptView(Contract):
    id: str
    traceId: str
    sessionId: str | None = None
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
    safetyStatus: SafetyStatus
    latencyMs: int = Field(ge=0)
    rateLimitWaitMs: int = Field(ge=0)
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    totalTokens: int = Field(ge=0)
    estimatedCostMicrousd: int = Field(ge=0)
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
    outputTokens: int = Field(ge=0)
    totalTokens: int = Field(ge=0)
    estimatedCostMicrousd: int = Field(ge=0)
    byProvider: dict[str, int]
    byModel: dict[str, int]
    byTask: dict[str, int]
    byTier: dict[str, int]


class AISafetyTestRequest(Contract):
    taskId: str = Field(default="RETURN_SMART_QUESTION_V1", min_length=1, max_length=128)
    payload: dict[str, Any]


class AISafetyTestResponse(Contract):
    taskId: str
    status: SafetyStatus
    signals: tuple[str, ...]
    allowed: bool
    deterministicResponse: dict[str, Any]
