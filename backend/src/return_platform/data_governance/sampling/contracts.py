"""Strict immutable contracts for safely bounded data sampling."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    field_validator,
    model_validator,
)

from return_platform.data_governance.inventory.contracts.base_contracts import (
    require_utc_timestamp,
)
from return_platform.shared.governance import (
    DataStoreType,
    ObjectKind,
)

MAX_SAMPLE_ROWS = 25
MAX_SAMPLE_TEXT_LENGTH = 255
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991


SampleIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=512,
    ),
]

SampleFieldName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
    ),
]

SamplePrimitive = str | int | FiniteFloat | bool | None


class SampleValueKind(StrEnum):
    """Sanitized representation used for one sampled value."""

    NULL = "NULL"
    BOOLEAN = "BOOLEAN"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    TEXT = "TEXT"
    REDACTED = "REDACTED"
    BINARY = "BINARY"
    UNSUPPORTED = "UNSUPPORTED"


class SamplingContractModel(BaseModel):
    """Base configuration for immutable sampling contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class SampledField(SamplingContractModel):
    """One sanitized field from a sampled physical row."""

    name: SampleFieldName
    value: SamplePrimitive
    value_kind: SampleValueKind
    truncated: bool = False

    @model_validator(mode="after")
    def validate_value_kind(self) -> Self:
        """Ensure the declared value kind matches the safe value."""

        if self.value_kind == SampleValueKind.NULL:
            if self.value is not None:
                raise ValueError(
                    "NULL sample fields must contain a null value.",
                )

        elif self.value_kind == SampleValueKind.BOOLEAN:
            if not isinstance(self.value, bool):
                raise ValueError(
                    "BOOLEAN sample fields must contain a Boolean value.",
                )

        elif self.value_kind == SampleValueKind.INTEGER:
            if isinstance(self.value, bool) or not isinstance(self.value, int):
                raise ValueError(
                    "INTEGER sample fields must contain an integer value.",
                )

            if abs(self.value) > MAX_SAFE_JSON_INTEGER:
                raise ValueError(
                    "INTEGER sample fields must be JSON-safe.",
                )

        elif self.value_kind == SampleValueKind.FLOAT:
            if isinstance(self.value, bool) or not isinstance(self.value, float):
                raise ValueError(
                    "FLOAT sample fields must contain a finite float.",
                )

        elif self.value_kind == SampleValueKind.TEXT:
            if not isinstance(self.value, str):
                raise ValueError(
                    "TEXT sample fields must contain text.",
                )

            if len(self.value) > MAX_SAMPLE_TEXT_LENGTH:
                raise ValueError(
                    "TEXT sample fields exceed the safe text length.",
                )

        elif self.value_kind == SampleValueKind.REDACTED:
            if self.value != "[REDACTED]":
                raise ValueError(
                    "REDACTED sample fields must use the redaction marker.",
                )

        elif self.value_kind == SampleValueKind.BINARY:
            if self.value != "[BINARY DATA]":
                raise ValueError(
                    "BINARY sample fields must use the binary marker.",
                )

        elif self.value_kind == SampleValueKind.UNSUPPORTED:
            if self.value != "[UNSUPPORTED TYPE]":
                raise ValueError(
                    "UNSUPPORTED sample fields must use the safe marker.",
                )

        if self.truncated and self.value_kind != SampleValueKind.TEXT:
            raise ValueError(
                "Only text sample fields may be marked as truncated.",
            )

        return self


class SampledRow(SamplingContractModel):
    """One immutable row of sanitized sample data."""

    fields: tuple[SampledField, ...] = ()

    @model_validator(mode="after")
    def validate_fields(self) -> Self:
        """Reject duplicate field identities in one sampled row."""

        field_names = tuple(field.name for field in self.fields)

        if len(set(field_names)) != len(field_names):
            raise ValueError(
                "Sampled rows must not contain duplicate field names.",
            )

        return self

    def get_field(
        self,
        field_name: str,
    ) -> SampledField | None:
        """Return one field by its exact physical name."""

        for field in self.fields:
            if field.name == field_name:
                return field

        return None


class AssetSample(SamplingContractModel):
    """Immutable bounded sample tied to one catalog asset."""

    catalog_version: SampleIdentifier
    asset_id: SampleIdentifier

    store: DataStoreType
    database: SampleIdentifier
    namespace: SampleIdentifier | None = None
    object_name: SampleIdentifier
    object_kind: ObjectKind

    sampled_at: datetime
    row_limit: int = Field(
        ge=1,
        le=MAX_SAMPLE_ROWS,
    )

    ordering_guaranteed: bool = False
    rows: tuple[SampledRow, ...] = ()

    @field_validator("sampled_at")
    @classmethod
    def validate_sampled_at(
        cls,
        value: datetime,
    ) -> datetime:
        """Require an explicit UTC sampling timestamp."""

        return require_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Require a physical identity compatible with its data store."""

        if self.store == DataStoreType.SQLSERVER:
            if self.namespace is None:
                raise ValueError(
                    "SQL Server samples require a namespace.",
                )

            if self.object_kind not in {
                ObjectKind.TABLE,
                ObjectKind.VIEW,
            }:
                raise ValueError(
                    "SQL Server samples must represent a table or view.",
                )

        elif self.store == DataStoreType.MONGODB:
            if self.namespace is not None:
                raise ValueError(
                    "MongoDB samples must not use a namespace.",
                )

            if self.object_kind != ObjectKind.COLLECTION:
                raise ValueError(
                    "MongoDB samples must represent a collection.",
                )

        return self

    @model_validator(mode="after")
    def validate_row_bound(self) -> Self:
        """Ensure returned rows never exceed the authorized limit."""

        if len(self.rows) > self.row_limit:
            raise ValueError(
                "Sample row count exceeds the authorized row limit.",
            )

        return self

    @property
    def row_count(self) -> int:
        """Return the number of sanitized rows."""

        return len(self.rows)
