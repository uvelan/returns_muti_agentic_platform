"""Shared immutable contracts for canonical Return Platform entities."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
)

__all__ = [
    "CanonicalBaseModel",
    "CanonicalIdentifier",
    "IdentityQuality",
    "NonBlankText",
    "Sha256Digest",
    "SourceProvenance",
    "UtcDateTime",
    "VersionReference",
]


class IdentityQuality(StrEnum):
    """Strength of the evidence supporting a canonical entity identity."""

    VERIFIED = "VERIFIED"
    CONDITIONAL = "CONDITIONAL"
    FALLBACK = "FALLBACK"


CanonicalIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=512,
        pattern=r"^[^\s\x00-\x1f\x7f]+$",
    ),
]
"""Stable canonical key with no whitespace or control characters."""

NonBlankText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=1_024,
    ),
]
"""Strict bounded text that cannot be empty after trimming."""

VersionReference = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$",
    ),
]
"""Bounded version or profile reference used by code-owned configuration."""

Sha256Digest = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[0-9a-f]{64}$",
    ),
]
"""Canonical lowercase hexadecimal SHA-256 digest."""


def _normalize_to_utc(value: datetime) -> datetime:
    """Normalize an already timezone-aware datetime to UTC."""
    return value.astimezone(UTC)


UtcDateTime = Annotated[AwareDatetime, AfterValidator(_normalize_to_utc)]
"""Timezone-aware datetime normalized to the UTC singleton timezone."""


class CanonicalBaseModel(BaseModel):
    """Strict immutable base for canonical domain contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class SourceProvenance(CanonicalBaseModel):
    """Traceable source and normalization evidence for one canonical record."""

    source_system: CanonicalIdentifier
    source_database: NonBlankText
    source_asset: NonBlankText
    source_record_id: NonBlankText
    source_updated_at: UtcDateTime | None = None
    source_version: VersionReference | None = None
    source_event_id: NonBlankText | None = None
    source_hash: Sha256Digest | None = None
    observed_at: UtcDateTime
    mapping_version: VersionReference
    configuration_version: VersionReference
    configuration_digest: Sha256Digest
