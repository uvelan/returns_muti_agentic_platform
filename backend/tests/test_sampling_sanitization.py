"""Tests for shared SQL Server and MongoDB sample sanitization."""

import math
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from return_platform.data_governance.sampling.contracts import (
    MAX_SAFE_JSON_INTEGER,
    MAX_SAMPLE_TEXT_LENGTH,
    SampleValueKind,
)
from return_platform.data_governance.sampling.sanitization import (
    SamplingSanitizationError,
    normalize_redaction_fields,
    sanitize_sample_row,
    sanitize_sample_value,
)


def test_normalize_redaction_fields_is_case_insensitive() -> None:
    """Normalize catalog redaction fields for physical-name matching."""

    normalized = normalize_redaction_fields(
        (
            "Email",
            "PHONE",
            "customer_id",
        ),
    )

    assert normalized == frozenset(
        {
            "email",
            "phone",
            "customer_id",
        },
    )


def test_empty_redaction_configuration_is_valid() -> None:
    """Permit assets that do not require field redaction."""

    normalized = normalize_redaction_fields(())

    assert normalized == frozenset()


@pytest.mark.parametrize(
    "redact_fields",
    [
        ("",),
        (" email",),
        ("email ",),
    ],
)
def test_invalid_redaction_field_name_is_rejected(
    redact_fields: tuple[str, ...],
) -> None:
    """Reject empty or whitespace-padded redaction field names."""

    with pytest.raises(
        SamplingSanitizationError,
        match="Redaction field names",
    ):
        normalize_redaction_fields(
            redact_fields,
        )


def test_case_insensitive_duplicate_redaction_fields_are_rejected() -> None:
    """Reject ambiguous redaction configuration."""

    with pytest.raises(
        SamplingSanitizationError,
        match="case-insensitively unique",
    ):
        normalize_redaction_fields(
            (
                "email",
                "Email",
            ),
        )


def test_redaction_is_applied_case_insensitively() -> None:
    """Redact matching fields without exposing their underlying value."""

    field = sanitize_sample_value(
        field_name="Email",
        value="customer@example.com",
        normalized_redaction_fields=frozenset(
            {
                "email",
            },
        ),
    )

    assert field.name == "Email"
    assert field.value == "[REDACTED]"
    assert field.value_kind == SampleValueKind.REDACTED
    assert field.truncated is False


def test_redaction_takes_precedence_over_binary_handling() -> None:
    """Never reveal the underlying value category for redacted fields."""

    field = sanitize_sample_value(
        field_name="secret_payload",
        value=b"sensitive bytes",
        normalized_redaction_fields=frozenset(
            {
                "secret_payload",
            },
        ),
    )

    assert field.value == "[REDACTED]"
    assert field.value_kind == SampleValueKind.REDACTED


def test_null_value_is_preserved() -> None:
    """Represent database null values explicitly."""

    field = sanitize_sample_value(
        field_name="deleted_at",
        value=None,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value is None
    assert field.value_kind == SampleValueKind.NULL
    assert field.truncated is False


def test_boolean_is_not_misclassified_as_integer() -> None:
    """Handle Boolean before integer because bool subclasses int."""

    field = sanitize_sample_value(
        field_name="is_active",
        value=True,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value is True
    assert field.value_kind == SampleValueKind.BOOLEAN


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        -1,
        MAX_SAFE_JSON_INTEGER,
        -MAX_SAFE_JSON_INTEGER,
    ],
)
def test_json_safe_integer_is_preserved(
    value: int,
) -> None:
    """Preserve integers that JavaScript can represent exactly."""

    field = sanitize_sample_value(
        field_name="sequence_number",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == value
    assert field.value_kind == SampleValueKind.INTEGER
    assert field.truncated is False


@pytest.mark.parametrize(
    "value",
    [
        MAX_SAFE_JSON_INTEGER + 1,
        -(MAX_SAFE_JSON_INTEGER + 1),
    ],
)
def test_unsafe_integer_is_converted_to_lossless_text(
    value: int,
) -> None:
    """Prevent frontend precision loss for oversized integers."""

    field = sanitize_sample_value(
        field_name="large_identifier",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == str(value)
    assert field.value_kind == SampleValueKind.TEXT
    assert field.truncated is False


@pytest.mark.parametrize(
    "value",
    [
        0.0,
        1.5,
        -42.75,
    ],
)
def test_finite_float_is_preserved(
    value: float,
) -> None:
    """Preserve finite floating-point values."""

    field = sanitize_sample_value(
        field_name="score",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == value
    assert field.value_kind == SampleValueKind.FLOAT
    assert field.truncated is False


@pytest.mark.parametrize(
    "value",
    [
        math.nan,
        math.inf,
        -math.inf,
    ],
)
def test_nonfinite_float_is_masked_as_unsupported(
    value: float,
) -> None:
    """Prevent invalid JSON numeric values from escaping."""

    field = sanitize_sample_value(
        field_name="invalid_number",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "[UNSUPPORTED TYPE]"
    assert field.value_kind == SampleValueKind.UNSUPPORTED
    assert field.truncated is False


def test_short_text_is_preserved() -> None:
    """Preserve text that is within the configured bound."""

    field = sanitize_sample_value(
        field_name="display_name",
        value="Jane Doe",
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "Jane Doe"
    assert field.value_kind == SampleValueKind.TEXT
    assert field.truncated is False


def test_text_at_exact_limit_is_not_truncated() -> None:
    """Treat the maximum supported text length as valid."""

    value = "A" * MAX_SAMPLE_TEXT_LENGTH

    field = sanitize_sample_value(
        field_name="description",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == value
    assert field.value_kind == SampleValueKind.TEXT
    assert field.truncated is False


def test_long_text_is_truncated_without_appending_extra_content() -> None:
    """Bound text to the contract maximum and mark the truncation."""

    value = "A" * (MAX_SAMPLE_TEXT_LENGTH + 100)

    field = sanitize_sample_value(
        field_name="description",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "A" * MAX_SAMPLE_TEXT_LENGTH
    assert len(field.value) == MAX_SAMPLE_TEXT_LENGTH
    assert field.value_kind == SampleValueKind.TEXT
    assert field.truncated is True


@pytest.mark.parametrize(
    "value",
    [
        b"\x00\x01",
        bytearray(b"\x00\x01"),
        memoryview(b"\x00\x01"),
    ],
)
def test_binary_values_are_masked(
    value: bytes | bytearray | memoryview,
) -> None:
    """Never expose binary payload contents."""

    field = sanitize_sample_value(
        field_name="payload",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "[BINARY DATA]"
    assert field.value_kind == SampleValueKind.BINARY
    assert field.truncated is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            Decimal("1234567890.123400"),
            "1234567890.123400",
        ),
        (
            Decimal("-0.25"),
            "-0.25",
        ),
    ],
)
def test_finite_decimal_is_converted_to_exact_text(
    value: Decimal,
    expected: str,
) -> None:
    """Preserve decimal precision without converting through float."""

    field = sanitize_sample_value(
        field_name="amount",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == expected
    assert field.value_kind == SampleValueKind.TEXT
    assert field.truncated is False


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_nonfinite_decimal_is_masked_as_unsupported(
    value: Decimal,
) -> None:
    """Prevent nonfinite decimal values from entering API output."""

    field = sanitize_sample_value(
        field_name="amount",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "[UNSUPPORTED TYPE]"
    assert field.value_kind == SampleValueKind.UNSUPPORTED


def test_datetime_is_converted_to_iso_text() -> None:
    """Convert datetime values through the explicit ISO representation."""

    value = datetime(
        2026,
        7,
        20,
        5,
        30,
        45,
        tzinfo=UTC,
    )

    field = sanitize_sample_value(
        field_name="created_at",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "2026-07-20T05:30:45+00:00"
    assert field.value_kind == SampleValueKind.TEXT


def test_date_is_converted_to_iso_text() -> None:
    """Convert date values through the explicit ISO representation."""

    field = sanitize_sample_value(
        field_name="business_date",
        value=date(2026, 7, 20),
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "2026-07-20"
    assert field.value_kind == SampleValueKind.TEXT


def test_time_is_converted_to_iso_text() -> None:
    """Convert time values through the explicit ISO representation."""

    field = sanitize_sample_value(
        field_name="cutoff_time",
        value=time(
            5,
            30,
            45,
            tzinfo=UTC,
        ),
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "05:30:45+00:00"
    assert field.value_kind == SampleValueKind.TEXT


def test_uuid_is_converted_to_canonical_text() -> None:
    """Convert UUID values without exposing implementation objects."""

    value = UUID(
        "12345678-1234-5678-1234-567812345678",
    )

    field = sanitize_sample_value(
        field_name="correlation_id",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "12345678-1234-5678-1234-567812345678"
    assert field.value_kind == SampleValueKind.TEXT


@pytest.mark.parametrize(
    "value",
    [
        {
            "nested": "document",
        },
        [
            "array",
        ],
        ("tuple",),
        {
            "set",
        },
        object(),
    ],
)
def test_nested_and_unknown_values_are_masked(
    value: object,
) -> None:
    """Do not serialize nested containers or unknown driver objects."""

    field = sanitize_sample_value(
        field_name="unsupported_value",
        value=value,
        normalized_redaction_fields=frozenset(),
    )

    assert field.value == "[UNSUPPORTED TYPE]"
    assert field.value_kind == SampleValueKind.UNSUPPORTED
    assert field.truncated is False


@pytest.mark.parametrize(
    "field_name",
    [
        "",
        "A" * 129,
    ],
)
def test_invalid_field_name_is_rejected(
    field_name: str,
) -> None:
    """Reject malformed physical field names."""

    with pytest.raises(
        SamplingSanitizationError,
        match="Sample field names",
    ):
        sanitize_sample_value(
            field_name=field_name,
            value="value",
            normalized_redaction_fields=frozenset(),
        )


def test_non_text_field_name_is_defensively_rejected() -> None:
    """Reject malformed mappings whose keys are not strings."""

    invalid_field_name = cast(
        str,
        123,
    )

    with pytest.raises(
        SamplingSanitizationError,
        match="must be text",
    ):
        sanitize_sample_value(
            field_name=invalid_field_name,
            value="value",
            normalized_redaction_fields=frozenset(),
        )


def test_sample_row_preserves_driver_field_order() -> None:
    """Preserve physical field order without claiming row-order stability."""

    row: dict[str, object] = {
        "id": 1,
        "Email": "customer@example.com",
        "name": "Jane Doe",
        "payload": b"\x00",
    }

    sampled_row = sanitize_sample_row(
        row=row,
        normalized_redaction_fields=frozenset(
            {
                "email",
            },
        ),
    )

    assert [field.name for field in sampled_row.fields] == [
        "id",
        "Email",
        "name",
        "payload",
    ]

    assert [field.value_kind for field in sampled_row.fields] == [
        SampleValueKind.INTEGER,
        SampleValueKind.REDACTED,
        SampleValueKind.TEXT,
        SampleValueKind.BINARY,
    ]


def test_sample_row_with_no_fields_is_valid() -> None:
    """Permit an empty sanitized row without mutable containers."""

    sampled_row = sanitize_sample_row(
        row={},
        normalized_redaction_fields=frozenset(),
    )

    assert sampled_row.fields == ()
