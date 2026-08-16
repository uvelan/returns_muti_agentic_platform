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
    #: Checked against `return_policy.normalized_return_methods` in the active
    #: snapshot, not by a `pattern=` here (CFG-03). A regex is a compile-time
    #: constant and this vocabulary is operator-owned: the previous fourteen-value
    #: pattern refused every method an operator added through the Control Centre,
    #: with a release activated and the configuration saying otherwise. See
    #: `operations/return_creation_policy.py` for where the check now happens and
    #: why it is on the request path rather than in a validator.
    shippingPathExpectation: str = Field(default="UNKNOWN", max_length=64)
    orderSource: str = Field(default="UNKNOWN", max_length=64)
    sourceWebOrderNumber: str | None = Field(default=None, max_length=128)
    trilogieOrderNumber: str | None = Field(default=None, max_length=128)
    productPresence: str | None = Field(default=None, max_length=64)
    branchReference: str | None = Field(default=None, max_length=128)
    associateReference: str | None = Field(default=None, max_length=128)
    pickupAssessment: dict[str, Any] | None = None
    #: `None` means "whatever assumption set is in force", and the request path
    #: stamps it from the active snapshot. A literal default here recorded the
    #: version that was true when this line was written onto a session the
    #: platform might be running a different release for -- a fabricated
    #: provenance, and one that persists.
    assumptionSetVersion: str | None = Field(default=None, max_length=128)
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


class CaseStatus(StrEnum):
    """Where a return has got to, as an associate would describe it.

    Deliberately not `ReturnStatus`, which is the *session's* execution status
    (QUEUED, RUNNING, INTERCEPTION_PENDING). A case outlives any one execution
    and can sit for days in a state no execution status can express -- the
    associate is not waiting on a queue, they are waiting on Support.
    """

    GATHERING_INFO = "GATHERING_INFO"
    AWAITING_BAY = "AWAITING_BAY"
    #: The four below arrive with the policy gate (plan sect. 7.7). They are
    #: added to *this* enum because it is the one `ReturnCaseWorkflow` writes and
    #: the one `cases.status` holds; the frozen `ReturnCaseStatus` in
    #: `case_projection/vocabulary.py` is a second, different set, and
    #: reconciling the two is the migration's job rather than the gate's.
    #:
    #: `POLICY_APPROVED` is transient: the workflow sets it and immediately opens
    #: the Support work item, which moves the case to `AWAITING_SUPPORT`. It is
    #: the one of the four with no counterpart in `ReturnCaseStatus`.
    POLICY_APPROVED = "POLICY_APPROVED"
    #: The evaluator asked for a human and **no work item was opened**. That is
    #: what distinguishes it from `AWAITING_SUPPORT`.
    AWAITING_POLICY_REVIEW = "AWAITING_POLICY_REVIEW"
    #: Terminal. Outside policy, and Support was never asked.
    POLICY_REJECTED = "POLICY_REJECTED"
    #: An operational failure parked the case -- today, a deployment with no
    #: published eligibility policy. Non-terminal: publishing one fixes it.
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    AWAITING_SUPPORT = "AWAITING_SUPPORT"
    RMA_RECEIVED = "RMA_RECEIVED"
    IN_TRANSIT = "IN_TRANSIT"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class FactChannel(StrEnum):
    """Which conversation a fact arrived on, or that no conversation did.

    `SYSTEM` covers facts derived from a source system or computed by an agent
    without anyone being asked -- a graph lookup, a sync result, a policy
    decision. Recording that as "channel A" would make provenance a lie.
    """

    CHANNEL_A = "CHANNEL_A"
    CHANNEL_B = "CHANNEL_B"
    SYSTEM = "SYSTEM"


class FactAcquisition(StrEnum):
    """How a fact came to be known -- the part of provenance that decides trust.

    `STATED` is what someone typed and nothing has verified. `OBSERVED` came
    from a source system. `DERIVED` was computed from other facts. `INFERRED` is
    a model's suggestion. An agent deciding whether to re-ask needs this: a
    STATED order number that found nothing is worth re-asking; an OBSERVED one
    is not.
    """

    STATED = "STATED"
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"


class CaseView(MutableContract):
    """One return, owning every fact about it.

    The two channel pointers are what make Channel B -> Channel A possible at
    all: today the only link between a support outcome and the associate's
    conversation is the browser matching an order reference client-side.
    """

    caseId: str
    tenantId: str
    principalId: str
    branchId: str | None = None
    status: CaseStatus = CaseStatus.GATHERING_INFO
    channelAConversationId: str | None = None
    channelBWorkItemId: str | None = None
    confirmedOrderReference: str | None = None
    # tenant | conversation | order | line-set. The idempotency boundary for
    # confirmation, and the reason a retried turn cannot fork the case.
    confirmationKey: str | None = None
    # The session this case's stage machine runs under, when one has been
    # started. Distinct from `workflowId`, which is the case's own durable
    # execution.
    sessionId: str | None = None
    workflowId: str | None = None
    configurationReleaseId: str | None = None
    graphGenerationId: str | None = None
    version: int = 0
    createdAt: datetime
    updatedAt: datetime


class CaseFactView(MutableContract):
    """One observation about a case, with how it was obtained.

    **Append-only.** Bay, Support, Fulfillment and Channel A all write facts
    concurrently; a single mutable `facts` sub-document would make the last
    writer win and silently drop what the others learned. Current state is a
    projection over this log (`latest_case_facts`), so provenance is never
    destroyed to record a newer value -- a correction supersedes rather than
    overwrites.
    """

    factId: str
    caseId: str
    factName: str
    value: Any = None
    agentId: str
    channel: FactChannel
    turnId: str | None = None
    sourceSystem: str | None = None
    # Which of two independent paths delivered this, when more than one can.
    sourcePath: str | None = None
    acquisitionMethod: FactAcquisition
    observedAt: datetime
    recordedAt: datetime
    supersedesFactId: str | None = None
    correlationId: str | None = None


class ReturnRecordShipmentView(MutableContract):
    """One parcel on one RMA, as the case aggregate stores it (D1, D4).

    Written by `CaseRepository.record_case_shipment` from an APPLIED
    `dbo.return_tracking` update, and read by
    `case_projection.assembly.project_shipments`. Declared here because the
    record document persists it and a stored field no contract names is a field
    that drifts.

    **Four fields, and the omissions are the point.**

    `trackingReference` is the parcel's identity -- `UQ_return_tracking_reference`
    makes it one row per number -- and is what `ShipmentProjection.shipmentId`
    reads. `carrier` is the code the feed filed, `None` when it filed none, in
    which case the projection falls back to the RMA's own `carrier` rather than
    asserting the parcel has none.

    The carrier's *status* is deliberately not here. `dbo.return_tracking`
    remains its authoritative home, and this shape exists to make the parcel
    visible rather than to become a second copy of the shipment log: the platform
    has no closed vocabulary to store a carrier status under (see
    `api.return_shipments.ShipmentUpdateRequest.shipmentStatus`), and its own
    reading of that status already reaches the case as the `fulfillment_status`
    fact. Nothing here would read a stored copy.
    """

    trackingReference: str
    carrier: str | None = None
    createdAt: datetime
    updatedAt: datetime


class ReturnRecordView(MutableContract):
    """One RMA, and everything that belongs to that RMA rather than to the case.

    First-class because one RMA covers N items and one case can carry N RMAs:
    a multi-item return can produce several RMAs with different labels and
    different return locations, and putting these on the case (one each) or on
    the item (one per item) can express neither.
    """

    returnRecordId: str
    caseId: str
    returnReference: str | None = None
    status: str = "DRAFT"
    #: How the goods come back, for **this** RMA (D23).
    #:
    #: Per record and never per case, for the same reason the label and the
    #: return location are: one case can carry several RMAs with different
    #: methods, and a case-level value would be read as the method of every one
    #: of them -- silently completing a `CUSTOMER_KEEP` record against a
    #: `PREPAID_PARCEL` requirement set, or hanging a `NO_PHYSICAL_RETURN` one
    #: forever waiting for a label.
    #:
    #: A plain string, not a closed enum. The catalogue is operator-owned
    #: through `return_policy.normalized_return_methods`, and a method an
    #: operator added must still render; an unmapped one leaves the completion
    #: profile unresolved rather than making the case unreadable.
    returnMethod: str | None = None
    #: Who is carrying the goods back, for **this** RMA (audit finding #9).
    #:
    #: The home `ShipmentProjection.carrier` reads from. Per record and never per
    #: case, for the same reason the tracking reference is: a split return goes
    #: back on two carriers, and a case-level value would attribute one package's
    #: carrier to the other's.
    #:
    #: A plain string with no closed vocabulary behind it. Unlike
    #: `returnMethod`, no operator-owned catalogue of carriers exists -- see
    #: `api.return_support.ReturnOutcomeRecord.carrier` for why one is not
    #: invented here -- so this holds whatever Support named, bounded to the
    #: length `dbo.return_record.carrier` can store.
    carrier: str | None = None
    returnLocation: str | None = None
    #: The tracking number **Support** stated when the RMA was issued. One slot,
    #: because it is one statement -- and not the same statement as `shipments`
    #: below, which is what carriers have actually filed. A record can carry
    #: either, both, or neither; `project_shipments` unions them on the tracking
    #: reference so that one parcel named twice is one parcel.
    trackingReference: str | None = None
    #: Every parcel a carrier has filed against this RMA (D1, D4).
    #:
    #: Absent on a record no carrier has scanned, which is the ordinary state of
    #: a freshly issued RMA -- absence here means "no carrier has filed one",
    #: never "this return has no parcels", and the two are distinguishable only
    #: because nothing writes an empty array to stand for the first.
    #:
    #: Plural because `dbo.return_tracking` has been RMA-scoped and plural since
    #: `006_return_shipment_state.sql`: a split return goes back in two parcels,
    #: each with its own tracking number and its own carrier, and the read model
    #: had one slot for them until this field existed.
    shipments: list[ReturnRecordShipmentView] | None = None
    labelReference: str | None = None
    shippingInstructionReference: str | None = None
    sourceSystem: str | None = None
    version: int = 0
    createdAt: datetime
    updatedAt: datetime


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
    #: What the stored document says, or nothing. The last copy of the literal
    #: lived here as a read fallback, which meant a session recorded before the
    #: field existed was reported as having run under an assumption set nobody
    #: had asserted it ran under.
    assumptionSetVersion: str | None = None
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
    cachedInputTokens: int | None = Field(default=None, ge=0)
    outputTokens: int | None = Field(default=None, ge=0)
    totalTokens: int | None = Field(default=None, ge=0)
    # Nullable, and null by default. The previous `= 0` meant every trace ever
    # recorded claimed a cost of zero, which is a measurement rather than an
    # absence: it charts, it sums, and it is wrong. `pricingStatus` is what
    # distinguishes "free" from "unpriced" (W4.11).
    estimatedCostMicros: int | None = Field(default=None, ge=0)
    pricingCurrency: str | None = None
    pricingStatus: str = "UNKNOWN"
    pricingVersion: str | None = None
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
