from datetime import UTC, datetime
from enum import StrEnum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


class DependencyStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    STARTING = "STARTING"
    UNKNOWN = "UNKNOWN"


class DependencyErrorCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    AUTH_FAILED = "AUTH_FAILED"
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    QUERY_FAILED = "QUERY_FAILED"
    UNINITIALIZED = "UNINITIALIZED"
    QUEUE_SATURATED = "QUEUE_SATURATED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class FreshnessStatus(StrEnum):
    LIVE = "LIVE"
    CACHED = "CACHED"
    STALE = "STALE"


class DependencyProbeResult(ContractModel):
    status: DependencyStatus
    latency_ms: int | None = Field(
        default=None,
        ge=0,
    )
    checked_at: AwareDatetime
    error_code: DependencyErrorCode | None = None
    safe_message: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )

    @field_validator("checked_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class PageMeta(ContractModel):
    next_cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    has_more: bool = False
    page_size: int = Field(
        default=50,
        ge=1,
        le=500,
    )


class WarningMeta(ContractModel):
    source: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    code: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    message: str = Field(
        min_length=1,
        max_length=500,
    )


class ResponseMeta(ContractModel):
    schema_version: str = Field(
        default="1.0",
        pattern=r"^\d+\.\d+$",
    )
    request_id: str = Field(
        min_length=1,
        max_length=64,
    )
    generated_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    freshness: FreshnessStatus = FreshnessStatus.LIVE
    partial: bool = False
    warnings: tuple[WarningMeta, ...] = Field(default_factory=tuple)

    @field_validator("generated_at")
    @classmethod
    def normalize_generated_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class APIResponse[T](ContractModel):
    data: T | None = None
    page: PageMeta | None = None
    meta: ResponseMeta
