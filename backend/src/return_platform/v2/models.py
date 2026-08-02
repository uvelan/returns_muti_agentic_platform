"""Strict contracts for modular configuration, schema design, and order sync."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class V2Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ModuleStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    RELEASED = "RELEASED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    QUARANTINED = "QUARANTINED"


class ReleaseStatus(StrEnum):
    DRAFT = "DRAFT"
    DEPENDENCIES_RESOLVED = "DEPENDENCIES_RESOLVED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    MIGRATION_READY = "MIGRATION_READY"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class ModuleDependency(V2Model):
    module_id: str = Field(alias="moduleId", min_length=3, max_length=160)
    version_constraint: str = Field(alias="versionConstraint", min_length=1, max_length=40)


class ConfigurationModule(V2Model):
    module_id: str = Field(alias="moduleId", pattern=r"^[a-z][a-z0-9_.-]+$")
    module_type: str = Field(alias="moduleType", pattern=r"^[A-Z][A-Z0-9_]+$")
    schema_version: str = Field(alias="schemaVersion", pattern=r"^\d+\.\d+$")
    configuration_version: str = Field(
        alias="configurationVersion", pattern=r"^\d+\.\d+\.\d+$"
    )
    owner: str = Field(min_length=2, max_length=100)
    status: ModuleStatus = ModuleStatus.DRAFT
    dependencies: tuple[ModuleDependency, ...] = ()
    payload: dict[str, Any]
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(alias="createdAt", default_factory=utc_now)
    created_by: str = Field(alias="createdBy", min_length=1, max_length=200)
    revision: int = Field(default=1, ge=1)


class ModuleCreate(V2Model):
    module_id: str = Field(alias="moduleId", pattern=r"^[a-z][a-z0-9_.-]+$")
    module_type: str = Field(alias="moduleType", pattern=r"^[A-Z][A-Z0-9_]+$")
    schema_version: str = Field(alias="schemaVersion", pattern=r"^\d+\.\d+$")
    configuration_version: str = Field(
        alias="configurationVersion", pattern=r"^\d+\.\d+\.\d+$"
    )
    owner: str = Field(min_length=2, max_length=100)
    dependencies: tuple[ModuleDependency, ...] = ()
    payload: dict[str, Any]


class DraftCreate(V2Model):
    configuration_version: str = Field(
        alias="configurationVersion", pattern=r"^\d+\.\d+\.\d+$"
    )
    from_version: str | None = Field(
        default=None, alias="fromVersion", pattern=r"^\d+\.\d+\.\d+$"
    )


class FieldPatch(V2Model):
    path: tuple[str | int, ...] = Field(min_length=1, max_length=32)
    value: Any = None
    operation: Literal["SET", "REMOVE", "APPEND"] = "SET"
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class ValidationIssue(V2Model):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    path: tuple[str | int, ...] = ()
    message: str = Field(min_length=1, max_length=500)
    severity: Literal["ERROR", "WARNING"] = "ERROR"
    suggested_resolution: str | None = Field(
        default=None, alias="suggestedResolution", max_length=500
    )


class ValidationResult(V2Model):
    valid: bool
    issues: tuple[ValidationIssue, ...] = ()
    checksum: str | None = None


class ReleaseModuleRef(V2Model):
    module_id: str = Field(alias="moduleId")
    version: str
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReleaseManifest(V2Model):
    release_id: str = Field(alias="releaseId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$")
    status: ReleaseStatus = ReleaseStatus.DRAFT
    modules: tuple[ReleaseModuleRef, ...]
    dependency_lock_digest: str = Field(
        alias="dependencyLockDigest", pattern=r"^[a-f0-9]{64}$"
    )
    created_at: datetime = Field(alias="createdAt", default_factory=utc_now)
    created_by: str = Field(alias="createdBy", min_length=1)
    activated_at: datetime | None = Field(default=None, alias="activatedAt")


class ReleaseCreate(V2Model):
    release_id: str = Field(alias="releaseId", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$")
    modules: tuple[ReleaseModuleRef, ...]


class ImportRequest(V2Model):
    format: Literal["JSON", "YAML"]
    content: str = Field(min_length=2, max_length=2_000_000)


class ImportRecord(V2Model):
    import_id: str = Field(alias="importId")
    status: Literal["QUARANTINED", "VALIDATED", "REJECTED", "DRAFTS_CREATED"]
    modules: tuple[ConfigurationModule, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()
    created_at: datetime = Field(alias="createdAt", default_factory=utc_now)
    created_by: str = Field(alias="createdBy")


class SourceField(V2Model):
    path: str = Field(min_length=1, max_length=300)
    data_type: str = Field(alias="dataType", min_length=1, max_length=80)
    nullable: bool = True
    key: bool = False
    sensitive: bool = False


class SourceStructure(V2Model):
    source_id: str = Field(alias="sourceId", min_length=1)
    dataset: str = Field(min_length=1)
    fields: tuple[SourceField, ...]
    identity_paths: tuple[str, ...] = Field(default=(), alias="identityPaths")
    candidate_joins: tuple[str, ...] = Field(default=(), alias="candidateJoins")
    fingerprint: str = Field(min_length=1, max_length=200)


class ProposalCommand(V2Model):
    module_id: str = Field(alias="moduleId")
    path: tuple[str | int, ...]
    operation: Literal["SET", "REMOVE", "APPEND"]
    current_value: Any = Field(default=None, alias="currentValue")
    proposed_value: Any = Field(default=None, alias="proposedValue")
    evidence: tuple[str, ...] = ()
    reason: str
    change_classification: str = Field(alias="changeClassification")
    required_owner: str = Field(alias="requiredOwner")


class SchemaQuestion(V2Model):
    question_id: str = Field(alias="questionId")
    field_path: str = Field(alias="fieldPath")
    prompt: str
    reason: str
    required_owner: str = Field(alias="requiredOwner")
    evidence: tuple[str, ...] = ()
    options: tuple[str, ...] = ()


class SchemaDesignCreate(V2Model):
    selected_modules: tuple[str, ...] = Field(alias="selectedModules")
    requested_capabilities: tuple[str, ...] = Field(alias="requestedCapabilities", min_length=1)
    source_structures: tuple[SourceStructure, ...] = Field(alias="sourceStructures")
    existing_schema: dict[str, Any] | None = Field(default=None, alias="existingSchema")


class SchemaAnswer(V2Model):
    question_id: str = Field(alias="questionId")
    value: Any


class SchemaDesignContext(V2Model):
    request_id: str = Field(alias="requestId")
    context_version: int = Field(alias="contextVersion", ge=1)
    selected_modules: tuple[str, ...] = Field(alias="selectedModules")
    requested_capabilities: tuple[str, ...] = Field(alias="requestedCapabilities")
    source_structures: tuple[SourceStructure, ...] = Field(alias="sourceStructures")
    existing_schema: dict[str, Any] | None = Field(default=None, alias="existingSchema")
    answers: dict[str, Any] = Field(default_factory=dict)
    commands: tuple[ProposalCommand, ...] = ()
    current_question: SchemaQuestion | None = Field(default=None, alias="currentQuestion")
    status: Literal["ANALYZING", "WAITING_FOR_ANSWER", "REVIEW_READY", "INVALID"]
    issues: tuple[ValidationIssue, ...] = ()
    created_by: str = Field(alias="createdBy")
    updated_at: datetime = Field(alias="updatedAt", default_factory=utc_now)


class AnchorType(StrEnum):
    FULL_ORDER_ID = "FULL_ORDER_ID"
    ORDER_REFERENCE = "ORDER_REFERENCE"
    TRACKING_NUMBER = "TRACKING_NUMBER"
    INVOICE_NUMBER = "INVOICE_NUMBER"
    DELIVERY_TICKET = "DELIVERY_TICKET"
    CUSTOMER_PO = "CUSTOMER_PO"


class OrderAnchor(V2Model):
    type: AnchorType
    value: str = Field(min_length=1, max_length=160)
    account_scope: str | None = Field(default=None, alias="accountScope", max_length=100)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("anchor value must not be blank")
        return normalized


class AuthorizationScope(V2Model):
    accounts: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()
    max_candidates: int = Field(default=20, alias="maxCandidates", ge=1, le=100)


class PartialSyncRequest(V2Model):
    anchor: OrderAnchor
    release_id: str = Field(alias="releaseId")
    authorization_scope: AuthorizationScope = Field(alias="authorizationScope")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=200)


class FullSyncRequest(V2Model):
    full_order_id: str = Field(alias="fullOrderId", min_length=3, max_length=200)
    release_id: str = Field(alias="releaseId")
    authorization_scope: AuthorizationScope = Field(alias="authorizationScope")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=200)


class OrderLineProjection(V2Model):
    full_order_line_id: str = Field(alias="fullOrderLineId")
    line_number: str = Field(alias="lineNumber")
    item_number: str | None = Field(default=None, alias="itemNumber")
    description: str | None = None
    quantity_ordered: float | None = Field(default=None, alias="quantityOrdered", ge=0)
    quantity_returned: float | None = Field(default=None, alias="quantityReturned", ge=0)


class OrderProjection(V2Model):
    full_order_id: str = Field(alias="fullOrderId")
    account: str
    order_number: str = Field(alias="orderNumber")
    customer_id: str | None = Field(default=None, alias="customerId")
    customer_name: str | None = Field(default=None, alias="customerName")
    customer_po: str | None = Field(default=None, alias="customerPo")
    delivery_ticket: str | None = Field(default=None, alias="deliveryTicket")
    invoice_numbers: tuple[str, ...] = Field(default=(), alias="invoiceNumbers")
    tracking_numbers: tuple[str, ...] = Field(default=(), alias="trackingNumbers")
    source_revision: str = Field(alias="sourceRevision")
    lines: tuple[OrderLineProjection, ...] = ()


class SyncStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NARROWING_REQUIRED = "NARROWING_REQUIRED"
    COMPLETED = "COMPLETED"
    NOT_FOUND = "NOT_FOUND"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class SyncResult(V2Model):
    request_id: str = Field(alias="requestId")
    sync_type: Literal["PARTIAL_ORDER_SYNC", "FULL_ORDER_SYNC"] = Field(alias="syncType")
    status: SyncStatus
    release_id: str = Field(alias="releaseId")
    full_order_ids: tuple[str, ...] = Field(default=(), alias="fullOrderIds")
    orders: tuple[OrderProjection, ...] = ()
    records_read: int = Field(default=0, alias="recordsRead", ge=0)
    graph_writes: int = Field(default=0, alias="graphWrites", ge=0)
    message: str
    digest: str
    created_at: datetime = Field(alias="createdAt", default_factory=utc_now)


class SourceOrderRecord(V2Model):
    account: str = Field(min_length=1)
    order_number: str = Field(alias="orderNumber", min_length=1)
    customer_id: str | None = Field(default=None, alias="customerId")
    customer_name: str | None = Field(default=None, alias="customerName")
    customer_po: str | None = Field(default=None, alias="customerPo")
    delivery_ticket: str | None = Field(default=None, alias="deliveryTicket")
    invoice_numbers: tuple[str, ...] = Field(default=(), alias="invoiceNumbers")
    tracking_numbers: tuple[str, ...] = Field(default=(), alias="trackingNumbers")
    source_revision: str = Field(alias="sourceRevision")
    lines: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def require_unique_lines(self) -> SourceOrderRecord:
        numbers = [str(line.get("lineNumber", "")).strip() for line in self.lines]
        if any(not number for number in numbers):
            raise ValueError("every order line requires immutable lineNumber")
        if len(numbers) != len(set(numbers)):
            raise ValueError("order line numbers must be unique within an order")
        return self
