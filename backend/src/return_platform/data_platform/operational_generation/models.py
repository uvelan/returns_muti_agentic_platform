from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class GuardSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    DENIAL = "DENIAL"


class FindingCode(StrEnum):
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_ENUM = "INVALID_ENUM"
    PATTERN_MISMATCH = "PATTERN_MISMATCH"
    LENGTH_VIOLATION = "LENGTH_VIOLATION"
    RANGE_VIOLATION = "RANGE_VIOLATION"
    DUPLICATE_NATURAL_KEY = "DUPLICATE_NATURAL_KEY"
    INVALID_FOREIGN_KEY = "INVALID_FOREIGN_KEY"
    OMC_DENIAL = "OMC_DENIAL"
    POLICY_DENIAL = "POLICY_DENIAL"
    DERIVED_PROJECTION_DENIAL = "DERIVED_PROJECTION_DENIAL"
    PII_VIOLATION = "PII_VIOLATION"
    INVALID_JSON_SHAPE = "INVALID_JSON_SHAPE"
    MISSING_ASSET = "MISSING_ASSET"
    GENERATED_DATA_DISABLED = "GENERATED_DATA_DISABLED"


class GuardFinding(BaseModel):
    code: FindingCode
    severity: GuardSeverity
    asset_id: str
    record_index: int | None = None
    field_path: str | None = None
    message: str
    expected_value: Any | None = None
    rejected_value: str | None = None
    remediation_hint: str | None = None


class ValidationResultState(StrEnum):
    VALID = "VALID"
    INVALID_RECORD = "INVALID_RECORD"
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    POLICY_DENIED = "POLICY_DENIED"


class ValidationResult(BaseModel):
    state: ValidationResultState
    findings: list[GuardFinding] = Field(default_factory=list)


class OperationProposal(BaseModel):
    asset_id: str
    records: list[dict[str, Any]]


class GenerationMode(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI_ASSISTED = "AI_ASSISTED"


class CollisionPolicy(StrEnum):
    REJECT = "REJECT"
    GENERATE_NEW_KEYS = "GENERATE_NEW_KEYS"
    SKIP_EXISTING = "SKIP_EXISTING"


class ScenarioType(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_ids: tuple[str, ...]
    record_count: int
    deterministic_seed: int
    tenant_id: str
    branch_id: str | None = None
    region_id: str | None = None
    date_from: datetime
    date_to: datetime
    generation_mode: GenerationMode
    collision_policy: CollisionPolicy
    scenario_distribution: Mapping[ScenarioType, int]


class GeneratedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str
    temporary_record_key: str
    values: Mapping[str, JsonValue]
    dependency_keys: tuple[str, ...]
    generation_index: int


class GenerationProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime
    generator_version: str
    metrics: Mapping[str, Any] = Field(default_factory=dict)
    ai_traces: list[Any] = Field(default_factory=list)


class OperationalGenerationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: UUID
    schema_release_id: str
    schema_checksum: str
    deterministic_seed: int
    generation_mode: GenerationMode
    records: tuple[GeneratedRecord, ...]
    provenance: GenerationProvenance
    proposal_checksum: str
