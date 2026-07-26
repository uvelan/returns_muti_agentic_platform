"""Stable API and persistence contracts for dependency simulation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SimulatorContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DependencyKind(StrEnum):
    OMC = "OMC"
    PARCEL = "PARCEL"
    FREIGHT = "FREIGHT"
    LSI = "LSI"


class SimulationScenario(StrEnum):
    SUCCESS = "SUCCESS"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    TIMEOUT = "TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class SimulationOperationStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    CONFIRMED = "CONFIRMED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    CANCELLED = "CANCELLED"


class SimulationOperationRequest(SimulatorContract):
    dependency: DependencyKind
    operation: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z][A-Z0-9_]+$")
    sessionId: str = Field(min_length=3, max_length=128)
    idempotencyKey: str = Field(min_length=8, max_length=200)
    scenario: SimulationScenario = SimulationScenario.SUCCESS
    payload: dict[str, Any] = Field(default_factory=dict)
    useAiNarrative: bool = True
    signalWorkflow: bool = True


class SimulationAdvanceRequest(SimulatorContract):
    targetStatus: str | None = Field(default=None, max_length=64)
    scenario: SimulationScenario = SimulationScenario.SUCCESS
    payload: dict[str, Any] = Field(default_factory=dict)
    useAiNarrative: bool = True
    signalWorkflow: bool = True


class SimulationNarrative(SimulatorContract):
    source: str
    message: str
    summary: str
    nextAction: str
    templateVersion: str
    aiMetricId: str | None = None


class SimulationOperationView(SimulatorContract):
    id: str
    dependency: DependencyKind
    operation: str
    sessionId: str
    idempotencyKey: str
    scenario: SimulationScenario
    status: SimulationOperationStatus
    externalReference: str | None = None
    simulatedState: str | None = None
    requestPayload: dict[str, Any]
    responsePayload: dict[str, Any]
    narrative: SimulationNarrative
    errorCode: str | None = None
    workflowEventType: str | None = None
    workflowSignalStatus: str | None = None
    createdAt: datetime
    updatedAt: datetime


class SimulationAIUsageMetric(SimulatorContract):
    id: str
    operationId: str
    sessionId: str
    dependency: DependencyKind
    operation: str
    provider: str
    model: str
    credentialId: str | None = None
    routeId: str | None = None
    modelTier: str = "LIGHTWEIGHT"
    selectionReason: str | None = None
    status: str
    fallbackUsed: bool
    attempt: int = Field(ge=0)
    latencyMs: int = Field(ge=0)
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    totalTokens: int = Field(ge=0)
    estimatedCostMicrousd: int = Field(ge=0)
    requestDigest: str
    responseDigest: str | None = None
    errorCode: str | None = None
    createdAt: datetime


class SimulationAISummary(SimulatorContract):
    requestCount: int = 0
    successCount: int = 0
    failureCount: int = 0
    fallbackCount: int = 0
    totalInputTokens: int = 0
    totalOutputTokens: int = 0
    totalTokens: int = 0
    estimatedCostMicrousd: int = 0
    byProvider: dict[str, dict[str, int]] = Field(default_factory=dict)
    byModel: dict[str, dict[str, int]] = Field(default_factory=dict)
    byDependency: dict[str, dict[str, int]] = Field(default_factory=dict)
    byOperation: dict[str, dict[str, int]] = Field(default_factory=dict)


class DependencySimulationSummary(SimulatorContract):
    enabled: bool
    banner: str
    environment: str
    modes: dict[str, str]
    operationCounts: dict[str, int]
    ai: SimulationAISummary
    configurationSha256: str


class SimulationE2ERequest(SimulatorContract):
    scenario: str = Field(default="BRANCH_PARCEL", pattern=r"^(BRANCH_PARCEL|OFFSITE_HEAVY)$")
    useAiNarrative: bool = True
    includeVendorRecovery: bool = True


class SimulationE2EResult(SimulatorContract):
    sessionId: str
    scenario: str
    operationIds: list[str]
    workflowStage: str | None = None
    caseFullyClosed: bool | None = None
    completedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SimulationResetRequest(SimulatorContract):
    confirmation: str = Field(pattern=r"^RESET_SIMULATION$")
    sessionId: str | None = Field(default=None, min_length=3, max_length=128)

