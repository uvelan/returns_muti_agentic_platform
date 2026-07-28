from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GuardSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    DENIAL = "DENIAL"

class FindingCode(str, Enum):
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

class ValidationResultState(str, Enum):
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
