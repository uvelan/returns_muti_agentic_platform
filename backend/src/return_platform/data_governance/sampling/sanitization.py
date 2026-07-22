"""Shared sanitization for bounded SQL Server and MongoDB samples."""

import math
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Final
from uuid import UUID

from return_platform.data_governance.sampling.contracts import (
    MAX_SAFE_JSON_INTEGER,
    MAX_SAMPLE_TEXT_LENGTH,
    SampledField,
    SampledRow,
    SampleValueKind,
)

_REDACTED_MARKER: Final = "[REDACTED]"
_BINARY_MARKER: Final = "[BINARY DATA]"
_UNSUPPORTED_MARKER: Final = "[UNSUPPORTED TYPE]"


class SamplingSanitizationError(ValueError):
    """Safe failure caused by malformed sampled row metadata."""


def normalize_redaction_fields(
    redact_fields: Iterable[str],
) -> frozenset[str]:
    """Normalize catalog redaction fields for case-insensitive matching."""

    normalized_fields: set[str] = set()

    for field_name in redact_fields:
        if not field_name:
            raise SamplingSanitizationError(
                "Redaction field names must not be empty.",
            )

        if field_name != field_name.strip():
            raise SamplingSanitizationError(
                "Redaction field names must not contain surrounding whitespace.",
            )

        normalized_name = field_name.casefold()

        if normalized_name in normalized_fields:
            raise SamplingSanitizationError(
                "Redaction fields must be case-insensitively unique.",
            )

        normalized_fields.add(normalized_name)

    return frozenset(normalized_fields)


def _text_field(
    *,
    field_name: str,
    value: str,
) -> SampledField:
    """Create a safely bounded text field."""

    is_truncated = len(value) > MAX_SAMPLE_TEXT_LENGTH
    safe_value = value[:MAX_SAMPLE_TEXT_LENGTH]

    return SampledField(
        name=field_name,
        value=safe_value,
        value_kind=SampleValueKind.TEXT,
        truncated=is_truncated,
    )


def _unsupported_field(
    *,
    field_name: str,
) -> SampledField:
    """Represent an unsupported value without exposing its contents."""

    return SampledField(
        name=field_name,
        value=_UNSUPPORTED_MARKER,
        value_kind=SampleValueKind.UNSUPPORTED,
    )


def sanitize_sample_value(
    *,
    field_name: str,
    value: object,
    normalized_redaction_fields: frozenset[str],
) -> SampledField:
    """Convert one untrusted driver value into an immutable safe field."""

    if not isinstance(field_name, str):
        raise SamplingSanitizationError(
            "Sample field names must be text.",
        )

    if not field_name:
        raise SamplingSanitizationError(
            "Sample field names must not be empty.",
        )

    if len(field_name) > 128:
        raise SamplingSanitizationError(
            "Sample field names exceed the supported length.",
        )

    if field_name.casefold() in normalized_redaction_fields:
        return SampledField(
            name=field_name,
            value=_REDACTED_MARKER,
            value_kind=SampleValueKind.REDACTED,
        )

    if value is None:
        return SampledField(
            name=field_name,
            value=None,
            value_kind=SampleValueKind.NULL,
        )

    if isinstance(value, bool):
        return SampledField(
            name=field_name,
            value=value,
            value_kind=SampleValueKind.BOOLEAN,
        )

    if isinstance(value, int):
        if abs(value) <= MAX_SAFE_JSON_INTEGER:
            return SampledField(
                name=field_name,
                value=value,
                value_kind=SampleValueKind.INTEGER,
            )

        return _text_field(
            field_name=field_name,
            value=str(value),
        )

    if isinstance(value, float):
        if not math.isfinite(value):
            return _unsupported_field(
                field_name=field_name,
            )

        return SampledField(
            name=field_name,
            value=value,
            value_kind=SampleValueKind.FLOAT,
        )

    if isinstance(value, str):
        return _text_field(
            field_name=field_name,
            value=value,
        )

    if isinstance(
        value,
        (
            bytes,
            bytearray,
            memoryview,
        ),
    ):
        return SampledField(
            name=field_name,
            value=_BINARY_MARKER,
            value_kind=SampleValueKind.BINARY,
        )

    if isinstance(value, Decimal):
        if not value.is_finite():
            return _unsupported_field(
                field_name=field_name,
            )

        return _text_field(
            field_name=field_name,
            value=format(value, "f"),
        )

    if isinstance(value, datetime):
        return _text_field(
            field_name=field_name,
            value=value.isoformat(),
        )

    if isinstance(value, date):
        return _text_field(
            field_name=field_name,
            value=value.isoformat(),
        )

    if isinstance(value, time):
        return _text_field(
            field_name=field_name,
            value=value.isoformat(),
        )

    if isinstance(value, UUID):
        return _text_field(
            field_name=field_name,
            value=str(value),
        )

    return _unsupported_field(
        field_name=field_name,
    )


def sanitize_sample_row(
    *,
    row: Mapping[str, object],
    normalized_redaction_fields: frozenset[str],
) -> SampledRow:
    """Sanitize one flat physical row while preserving field order."""

    fields: list[SampledField] = []

    for field_name, value in row.items():
        fields.append(
            sanitize_sample_value(
                field_name=field_name,
                value=value,
                normalized_redaction_fields=(normalized_redaction_fields),
            ),
        )

    return SampledRow(
        fields=tuple(fields),
    )
