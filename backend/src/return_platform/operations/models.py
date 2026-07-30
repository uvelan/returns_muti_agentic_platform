"""Operational API contracts for the end-to-end return product."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MutableContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ReturnStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    INTERCEPTION_PENDING = "INTERCEPTION_PENDING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    WAITING_SUPPORT = "WAITING_SUPPORT"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SupportCaseStatus(StrEnum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class AIDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    WAITING_SUPPORT = "WAITING_SUPPORT"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"


class AIRequestStatus(StrEnum):
    CREATED = "CREATED"
    REDACTED = "REDACTED"
    POLICY_CHECKED = "POLICY_CHECKED"
    INTERCEPTION_PENDING = "INTERCEPTION_PENDING"
    DISPATCHED = "DISPATCHED"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    RESPONSE_VALIDATED = "RESPONSE_VALIDATED"
    DECISION_PERSISTED = "DECISION_PERSISTED"
    REDACTION_FAILED = "REDACTION_FAILED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RESPONSE_INVALID = "RESPONSE_INVALID"
    CANCELLED = "CANCELLED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class ReturnCreateRequest(MutableContract):
    customerReference: str = Field(min_length=1, max_length=128)
    orderReference: str = Field(min_length=1, max_length=128)
    itemReferences: list[str] = Field(min_length=1, max_length=50)
    productReferences: list[str] = Field(default_factory=list, max_length=50)
    processingWarehouseReference: str | None = Field(default=None, max_length=128)
    productType: str | None = Field(default=None, max_length=64)
    reasonCode: str = Field(min_length=1, max_length=64)
    returnQuantity: int = Field(default=1, ge=1, le=10_000)
    packageCount: int = Field(default=1, ge=1, le=10_000)
    shippingPathExpectation: str = Field(
        default="UNKNOWN",
        pattern=r"^(PPL|BOL|CUSTOMER_SHIP|NO_LABEL|DIRECT_VENDOR|FIELD_SCRAP|PREPAID_PARCEL|BRANCH_UPS|BRANCH_LTL|OFFSITE_PARCEL|OFFSITE_LTL|NO_PHYSICAL_RETURN|CUSTOMER_KEEP|UNKNOWN)$",
    )
    orderSource: str = Field(default="UNKNOWN", max_length=64)
    sourceWebOrderNumber: str | None = Field(default=None, max_length=128)
    trilogieOrderNumber: str | None = Field(default=None, max_length=128)
    productPresence: str | None = Field(default=None, max_length=64)
    branchReference: str | None = Field(default=None, max_length=128)
    associateReference: str | None = Field(default=None, max_length=128)
    pickupAssessment: dict[str, Any] | None = None
    assumptionSetVersion: str = Field(default="FERGUSON-RETURN-ASSUMPTIONS-1.0", max_length=128)
    notes: str | None = Field(default=None, max_length=2_000)
    channel: str = Field(default="SYSTEM", pattern=r"^(CUSTOMER|ASSOCIATE|SYSTEM)$")
    workflowMode: str = Field(default="PRODUCTION_V2", pattern=r"^(PRODUCTION_V2|LEGACY_V1)$")
    idempotencyKey: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("itemReferences")
    @classmethod
    def unique_items(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("itemReferences must contain unique non-blank values")
        return normalized


class ReturnSessionView(MutableContract):
    id: str
    correlationId: str
    workflowId: str | None = None
    workflowMode: str = "PRODUCTION_V2"
    customerReference: str
    orderReference: str
    itemReferences: list[str]
    productReferences: list[str]
    processingWarehouseReference: str | None = None
    productType: str | None = None
    reasonCode: str
    returnQuantity: int = Field(ge=1, le=10_000)
    packageCount: int = Field(ge=1, le=10_000)
    shippingPathExpectation: str
    orderSource: str = "UNKNOWN"
    sourceWebOrderNumber: str | None = None
    trilogieOrderNumber: str | None = None
    productPresence: str | None = None
    branchReference: str | None = None
    associateReference: str | None = None
    pickupAssessment: dict[str, Any] | None = None
    assumptionSetVersion: str = "FERGUSON-RETURN-ASSUMPTIONS-1.0"
    productionWorkflowVersion: str | None = None
    returnRequestSnapshotId: str | None = None
    notes: str | None = None
    channel: str
    status: ReturnStatus
    currentStage: str
    progressPercentage: int = Field(ge=0, le=100)
    eligibilityDecision: AIDecision | None = None
    returnReference: str | None = None
    supportTicketReference: str | None = None
    supportWorkItemId: str | None = None
    supportStatus: str | None = None
    omcReturnVersion: str | None = None
    approvedReturnMethod: str | None = None
    shippingInstructionReference: str | None = None
    customerResolutionStatus: str = "PENDING"
    physicalReturnStatus: str = "NOT_STARTED"
    warehouseStatus: str = "NOT_REQUIRED_OR_PENDING"
    vendorRecoveryStatus: str = "NOT_REQUIRED_OR_PENDING"
    caseClosureStatus: str = "OPEN"
    trackingReference: str | None = None
    bayReference: str | None = None
    feedbackReference: str | None = None
    supportCaseId: str | None = None
    aiRequestId: str | None = None
    failureCode: str | None = None
    failureMessage: str | None = None
    version: int = Field(ge=0)
    createdAt: datetime
    updatedAt: datetime


class TimelineEvent(MutableContract):
    id: str
    streamId: str
    sequence: int = Field(ge=1)
    eventType: str
    actorType: str
    actorId: str
    payload: dict[str, Any]
    occurredAt: datetime
    publishedAt: datetime | None = None


class SupportCaseView(MutableContract):
    id: str
    sessionId: str
    caseType: str
    status: SupportCaseStatus
    priority: str
    reason: str
    assignedTo: str | None = None
    resolution: str | None = None
    decision: AIDecision | None = None
    slaDueAt: datetime
    slaBreached: bool
    version: int = Field(ge=0)
    createdAt: datetime
    updatedAt: datetime


class SupportOperationRequest(MutableContract):
    operation: str = Field(pattern=r"^(ASSIGN|APPROVE|REJECT|RETRY|CANCEL|RESUME)$")
    reason: str = Field(min_length=3, max_length=1_000)
    expectedVersion: int = Field(ge=0)
    assignee: str | None = Field(default=None, max_length=128)


class AITraceView(MutableContract):
    id: str
    sessionId: str | None = None
    status: AIRequestStatus
    taskId: str = "RETURN_ELIGIBILITY_V1"
    configuredTier: str = "LIGHTWEIGHT"
    selectedTier: str | None = None
    provider: str | None = None
    model: str | None = None
    credentialId: str | None = None
    routeId: str | None = None
    promptVersion: str
    redactedInput: dict[str, Any]
    systemPrompt: str
    requestDigest: str
    responseText: str | None = None
    decision: AIDecision | None = None
    explanation: str | None = None
    confidenceMillionths: int | None = Field(default=None, ge=0, le=1_000_000)
    latencyMs: int | None = Field(default=None, ge=0)
    rateLimitWaitMs: int = Field(default=0, ge=0)
    inputTokens: int | None = Field(default=None, ge=0)
    outputTokens: int | None = Field(default=None, ge=0)
    totalTokens: int | None = Field(default=None, ge=0)
    estimatedCostMicrousd: int = Field(default=0, ge=0)
    responseDigest: str | None = None
    attempts: int = Field(default=0, ge=0)
    fallbackUsed: bool = False
    safetyStatus: str = "SAFE"
    safetySignals: list[str] = Field(default_factory=list)
    selectionReason: str | None = None
    errorCode: str | None = None
    interceptedBy: str | None = None
    interceptionReason: str | None = None
    originalRequestDigest: str | None = None
    version: int = Field(ge=0)
    createdAt: datetime
    updatedAt: datetime


class AIReplayRequest(MutableContract):
    provider: str | None = Field(default=None, max_length=32)
    editedSystemPrompt: str | None = Field(default=None, min_length=10, max_length=8_000)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        allowed = {"GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR"}
        if normalized not in allowed:
            raise ValueError("provider is invalid")
        return normalized


class AICompareRequest(MutableContract):
    providers: list[str] = Field(min_length=2, max_length=6)

    @field_validator("providers")
    @classmethod
    def validate_providers(cls, value: list[str]) -> list[str]:
        allowed = {"GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR"}
        normalized = [provider.strip().upper() for provider in value]
        if len(set(normalized)) != len(normalized) or any(
            provider not in allowed for provider in normalized
        ):
            raise ValueError("providers are invalid")
        return normalized


class AIInterceptionRequest(MutableContract):
    action: str = Field(pattern=r"^(APPROVE|REJECT|REVIEW_REQUIRED|EDIT_AND_DISPATCH|CANCEL)$")
    reason: str = Field(min_length=3, max_length=1_000)
    expectedVersion: int = Field(ge=0)
    editedSystemPrompt: str | None = Field(default=None, min_length=10, max_length=8_000)


class AISimulatorRequest(MutableContract):
    customerReference: str = Field(min_length=1, max_length=128)
    orderReferences: list[str] = Field(min_length=1, max_length=20)
    reasonCode: str = Field(min_length=1, max_length=64)
    requestedDecision: AIDecision | None = None
    persist: bool = True


class AIGatewaySettingsView(MutableContract):
    interceptMode: bool
    providerOrder: list[str]
    version: int = Field(ge=0)
    updatedAt: datetime
    updatedBy: str


class AIGatewaySettingsUpdate(MutableContract):
    interceptMode: bool
    providerOrder: list[str] = Field(min_length=1, max_length=6)
    expectedVersion: int = Field(ge=0)

    @field_validator("providerOrder")
    @classmethod
    def validate_provider_order(cls, value: list[str]) -> list[str]:
        allowed = {"GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR"}
        normalized = [provider.strip().upper() for provider in value]
        if len(set(normalized)) != len(normalized) or any(
            provider not in allowed for provider in normalized
        ):
            raise ValueError("providerOrder is invalid")
        return normalized


class SeedStatusView(MutableContract):
    version: str
    digest: str
    appliedAt: datetime | None = None
    appliedBy: str | None = None
    ready: bool
    counts: dict[str, int]
    scenarioCounts: dict[str, int]
    validationErrors: list[str]
    requestedRecordLimit: int | None = None


class SeedOperationStatus(StrEnum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class SeedApplyRequest(MutableContract):
    recordLimit: int = Field(ge=10, le=1_000_000)


class SeedDeleteRequest(MutableContract):
    confirmation: str = Field(min_length=1, max_length=64)

    @field_validator("confirmation")
    @classmethod
    def require_delete_confirmation(cls, value: str) -> str:
        if value != "DELETE SEED DATA":
            raise ValueError('confirmation must equal "DELETE SEED DATA"')
        return value


class SeedOperationView(MutableContract):
    operationId: str | None = None
    kind: str | None = None
    status: SeedOperationStatus = SeedOperationStatus.IDLE
    requestedRecordLimit: int | None = None
    processedRecords: int = 0
    totalRecords: int = 0
    phase: str = "Idle"
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    error: str | None = None


def normalize_utc_datetime(value: datetime) -> datetime:
    """Treat timezone-naive persistence timestamps as UTC and normalize aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)
